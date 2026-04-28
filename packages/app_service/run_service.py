"""
RunManager — 后台任务管理器

Web API 需要"立即返回 run_id，任务后台执行"的能力。
RunManager 不修改 AgentRuntime / Orchestrator 本身，只在外围用
asyncio.Task 包装它们，并维护运行状态 + EventBus 订阅。

设计原则：
- AgentRuntime 保持纯净，不感知 RunManager 的存在
- cancel() 只设置取消事件，等 runtime 在下个步骤自然退出
- subscribe() 先 replay ring buffer，再推新事件（断线续传安全）
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncIterator

from packages.runtime.models import AgentEvent, AgentState, EventType, RunStatus, to_jsonable


class TaskKind(str, Enum):
    CHAT = "chat"
    ORCHESTRATE = "orchestrate"


@dataclass
class RunRecord:
    """内存中对一个后台任务的记录"""
    run_id: str
    kind: TaskKind
    goal: str
    user_id: str
    session_id: str
    created_at: float = field(default_factory=time.time)
    status: str = "running"            # running | completed | failed | cancelled
    final_output: str | None = None
    failure_reason: str | None = None
    cost_summary: dict[str, Any] = field(default_factory=dict)
    _task: asyncio.Task | None = field(default=None, repr=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "kind": self.kind.value,
            "goal": self.goal,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "created_at": self.created_at,
            "status": self.status,
            "final_output": self.final_output,
            "failure_reason": self.failure_reason,
            "cost_summary": self.cost_summary,
        }


class _RuntimeEventForwarder:
    """把 AgentRuntime 内部事件转发到 RunManager 的外部 run 流。"""

    def __init__(self, manager: "RunManager", record: RunRecord) -> None:
        self.manager = manager
        self.record = record
        self.runtime_run_id: str | None = None

    async def handle(self, event: AgentEvent) -> None:
        if self.runtime_run_id is None:
            if event.event_type != EventType.RUN_STARTED:
                return
            if event.payload.get("session_id") != self.record.session_id:
                return
            if event.payload.get("task") != self.record.goal:
                return
            self.runtime_run_id = event.run_id
        elif event.run_id != self.runtime_run_id:
            return

        self.manager._push_event(
            self.record.run_id,
            {
                "kind": event.event_kind or event.event_type.value,
                "event_kind": event.event_kind or event.event_type.value,
                "event_type": event.event_type.value,
                "run_id": self.record.run_id,
                "runtime_run_id": event.run_id,
                "step": event.step,
                "ts": event.ts,
                "payload": to_jsonable(event.payload),
            },
        )


class RunManager:
    """
    后台任务注册表。

    用法（在 FastAPI lifespan 或测试里初始化一个单例）::

        manager = RunManager()

        # 启动 chat run
        run_id = await manager.start_chat(
            chat_service=svc,
            user_id="demo",
            session_id="s1",
            message="hello",
        )

        # 订阅事件（SSE 使用）
        async for event in manager.subscribe(run_id):
            yield event

        # 取消
        manager.cancel(run_id)
    """

    def __init__(self) -> None:
        self._records: dict[str, RunRecord] = {}
        # run_id → list of asyncio.Queue[dict]
        # 每个 SSE 连接对应一个 Queue
        self._queues: dict[str, list[asyncio.Queue]] = {}
        # ring buffer: run_id → list of events（有序）
        self._ring: dict[str, list[dict[str, Any]]] = {}
        self._ring_max = 2000
        # cancel events
        self._cancel_events: dict[str, asyncio.Event] = {}

    # ── 启动 ──────────────────────────────────────────────────────────────

    async def start_chat(
        self,
        chat_service: Any,
        user_id: str,
        session_id: str,
        message: str,
    ) -> str:
        """在后台启动 chat run，立即返回 run_id。"""
        run_id = str(uuid.uuid4())
        session_id = session_id or f"session-{run_id[:8]}"
        cancel_ev = asyncio.Event()
        self._cancel_events[run_id] = cancel_ev

        record = RunRecord(
            run_id=run_id,
            kind=TaskKind.CHAT,
            goal=message,
            user_id=user_id,
            session_id=session_id,
        )
        self._records[run_id] = record
        self._ring[run_id] = []
        self._queues[run_id] = []

        task = asyncio.create_task(
            self._run_chat(chat_service, record, cancel_ev),
            name=f"chat-{run_id[:8]}",
        )
        record._task = task
        return run_id

    async def start_orchestrate(
        self,
        orchestrate_service: Any,
        user_id: str,
        goal: str,
        context: str = "",
    ) -> str:
        """在后台启动 orchestrate run，立即返回 run_id。"""
        run_id = str(uuid.uuid4())
        cancel_ev = asyncio.Event()
        self._cancel_events[run_id] = cancel_ev

        record = RunRecord(
            run_id=run_id,
            kind=TaskKind.ORCHESTRATE,
            goal=goal,
            user_id=user_id,
            session_id=f"orch-{run_id[:8]}",
        )
        self._records[run_id] = record
        self._ring[run_id] = []
        self._queues[run_id] = []

        task = asyncio.create_task(
            self._run_orchestrate(orchestrate_service, record, cancel_ev),
            name=f"orch-{run_id[:8]}",
        )
        record._task = task
        return run_id

    # ── 控制 ──────────────────────────────────────────────────────────────

    def cancel(self, run_id: str) -> bool:
        """发送取消信号，runtime 在下个步骤自然退出。"""
        ev = self._cancel_events.get(run_id)
        if ev and not ev.is_set():
            ev.set()
            return True
        return False

    async def wait(self, run_id: str) -> RunRecord | None:
        record = self._records.get(run_id)
        if record is None:
            return None
        task = record._task
        if task and not task.done():
            await task
        return record

    # ── 查询 ──────────────────────────────────────────────────────────────

    def get(self, run_id: str) -> RunRecord | None:
        return self._records.get(run_id)

    def list_runs(
        self,
        limit: int = 20,
        status_filter: str | None = None,
    ) -> list[dict[str, Any]]:
        records = sorted(self._records.values(), key=lambda r: r.created_at, reverse=True)
        if status_filter:
            records = [r for r in records if r.status == status_filter]
        return [r.to_dict() for r in records[:limit]]

    # ── SSE 订阅 ──────────────────────────────────────────────────────────

    async def subscribe(
        self,
        run_id: str,
        after_seq: int = 0,
    ) -> AsyncIterator[dict[str, Any]]:
        """
        异步生成器：先 replay ring buffer，再实时推新事件。
        支持 Last-Event-ID 断线续传（传 after_seq）。

        每个 event dict 包含 seq、kind、payload 等字段。
        """
        # 先 replay
        ring = self._ring.get(run_id, [])
        for ev in ring:
            if ev.get("seq", 0) > after_seq:
                yield ev

        # 判断是否已经结束
        record = self._records.get(run_id)
        if record and record.status not in ("running",):
            return

        # 创建专属 Queue 接收新事件
        q: asyncio.Queue[dict] = asyncio.Queue()
        self._queues.setdefault(run_id, []).append(q)
        try:
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=30.0)
                    if ev.get("_sentinel"):
                        break
                    yield ev
                except asyncio.TimeoutError:
                    # 发心跳保持连接
                    yield {"kind": "heartbeat", "ts": time.time()}
                    # 再检查是否已结束
                    rec = self._records.get(run_id)
                    if rec and rec.status not in ("running",):
                        break
        finally:
            qs = self._queues.get(run_id, [])
            if q in qs:
                qs.remove(q)

    # ── 内部 ──────────────────────────────────────────────────────────────

    def _push_event(self, run_id: str, event: dict[str, Any]) -> None:
        """把事件存入 ring buffer 并广播到所有订阅队列。"""
        ring = self._ring.setdefault(run_id, [])
        seq = len(ring) + 1
        event = {"seq": seq, **event}
        ring.append(event)
        if len(ring) > self._ring_max:
            ring.pop(0)
        for q in list(self._queues.get(run_id, [])):
            q.put_nowait(event)

    def _close_queues(self, run_id: str) -> None:
        """通知所有订阅者该 run 已结束。"""
        for q in list(self._queues.get(run_id, [])):
            q.put_nowait({"_sentinel": True})

    def _attach_runtime_forwarder(self, chat_service: Any, record: RunRecord) -> _RuntimeEventForwarder | None:
        runtime = getattr(chat_service, "_runtime", None)
        event_bus = getattr(runtime, "event_bus", None)
        if event_bus is None or not hasattr(event_bus, "subscribe"):
            return None
        forwarder = _RuntimeEventForwarder(self, record)
        event_bus.subscribe(forwarder)
        return forwarder

    def _detach_runtime_forwarder(self, chat_service: Any, forwarder: _RuntimeEventForwarder | None) -> None:
        if forwarder is None:
            return
        runtime = getattr(chat_service, "_runtime", None)
        event_bus = getattr(runtime, "event_bus", None)
        if event_bus is not None and hasattr(event_bus, "unsubscribe"):
            event_bus.unsubscribe(forwarder)

    async def _run_chat(
        self,
        chat_service: Any,
        record: RunRecord,
        cancel_ev: asyncio.Event,
    ) -> None:
        """实际执行 chat，捕获结果并更新 record。"""
        forwarder = self._attach_runtime_forwarder(chat_service, record)
        self._push_event(record.run_id, {
            "kind": "run.started",
            "run_id": record.run_id,
            "goal": record.goal,
            "ts": time.time(),
        })
        try:
            result = await chat_service.chat(
                user_id=record.user_id,
                session_id=record.session_id,
                message=record.goal,
            )
            record.status = result.status
            record.final_output = result.final_output
            record.failure_reason = result.failure_reason
            record.cost_summary = result.cost_summary or {}
            self._push_event(record.run_id, {
                "kind": f"run.{result.status}",
                "run_id": record.run_id,
                "final_output": result.final_output,
                "ts": time.time(),
            })
        except asyncio.CancelledError:
            record.status = "cancelled"
            self._push_event(record.run_id, {
                "kind": "run.cancelled",
                "run_id": record.run_id,
                "ts": time.time(),
            })
        except Exception as exc:
            record.status = "failed"
            record.failure_reason = str(exc)
            self._push_event(record.run_id, {
                "kind": "run.failed",
                "run_id": record.run_id,
                "error": str(exc),
                "ts": time.time(),
            })
        finally:
            self._detach_runtime_forwarder(chat_service, forwarder)
            self._close_queues(record.run_id)
            self._cancel_events.pop(record.run_id, None)

    async def _run_orchestrate(
        self,
        orchestrate_service: Any,
        record: RunRecord,
        cancel_ev: asyncio.Event,
    ) -> None:
        """实际执行 orchestrate，捕获结果并更新 record。"""
        self._push_event(record.run_id, {
            "kind": "run.started",
            "run_id": record.run_id,
            "goal": record.goal,
            "ts": time.time(),
        })
        try:
            summary = await orchestrate_service.run(goal=record.goal)
            record.status = summary.status
            record.final_output = None
            record.cost_summary = summary.cost_summary or {}
            self._push_event(record.run_id, {
                "kind": f"plan.{summary.status}",
                "run_id": record.run_id,
                "plan_run_id": summary.plan_run_id,
                "ts": time.time(),
            })
        except asyncio.CancelledError:
            record.status = "cancelled"
            self._push_event(record.run_id, {
                "kind": "run.cancelled",
                "run_id": record.run_id,
                "ts": time.time(),
            })
        except Exception as exc:
            record.status = "failed"
            record.failure_reason = str(exc)
            self._push_event(record.run_id, {
                "kind": "run.failed",
                "run_id": record.run_id,
                "error": str(exc),
                "ts": time.time(),
            })
        finally:
            self._close_queues(record.run_id)
            self._cancel_events.pop(record.run_id, None)
