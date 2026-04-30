from __future__ import annotations

import asyncio
import json
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from packages.runtime.model_input_audit import ModelInputAuditor


def test_model_input_auditor_records_totals_and_content(tmp_path: Path) -> None:
    path = tmp_path / "model_input_audit.jsonl"
    auditor = ModelInputAuditor(path=path, enabled=True, include_content=True)

    record = asyncio.run(
        auditor.audit(
            run_id="run-1",
            step=2,
            model_name="fake-model",
            messages=[
                SystemMessage(content="system prompt"),
                HumanMessage(content="查一下郑州天气"),
                AIMessage(
                    content="",
                    tool_calls=[{"id": "call-1", "name": "web_search", "args": {"query": "郑州天气"}}],
                ),
                ToolMessage(content="工具返回内容" * 10, tool_call_id="call-1"),
            ],
            tool_schemas=[{"type": "function", "function": {"name": "web_search"}}],
        )
    )

    assert record["totals"]["messages"] == 4
    assert record["totals"]["tool_schemas"] == 1
    assert record["totals"]["chars"] > 0
    assert record["by_role"]["tool"]["messages"] == 1
    assert any(item["role"] == "tool" and item["source_tool"] == "web_search" for item in record["top_messages"])

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    saved = json.loads(lines[0])
    assert saved["messages"][3]["content"].startswith("工具返回内容")
    assert saved["messages"][3]["tool_call_id"] == "call-1"
    assert saved["messages"][3]["source_tool"] == "web_search"
    assert saved["messages"][3]["source_args"] == {"query": "郑州天气"}


def test_model_input_auditor_can_omit_content(tmp_path: Path) -> None:
    path = tmp_path / "model_input_audit.jsonl"
    auditor = ModelInputAuditor(path=path, enabled=True, include_content=False)

    asyncio.run(
        auditor.audit(
            run_id="run-1",
            step=0,
            model_name="fake-model",
            messages=[HumanMessage(content="secret text")],
            tool_schemas=[],
        )
    )

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert "content" not in saved["messages"][0]
    assert saved["tool_schemas"] == []
