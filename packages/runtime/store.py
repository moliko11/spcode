from __future__ import annotations

import json
from pathlib import Path

from .config import AUDIT_LOG_PATH, CHECKPOINT_DIR, MEMORY_COMPACTION_DIR, MEMORY_TRANSCRIPTS_DIR, MEMORY_USERS_DIR, PLAN_RUNS_DIR, SESSION_DIR, WORKSPACE_DIR
from .models import AgentState, Phase, RunStatus, SessionMessage, StepRecord, ToolResult, to_jsonable


def ensure_dirs() -> None:
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    PLAN_RUNS_DIR.mkdir(parents=True, exist_ok=True)
    WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
    AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    MEMORY_USERS_DIR.mkdir(parents=True, exist_ok=True)
    MEMORY_TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    MEMORY_COMPACTION_DIR.mkdir(parents=True, exist_ok=True)


class FileSessionStore:
    """
    会话存储
    """
    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, session_id: str) -> Path:
        return self.root / f"{session_id}.json"

    async def load_messages(self, session_id: str) -> list[SessionMessage]:
        path = self._path(session_id)
        if not path.exists():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        return [SessionMessage(**item) for item in data]

    async def append_message(self, session_id: str, role: str, content: str) -> None:
        messages = await self.load_messages(session_id)
        messages.append(SessionMessage(role=role, content=content))
        self._path(session_id).write_text(
            json.dumps([to_jsonable(m) for m in messages], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


class FileCheckpointStore:
    """
    检查点存储
    """
    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, run_id: str) -> Path:
        return self.root / f"{run_id}.json"

    def save(self, state: AgentState) -> None:
        payload = to_jsonable(state)
        self._path(state.run_id).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def load(self, run_id: str) -> AgentState | None:
        path = self._path(run_id)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return AgentState(
            run_id=data["run_id"],
            user_id=data["user_id"],
            task=data["task"],
            session_id=data["session_id"],
            status=RunStatus(data["status"]),
            phase=Phase(data["phase"]),
            step=int(data["step"]),
            started_at=float(data["started_at"]),
            updated_at=float(data["updated_at"]),
            history=[StepRecord(**item) for item in data.get("history", [])],
            tool_results=[ToolResult(**item) for item in data.get("tool_results", [])],
            final_output=data.get("final_output"),
            failure_reason=data.get("failure_reason"),
            conversation=[SessionMessage(**item) for item in data.get("conversation", [])],
            runtime_messages=list(data.get("runtime_messages", [])),
            pending_tool_call=data.get("pending_tool_call"),
            pending_tool_result=data.get("pending_tool_result"),
            pending_human_request=data.get("pending_human_request"),
            metadata=dict(data.get("metadata", {})),
        )
