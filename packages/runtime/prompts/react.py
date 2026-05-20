"""
react.py — ReAct (Thought → Action → Observation) 格式 overlay

当 autonomy_policy.mode == "agentic" 时追加，引导模型显式地展示推理链。
"""

from __future__ import annotations


REACT_OVERLAY = """\
Reasoning format (ReAct):
For each step in agentic mode, structure your internal reasoning as:
  Thought: <brief analysis of current state and what to do next>
  Action: <tool call or decision>
  Observation: <summarize what was learned from the tool result>

Rules:
- Do not expose the full Thought in your final user-facing reply; it is internal scaffolding.
- Each Observation must inform the next Thought — never ignore tool output.
- If an Observation shows failure, reflect before retrying (consider alternative approaches).
- When all sub-tasks are done, synthesize Observations into a concise final answer.
"""


def build_react_overlay() -> str:
    return REACT_OVERLAY
