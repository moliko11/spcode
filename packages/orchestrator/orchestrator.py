from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from packages.planner.models import PlanStatus, StepStatus, TaskPlan, TaskStep
from packages.planner.planner import Planner
from packages.planner.store import PlanStore
from packages.runtime.agent_loop import AgentRuntime

from .executor import StepExecutor
from .models import PlanRun, StepRun, StepRunStatus
from .store import PlanRunStore

logger = logging.getLogger(__name__)


class Orchestrator:
    """
    W2 顺序执行器：
    1. 接收 goal → 调用 Planner 生成 TaskPlan
    2. 按 step 顺序（尊重 dependencies）串行调用 StepExecutor
    3. 任一 step 失败则停止，记录原因
    4. 返回 PlanRun 执行记录
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
        # 每次 orchestration 用独立 session，避免跨任务上下文污染
        self._session_id = session_id or f"orchestrate-{uuid.uuid4()}"

    async def run(self, goal: str, context: str = "") -> PlanRun:
        """生成计划并串行执行所有步骤，返回 PlanRun。"""
        plan = await self._planner.create_plan(goal, context=context)
        plan.status = PlanStatus.RUNNING
        plan.metadata["execution_order"] = [step.step_id for step in self._execution_order(plan)]
        self._plan_store.save(plan)
        logger.info("Orchestrator plan_id=%s steps=%d", plan.plan_id, len(plan.steps))

        plan_run = PlanRun(
            plan_id=plan.plan_id,
            goal=goal,
            status="running",
            session_id=self._session_id,
            started_at=time.time(),
        )
        self._persist(plan, plan_run)
        return await self._execute_from_index(plan, plan_run, start_index=0)

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

        ordered_steps = self._ordered_steps(plan)
        step = ordered_steps[plan_run.current_step_index]
        step_run = self._find_step_run(plan_run, step.step_id)
        if step_run is None:
            raise ValueError(f"step_run not found for step: {step.step_id}")

        executor = StepExecutor(
            runtime=self._runtime,
            user_id=self._user_id,
            session_id=plan_run.session_id,
        )
        await executor.resume(
            step_run=step_run,
            approved=approved,
            approved_by=approved_by,
            edited_arguments=edited_arguments,
        )
        self._sync_step_from_run(step, step_run)
        if step_run.status == StepRunStatus.WAITING_HUMAN:
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

        plan_run.current_step_index += 1
        plan_run.pending_step_id = None
        plan_run.status = "running"
        plan.status = PlanStatus.RUNNING
        self._persist(plan, plan_run)
        return await self._execute_from_index(plan, plan_run, start_index=plan_run.current_step_index)

    # ------------------------------------------------------------------

    def _execution_order(self, plan: TaskPlan) -> list[TaskStep]:
        """
        简单拓扑排序：按 step 在列表中的原始顺序输出，
        保证依赖在前（W2 阶段 LLM 输出的步骤本身就是有序的，此处仅做基础校验）。
        """
        step_map = {s.step_id: s for s in plan.steps}
        visited: set[str] = set()
        result: list[TaskStep] = []

        def visit(step_id: str) -> None:
            if step_id in visited:
                return
            visited.add(step_id)
            step = step_map.get(step_id)
            if step is None:
                return
            for dep in step.dependencies:
                visit(dep)
            result.append(step)

        for step in plan.steps:
            visit(step.step_id)
        return result

    async def _execute_from_index(self, plan: TaskPlan, plan_run: PlanRun, start_index: int) -> PlanRun:
        executor = StepExecutor(
            runtime=self._runtime,
            user_id=self._user_id,
            session_id=plan_run.session_id,
        )
        ordered_steps = self._ordered_steps(plan)
        completed_ids = {step.step_id for step in plan.steps if step.status == StepStatus.COMPLETED}
        failed = any(step.status == StepStatus.FAILED for step in plan.steps)

        for index in range(start_index, len(ordered_steps)):
            plan_run.current_step_index = index
            step = ordered_steps[index]
            if not all(dep in completed_ids for dep in step.dependencies):
                step_run = StepRun(
                    step_id=step.step_id,
                    title=step.title,
                    status=StepRunStatus.SKIPPED,
                    error="dependency not satisfied",
                )
                plan_run.step_runs.append(step_run)
                step.status = StepStatus.SKIPPED
                step.error = "dependency not satisfied"
                continue
            if failed:
                step_run = StepRun(
                    step_id=step.step_id,
                    title=step.title,
                    status=StepRunStatus.SKIPPED,
                    error="previous step failed",
                )
                plan_run.step_runs.append(step_run)
                step.status = StepStatus.SKIPPED
                step.error = "previous step failed"
                continue

            step_run = StepRun(step_id=step.step_id, title=step.title)
            plan_run.step_runs.append(step_run)
            await executor.run(step, step_run)
            self._sync_step_from_run(step, step_run)
            self._persist(plan, plan_run)

            if step_run.status == StepRunStatus.COMPLETED:
                completed_ids.add(step.step_id)
                plan_run.current_step_index = index + 1
                continue
            if step_run.status == StepRunStatus.WAITING_HUMAN:
                plan.status = PlanStatus.WAITING_HUMAN
                plan_run.status = "waiting_human"
                plan_run.pending_step_id = step.step_id
                self._persist(plan, plan_run)
                return self._finalize_plan_run(plan_run)
            failed = True
            plan.status = PlanStatus.FAILED
            plan_run.status = "failed"
            plan_run.pending_step_id = None

        if not failed:
            plan.status = PlanStatus.COMPLETED
            plan_run.status = "completed"
            plan_run.completed = True
        plan_run.pending_step_id = None
        self._persist(plan, plan_run)
        return self._finalize_plan_run(plan_run)

    def _ordered_steps(self, plan: TaskPlan) -> list[TaskStep]:
        execution_order = plan.metadata.get("execution_order")
        if isinstance(execution_order, list) and execution_order:
            step_map = {step.step_id: step for step in plan.steps}
            return [step_map[step_id] for step_id in execution_order if step_id in step_map]
        ordered = self._execution_order(plan)
        plan.metadata["execution_order"] = [step.step_id for step in ordered]
        return ordered

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
