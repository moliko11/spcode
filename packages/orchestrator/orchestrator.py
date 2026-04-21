from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

from packages.planner.models import PlanStatus, StepStatus, TaskPlan, TaskStep
from packages.planner.planner import Planner
from packages.planner.scheduler import Scheduler
from packages.planner.store import PlanStore
from packages.runtime.agent_loop import AgentRuntime

from .executor import StepExecutor
from .models import PlanRun, StepRun, StepRunStatus
from .store import PlanRunStore

logger = logging.getLogger(__name__)


class Orchestrator:
    """
    W4 Wave-based 并行执行器：
    1. 接收 goal → 调用 Planner 生成 TaskPlan（带 dependencies DAG）
    2. 每一轮（wave）用 Scheduler 找出所有依赖已满足的就绪步骤
    3. 用 asyncio.gather() 并行执行同一波的所有步骤
       - 每步使用独立 session，避免上下文竞争
    4. 循环直到所有步骤完成/失败/跳过
    5. 支持 WAITING_HUMAN → resume() 恢复继续下一 wave
    """

    def __init__(
        self,
        runtime: AgentRuntime,
        planner: Planner,
        plan_store: PlanStore,
        plan_run_store: PlanRunStore,
        user_id: str = "demo-user",
        session_id: str | None = None,
    ) -> None:
        self._runtime = runtime
        self._planner = planner
        self._plan_store = plan_store
        self._plan_run_store = plan_run_store
        self._user_id = user_id
        self._scheduler = Scheduler()
        # plan_run 级别的 session 前缀；每个 step 用 {prefix}-{step_id} 独立隔离
        self._session_prefix = session_id or f"orchestrate-{uuid.uuid4()}"

    async def run(self, goal: str, context: str = "") -> PlanRun:
        """生成计划并以 wave 方式并行执行所有步骤，返回 PlanRun。"""
        plan = await self._planner.create_plan(goal, context=context)
        plan.status = PlanStatus.RUNNING
        self._plan_store.save(plan)
        logger.info(
            "Orchestrator plan_id=%s steps=%d goal=%r",
            plan.plan_id, len(plan.steps), goal,
        )

        plan_run = PlanRun(
            plan_id=plan.plan_id,
            goal=goal,
            status="running",
            session_id=self._session_prefix,
            started_at=time.time(),
        )
        self._persist(plan, plan_run)
        return await self._execute_waves(plan, plan_run)

    async def resume(
        self,
        plan_run_id: str,
        approved: bool,
        approved_by: str = "human",
        edited_arguments: dict[str, Any] | None = None,
    ) -> PlanRun:
        """恢复一个因人工审批暂停的计划运行。"""
        plan_run = self._plan_run_store.load(plan_run_id)
        if plan_run is None:
            raise ValueError(f"plan_run not found: {plan_run_id}")
        plan = self._plan_store.load(plan_run.plan_id)
        if plan is None:
            raise ValueError(f"plan not found: {plan_run.plan_id}")
        if plan_run.status != "waiting_human" or plan_run.pending_step_id is None:
            raise ValueError("plan_run is not waiting for human approval")

        # 通过 pending_step_id 直接定位等待步骤（支持并行执行场景）
        step = next(
            (s for s in plan.steps if s.step_id == plan_run.pending_step_id), None
        )
        if step is None:
            raise ValueError(f"step not found: {plan_run.pending_step_id}")
        step_run = self._find_step_run(plan_run, step.step_id)
        if step_run is None:
            raise ValueError(f"step_run not found for step: {step.step_id}")

        # 使用步骤记录的 session_id（并行时每步独立），兜底用 plan_run.session_id
        step_session_id = step_run.step_session_id or plan_run.session_id

        executor = StepExecutor(
            runtime=self._runtime,
            user_id=self._user_id,
            session_id=step_session_id,
        )
        await executor.resume(
            step_run=step_run,
            approved=approved,
            approved_by=approved_by,
            edited_arguments=edited_arguments,
        )
        self._sync_step_from_run(step, step_run)

        if step_run.status == StepRunStatus.WAITING_HUMAN:
            # 同一步骤内又遇到新的审批请求
            plan.status = PlanStatus.WAITING_HUMAN
            plan_run.status = "waiting_human"
            plan_run.pending_step_id = step.step_id
            self._persist(plan, plan_run)
            return self._finalize_plan_run(plan_run)

        if step_run.status == StepRunStatus.FAILED:
            plan.status = PlanStatus.FAILED
            plan_run.status = "failed"
            plan_run.pending_step_id = None
            self._persist(plan, plan_run)
            return self._finalize_plan_run(plan_run)

        # 步骤已完成，继续下一 wave
        plan_run.pending_step_id = None
        plan_run.status = "running"
        plan.status = PlanStatus.RUNNING
        self._persist(plan, plan_run)
        return await self._execute_waves(plan, plan_run)

    async def recover(self, plan_run_id: str) -> PlanRun:
        """
        跨进程恢复：从持久化存储加载未完成的 PlanRun，重置崩溃中的 RUNNING 步骤，
        继续 wave-based 执行。适用于进程被强制终止后重新启动的场景。
        """
        plan_run = self._plan_run_store.load(plan_run_id)
        if plan_run is None:
            raise ValueError(f"plan_run not found: {plan_run_id}")
        plan = self._plan_store.load(plan_run.plan_id)
        if plan is None:
            raise ValueError(f"plan not found: {plan_run.plan_id}")

        if plan_run.status in ("completed", "failed"):
            return self._finalize_plan_run(plan_run)

        # 恢复 session 前缀，确保步骤 session ID 与原来一致
        self._session_prefix = plan_run.session_id

        # 将崩溃中（RUNNING）的步骤重置为 PENDING，让调度器重新调度
        crashed_step_ids = {
            s.step_id for s in plan.steps if s.status == StepStatus.RUNNING
        }
        for step in plan.steps:
            if step.step_id in crashed_step_ids:
                step.status = StepStatus.PENDING
        # 移除对应的不完整 step_runs，wave 循环会重新创建它们
        plan_run.step_runs = [
            r for r in plan_run.step_runs if r.step_id not in crashed_step_ids
        ]

        plan_run.status = "running"
        plan_run.pending_step_id = None
        plan.status = PlanStatus.RUNNING
        self._persist(plan, plan_run)
        logger.info(
            "Orchestrator recover plan_run_id=%s crashed_steps=%s",
            plan_run_id, list(crashed_step_ids),
        )
        return await self._execute_waves(plan, plan_run)

    # ------------------------------------------------------------------
    # Wave-based 并行执行核心
    # ------------------------------------------------------------------

    async def _execute_waves(self, plan: TaskPlan, plan_run: PlanRun) -> PlanRun:
        """
        Wave-based 并行执行循环：
        每轮找出所有依赖已满足的 PENDING 步骤，用 asyncio.gather() 并行运行，
        直到没有更多就绪步骤为止。
        """
        wave_index = 0

        while True:
            completed_ids = {
                s.step_id for s in plan.steps if s.status == StepStatus.COMPLETED
            }
            has_failed = any(
                s.status == StepStatus.FAILED for s in plan.steps
            )

            # 检测死锁：有 PENDING 步骤但没有任何步骤可就绪
            if self._scheduler.is_stuck(plan, completed_ids):
                logger.warning(
                    "Orchestrator detected stuck plan_id=%s; skipping remaining PENDING steps",
                    plan.plan_id,
                )
                for step in plan.steps:
                    if step.status == StepStatus.PENDING:
                        step_run = StepRun(
                            step_id=step.step_id,
                            title=step.title,
                            status=StepRunStatus.SKIPPED,
                            error="stuck: dependency cycle or all deps failed",
                        )
                        plan_run.step_runs.append(step_run)
                        step.status = StepStatus.SKIPPED
                        step.error = step_run.error
                break

            ready_steps = self._scheduler.get_ready_steps(plan, completed_ids)

            if not ready_steps:
                break  # 没有可执行步骤，结束

            if has_failed:
                # 有步骤失败，跳过剩余就绪步骤
                for step in ready_steps:
                    step_run = StepRun(
                        step_id=step.step_id,
                        title=step.title,
                        status=StepRunStatus.SKIPPED,
                        error="previous step failed",
                    )
                    plan_run.step_runs.append(step_run)
                    step.status = StepStatus.SKIPPED
                    step.error = step_run.error
                self._persist(plan, plan_run)
                break

            logger.info(
                "Wave %d: running %d step(s) in parallel: %s",
                wave_index,
                len(ready_steps),
                [s.step_id for s in ready_steps],
            )

            # 为本 wave 的每个步骤创建独立 session 和 StepRun
            wave_pairs: list[tuple[TaskStep, StepRun]] = []
            tasks = []
            for step in ready_steps:
                step_session_id = f"{self._session_prefix}-{step.step_id}"
                step_run = StepRun(
                    step_id=step.step_id,
                    title=step.title,
                    step_session_id=step_session_id,
                )
                plan_run.step_runs.append(step_run)
                step.status = StepStatus.RUNNING
                executor = StepExecutor(
                    runtime=self._runtime,
                    user_id=self._user_id,
                    session_id=step_session_id,
                )
                tasks.append(executor.run(step, step_run))
                wave_pairs.append((step, step_run))

            self._persist(plan, plan_run)

            # 并行执行本 wave
            await asyncio.gather(*tasks)

            # 处理本 wave 结果
            first_waiting: tuple[TaskStep, StepRun] | None = None
            for step, step_run in wave_pairs:
                self._sync_step_from_run(step, step_run)
                if step_run.status == StepRunStatus.WAITING_HUMAN and first_waiting is None:
                    first_waiting = (step, step_run)

            self._persist(plan, plan_run)

            if first_waiting is not None:
                wait_step, wait_run = first_waiting
                plan.status = PlanStatus.WAITING_HUMAN
                plan_run.status = "waiting_human"
                plan_run.pending_step_id = wait_step.step_id
                self._persist(plan, plan_run)
                return self._finalize_plan_run(plan_run)

            wave_index += 1

        # 确定最终状态
        has_failed = any(s.status == StepStatus.FAILED for s in plan.steps)
        if has_failed:
            plan.status = PlanStatus.FAILED
            plan_run.status = "failed"
        else:
            plan.status = PlanStatus.COMPLETED
            plan_run.status = "completed"
            plan_run.completed = True
        plan_run.pending_step_id = None
        self._persist(plan, plan_run)
        return self._finalize_plan_run(plan_run)

    def _find_step_run(self, plan_run: PlanRun, step_id: str) -> StepRun | None:
        for step_run in plan_run.step_runs:
            if step_run.step_id == step_id:
                return step_run
        return None

    def _sync_step_from_run(self, step: TaskStep, step_run: StepRun) -> None:
        if step_run.status == StepRunStatus.COMPLETED:
            step.status = StepStatus.COMPLETED
            step.output = step_run.output
            step.error = None
            return
        if step_run.status == StepRunStatus.WAITING_HUMAN:
            step.status = StepStatus.WAITING_HUMAN
            step.error = None
            step.metadata["pending_human_request"] = step_run.pending_human_request or {}
            return
        if step_run.status == StepRunStatus.SKIPPED:
            step.status = StepStatus.SKIPPED
            step.error = step_run.error
            return
        step.status = StepStatus.FAILED
        step.error = step_run.error

    def _persist(self, plan: TaskPlan, plan_run: PlanRun) -> None:
        plan.updated_at = time.time()
        self._plan_store.save(plan)
        self._plan_run_store.save(plan_run)

    def _finalize_plan_run(self, plan_run: PlanRun) -> PlanRun:
        if plan_run.status in {"completed", "failed"}:
            plan_run.finished_at = time.time()
        plan_run.final_output = self._summarize(plan_run)
        self._plan_run_store.save(plan_run)
        return plan_run

    def _summarize(self, plan_run: PlanRun) -> str:
        lines = [f"goal: {plan_run.goal}", ""]
        for sr in plan_run.step_runs:
            icon = "✓" if sr.status == StepRunStatus.COMPLETED else ("…" if sr.status == StepRunStatus.WAITING_HUMAN else ("✗" if sr.status == StepRunStatus.FAILED else "⊘"))
            lines.append(f"  {icon} [{sr.step_id}] {sr.title}")
            if sr.output:
                preview = sr.output[:200].replace("\n", " ")
                lines.append(f"      → {preview}")
            if sr.error:
                lines.append(f"      ✗ {sr.error}")
            if sr.pending_human_request:
                lines.append(f"      ? waiting approval for {sr.pending_human_request.get('context', {}).get('tool_name', 'tool')}")
        return "\n".join(lines)
