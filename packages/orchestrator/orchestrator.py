from __future__ import annotations

import logging
import time
import uuid

from packages.planner.models import StepStatus, TaskPlan, TaskStep
from packages.planner.planner import Planner
from packages.planner.store import PlanStore
from packages.runtime.agent_loop import AgentRuntime

from .executor import StepExecutor
from .models import PlanRun, StepRun, StepRunStatus

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
        user_id: str = "demo-user",
        session_id: str | None = None,
    ) -> None:
        self._runtime = runtime
        self._planner = planner
        self._plan_store = plan_store
        self._user_id = user_id
        # 每次 orchestration 用独立 session，避免跨任务上下文污染
        self._session_id = session_id or f"orchestrate-{uuid.uuid4()}"

    async def run(self, goal: str, context: str = "") -> PlanRun:
        """生成计划并串行执行所有步骤，返回 PlanRun。"""
        # 1. 生成计划
        plan = await self._planner.create_plan(goal, context=context)
        self._plan_store.save(plan)
        logger.info("Orchestrator plan_id=%s steps=%d", plan.plan_id, len(plan.steps))

        plan_run = PlanRun(
            plan_id=plan.plan_id,
            goal=goal,
            started_at=time.time(),
        )

        executor = StepExecutor(
            runtime=self._runtime,
            user_id=self._user_id,
            session_id=self._session_id,
        )

        completed_ids: set[str] = set()
        failed = False

        for step in self._execution_order(plan):
            # 检查依赖是否全部完成
            if not all(dep in completed_ids for dep in step.dependencies):
                step_run = StepRun(
                    step_id=step.step_id,
                    title=step.title,
                    status=StepRunStatus.SKIPPED,
                    error="dependency not satisfied",
                )
                plan_run.step_runs.append(step_run)
                logger.warning("Orchestrator step=%s skipped (dep not met)", step.step_id)
                continue

            if failed:
                step_run = StepRun(
                    step_id=step.step_id,
                    title=step.title,
                    status=StepRunStatus.SKIPPED,
                    error="previous step failed",
                )
                plan_run.step_runs.append(step_run)
                continue

            step_run = StepRun(step_id=step.step_id, title=step.title)
            plan_run.step_runs.append(step_run)

            await executor.run(step, step_run)

            if step_run.status == StepRunStatus.COMPLETED:
                completed_ids.add(step.step_id)
                # 同步回 TaskStep 状态（保存到 plan store）
                step.status = StepStatus.COMPLETED
                step.output = step_run.output
            else:
                failed = True
                step.status = StepStatus.FAILED
                step.error = step_run.error

        # 保存最终 plan 状态
        self._plan_store.save(plan)

        plan_run.finished_at = time.time()
        plan_run.completed = not failed
        plan_run.final_output = self._summarize(plan_run)
        return plan_run

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

    def _summarize(self, plan_run: PlanRun) -> str:
        lines = [f"goal: {plan_run.goal}", ""]
        for sr in plan_run.step_runs:
            icon = "✓" if sr.status == StepRunStatus.COMPLETED else ("✗" if sr.status == StepRunStatus.FAILED else "⊘")
            lines.append(f"  {icon} [{sr.step_id}] {sr.title}")
            if sr.output:
                # 只截取前 200 字，避免输出过长
                preview = sr.output[:200].replace("\n", " ")
                lines.append(f"      → {preview}")
            if sr.error:
                lines.append(f"      ✗ {sr.error}")
        return "\n".join(lines)
