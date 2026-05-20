"""
packages/runtime/prompts — 分层提示词体系

对外暴露唯一入口 PromptSelector。
MessageBuilder 通过它将 base + specialist overlay 组装成最终 system prompt。
"""

from .selector import PromptSelector

__all__ = ["PromptSelector"]
