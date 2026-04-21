from __future__ import annotations

import logging
import time

from packages.planner.models import TaskStep
from packages.runtime.agent_loop import AgentRuntime
from packages.runtime.models import RunStatus

from .models import StepRun, StepRunStatus

logger = logging.getLogger(__name__)


class StepExecutor:
    """
    负责执行单个 TaskStep：
    将 step 描述拼成 prompt，调用 AgentRuntime.chat()，
    把结果写回 StepRun。
    """

    def __init__(self, runtime: AgentRuntime, user_id: str, session_id: str) -> None:
        self._runtime = runtime
        self._user_id = user_id
        self._session_id = session_id

    async def run(self, step: TaskStep, step_run: StepRun) -> StepRun:
        """执行一个步骤，原地更新 step_run 并返回。"""
        step_run.status = StepRunStatus.RUNNING
        step_run.started_at = time.time()

        prompt = self._build_prompt(step)
        logger.info("StepExecutor step=%s title=%r", step.step_id, step.title)

        try:
            state = await self._runtime.chat(
                user_id=self._user_id,
                session_id=self._session_id,
                message=prompt,
            )
            step_run.run_id = state.run_id
            if state.status == RunStatus.COMPLETED:
                step_run.status = StepRunStatus.COMPLETED
                step_run.output = state.final_output or ""
            else:
                step_run.status = StepRunStatus.FAILED
                step_run.error = state.failure_reason or f"status={state.status.value}"
        except Exception as exc:
            logger.exception("StepExecutor step=%s failed", step.step_id)
            step_run.status = StepRunStatus.FAILED
            step_run.error = str(exc)
        finally:
            step_run.finished_at = time.time()

        return step_run

    # ------------------------------------------------------------------

    def _build_prompt(self, step: TaskStep) -> str:
        parts = [f"# 任务步骤：{step.title}", step.description]
        if step.acceptance_criteria:
            criteria = "\n".join(f"- {c}" for c in step.acceptance_criteria)
            parts.append(f"\n## 验收标准\n{criteria}")
        if step.suggested_tools:
            parts.append(f"\n## 建议工具\n{', '.join(step.suggested_tools)}")
        return "\n\n".join(parts)
