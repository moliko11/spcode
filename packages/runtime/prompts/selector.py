"""
selector.py — PromptSelector

根据 AgentState.metadata 中的任务类型 / 运行模式，
将 base + specialist overlays 组装成最终 system prompt。

用法（在 MessageBuilder 内）：
    selector = PromptSelector(workspace_root=..., skill_tool=..., ...)
    prompt = selector.assemble(state, datetime_dict={...})
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .base import build_base_prompt
from .code_agent import build_code_agent_overlay
from .planner_mode import build_planner_mode_overlay
from .react import build_react_overlay
from .reviewer_mode import build_reviewer_mode_overlay

if TYPE_CHECKING:
    from packages.runtime.models import AgentState
    from packages.tools.SkillTool.tool import SkillTool


class PromptSelector:
    """
    组装 system prompt。

    规则：
    - 始终追加 base
    - plan_mode.active == True  → 追加 planner_mode_overlay
    - task_type == "code"       → 追加 code_agent_overlay
    - task_type == "review"     → 追加 reviewer_mode_overlay
    - autonomy mode == "agentic"→ 追加 react_overlay
    - 始终追加 skill_listing / recall_text / autonomy_policy_text（如有）
    """

    def __init__(
        self,
        *,
        workspace_root: str,
        skill_tool: "SkillTool | None" = None,
    ) -> None:
        self.workspace_root = workspace_root
        self.skill_tool = skill_tool

    def assemble(
        self,
        state: "AgentState",
        *,
        datetime_dict: dict[str, str],
        loaded_tools: str,
        dynamic_tools: str,
        autonomy_text: str = "",
    ) -> str:
        sections: list[str] = []

        # ── 1. 基础提示词（全局共享）──────────────────────────────────────
        sections.append(
            build_base_prompt(
                workspace_root=self.workspace_root,
                date_str=datetime_dict.get("date", ""),
                datetime_str=datetime_dict.get("datetime", ""),
                timezone=datetime_dict.get("timezone", ""),
                loaded_tools=loaded_tools,
                dynamic_tools=dynamic_tools,
            )
        )

        # ── 2. 专项 overlay 选择 ───────────────────────────────────────────
        plan_mode = state.metadata.get("plan_mode")
        is_plan_mode = isinstance(plan_mode, dict) and plan_mode.get("active")

        task_type = str(state.metadata.get("task_type") or "").lower()
        autonomy_policy = state.metadata.get("autonomy_policy")
        autonomy_mode = str((autonomy_policy or {}).get("mode") or "").lower() if isinstance(autonomy_policy, dict) else ""

        if is_plan_mode:
            sections.append(build_planner_mode_overlay())
        elif task_type == "review":
            sections.append(build_reviewer_mode_overlay())
        elif task_type == "code":
            sections.append(build_code_agent_overlay())

        if autonomy_mode == "agentic":
            sections.append(build_react_overlay())

        # ── 3. Skill listing ──────────────────────────────────────────────
        if self.skill_tool is not None:
            listing = self.skill_tool.build_skill_listing()
            if listing:
                sections.append(listing)

        # ── 4. Recall (long-term memory injection) ────────────────────────
        recall_text = state.metadata.get("recall_text")
        if recall_text:
            sections.append(recall_text)

        # ── 5. Autonomy policy ────────────────────────────────────────────
        if autonomy_text:
            sections.append(autonomy_text)

        # ── 6. Reflection note (failure analysis / loop detection) ────────
        reflection_text = state.metadata.get("_reflection")
        if reflection_text:
            sections.append(f"[System Observation]\n{reflection_text}")

        return "\n\n".join(s.strip() for s in sections if s.strip()) + "\n"
