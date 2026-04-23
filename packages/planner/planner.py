from __future__ import annotations

import json
import logging
import time

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from .models import PlanStatus, TaskPlan, TaskStep
from .prompts import PLAN_SYSTEM_PROMPT, build_plan_user_prompt

logger = logging.getLogger(__name__)


class PlannerError(Exception):
    """Planner 无法生成有效计划时抛出"""  


class Planner:
    """
    任务规划器：接收目标（goal）和可选背景（context），
    调用 LLM 生成结构化 TaskPlan。

    W1 阶段只负责生成计划，不执行任何工具。
    """

    def __init__(self, llm: BaseChatModel) -> None:
        self._llm = llm

    async def create_plan(self, goal: str, context: str = "") -> TaskPlan:
        """
        调用 LLM 将 goal 拆分为带依赖关系和验收标准的 TaskPlan。

        :param goal: 用户的目标描述。
        :param context: 可选背景（记忆摘要、项目说明等）。
        :raises PlannerError: LLM 返回无法解析的内容时。
        """
        messages = [
            SystemMessage(content=PLAN_SYSTEM_PROMPT),
            HumanMessage(content=build_plan_user_prompt(goal, context)),
        ]

        logger.debug("Planner.create_plan goal=%r", goal)
        response = await self._llm.ainvoke(messages)
        raw: str = response.content if hasattr(response, "content") else str(response)

        plan = self._parse_response(goal, context, raw)
        return plan

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    def _parse_response(self, goal: str, context: str, raw: str) -> TaskPlan:
        """解析 LLM 返回的 JSON 字符串，构造 TaskPlan。"""
        # 有时 LLM 会用 ```json ... ``` 包裹，尝试剥离
        text = raw.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            # 去掉首行（```json 或 ```）和末行（```）
            inner = lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
            text = "\n".join(inner).strip()

        try:
            data: dict = json.loads(text)
        except json.JSONDecodeError as exc:
            raise PlannerError(
                f"LLM 返回了无法解析的 JSON: {exc}\nraw={raw!r}"
            ) from exc

        steps: list[TaskStep] = []
        for raw_step in data.get("steps", []):
            step = TaskStep(
                step_id=raw_step.get("step_id", f"step_{len(steps) + 1}"),
                title=raw_step.get("title", ""),
                description=raw_step.get("description", ""),
                dependencies=raw_step.get("dependencies", []),
                acceptance_criteria=raw_step.get("acceptance_criteria", []),
                suggested_tools=raw_step.get("suggested_tools", []),
            )
            steps.append(step)

        if not steps:
            raise PlannerError("LLM 返回的计划中没有任何步骤")

        now = time.time()
        plan = TaskPlan(
            goal=goal,
            context=context,
            steps=steps,
            status=PlanStatus.DRAFT,
            created_at=now,
            updated_at=now,
        )
        return plan
