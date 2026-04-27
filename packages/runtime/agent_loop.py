from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any

from langchain_core.messages import AIMessage, SystemMessage, ToolMessage

from .budget import BudgetController, IdempotencyStore
from .config import DEFAULT_LOADED_TOOL_NAMES
from .cost import CostTracker, TokenUsage
from .events import EventBus
from .executor import ToolExecutor
from .guardrail import GuardrailEngine
from .llm_client import NativeToolCallingLLMClient
from .message_builder import MessageBuilder
from .models import (
    AgentEvent,
    AgentState,
    EventKind,
    EventType,
    HumanInterventionRequired,
    OrchestrationResult,
    OrchestrationTurn,
    Phase,
    RunStatus,
    SessionMessage,
    StepRecord,
    ToolCall,
    ToolResult,
    deserialize_message,
    normalize_tool_message,
    safe_json_dumps,
    serialize_message,
    to_jsonable,
)


class RunCancelled(Exception):
    """外部通过 cancel() 主动取消 run 时抛出。"""
from packages.memory.compaction import CompactionPipeline
from .registry import ToolRegistry
from .store import FileCheckpointStore, FileSessionStore
from .timing import elapsed_ms, now, record_timing


class AgentRuntime:
    def __init__(
        self,
        llm_client: NativeToolCallingLLMClient,
        message_builder: MessageBuilder,
        tool_executor: ToolExecutor,
        registry: ToolRegistry,
        session_store: FileSessionStore,
        checkpoint_store: FileCheckpointStore,
        event_bus: EventBus,
        guardrail_engine: GuardrailEngine,
        budget_controller: BudgetController,
        idempotency_store: IdempotencyStore,
    ) -> None:
        self.failpoint: str | None = None
        self.llm_client = llm_client
        self.message_builder = message_builder
        self.tool_executor = tool_executor
        self.registry = registry
        self.session_store = session_store
        self.checkpoint_store = checkpoint_store
        self.event_bus = event_bus
        self.guardrail_engine = guardrail_engine
        self.budget_controller = budget_controller
        self.idempotency_store = idempotency_store
        self.memory_manager = None
        self.compaction_pipeline = CompactionPipeline()
        # A6: run_id → asyncio.Event，用于外部主动取消
        self._cancel_events: dict[str, asyncio.Event] = {}

    def set_failpoint(self, name: str | None) -> None:
        self.failpoint = name

    def _hit_failpoint(self, name: str) -> None:
        if self.failpoint == name:
            raise RuntimeError(f"injected failpoint: {name}")

    def cancel(self, run_id: str) -> bool:
        """从外部取消一个正在运行的 run（线程安全，返回是否找到该 run）。"""
        ev = self._cancel_events.get(run_id)
        if ev:
            ev.set()
            return True
        return False

    async def fork(self, run_id: str, from_step: int | None = None) -> str:
        """
        从已有 checkpoint 分叉一个新 run。
        from_step=None 表示从最新检查点分叉；返回新 run_id。
        """
        state = self.checkpoint_store.load(run_id)
        if state is None:
            raise ValueError(f"checkpoint not found: {run_id}")
        new_run_id = str(uuid.uuid4())
        new_state = AgentState(
            run_id=new_run_id,
            user_id=state.user_id,
            task=state.task,
            session_id=state.session_id,
            status=RunStatus.IDLE,
            phase=Phase.DECIDING,
            step=from_step if from_step is not None else state.step,
            history=list(state.history[: (from_step or state.step)]),
            tool_results=list(state.tool_results),
            runtime_messages=list(state.runtime_messages),
            conversation=list(state.conversation),
            metadata={
                **state.metadata,
                "forked_from": run_id,
                "fork_step": from_step or state.step,
            },
        )
        self.checkpoint_store.save(new_state)
        await self.event_bus.publish(
            AgentEvent(
                run_id=new_run_id,
                event_type=EventType.RUN_STARTED,
                event_kind=EventKind.run_forked.value,
                ts=time.time(),
                step=new_state.step,
                payload={"parent_run_id": run_id, "from_step": from_step or state.step},
            )
        )
        return new_run_id

    async def chat(self, user_id: str, session_id: str, message: str) -> AgentState:
        run_start = now()
        self.guardrail_engine.validate_user_input(message)
        session_start = now()
        previous = await self.session_store.load_messages(session_id)
        await self.session_store.append_message(session_id, "user", message)
        session_io_ms = elapsed_ms(session_start)
        recall_text: str | None = None
        memory_recall_ms = 0
        memory_error: dict[str, Any] | None = None
        if self.memory_manager is not None:
            memory_start = now()
            try:
                recall_pack = await self.memory_manager.recall(message, user_id)
            except Exception as exc:
                memory_recall_ms = elapsed_ms(memory_start)
                memory_error = {"type": type(exc).__name__, "message": str(exc), "stage": "memory_recall"}
            else:
                memory_recall_ms = elapsed_ms(memory_start)
                if recall_pack.injected_text:
                    recall_text = recall_pack.injected_text
                await self.event_bus.publish(AgentEvent(
                    run_id="",
                    event_type=EventType.MEMORY_RECALLED,
                    ts=time.time(),
                    step=0,
                    payload={"items": len(recall_pack.items), "query": message[:80], "duration_ms": memory_recall_ms},
                ))
        state = AgentState(
            run_id=str(uuid.uuid4()),
            user_id=user_id,
            task=message,
            session_id=session_id,
            status=RunStatus.RUNNING,
            phase=Phase.DECIDING,
            conversation=previous + [SessionMessage(role="user", content=message)],
            metadata={"tool_ledger": {}, "loaded_tools": list(DEFAULT_LOADED_TOOL_NAMES), "recall_text": recall_text},
        )
        record_timing(state.metadata, "session_io_ms", session_io_ms)
        if self.memory_manager is not None:
            record_timing(state.metadata, "memory_recall_ms", memory_recall_ms)
        if memory_error is not None:
            state.metadata.setdefault("errors", []).append(memory_error)
        state.runtime_messages = [serialize_message(m) for m in self.message_builder.build_initial_messages(state)]
        await self.event_bus.publish(
            AgentEvent(
                run_id=state.run_id,
                event_type=EventType.RUN_STARTED,
                ts=time.time(),
                step=state.step,
                payload={"task": message, "session_id": session_id},
            )
        )
        cancel_ev = asyncio.Event()
        self._cancel_events[state.run_id] = cancel_ev
        await self._save_checkpoint(state)
        try:
            result = await self._continue(state, cancel_ev)
            self._refresh_total_timing(result, run_start)
            await self._save_checkpoint(result)
            return result
        except RunCancelled:
            state.status = RunStatus.CANCELLED
            state.phase = Phase.COMPLETED
            state.updated_at = time.time()
            await self.event_bus.publish(
                AgentEvent(
                    run_id=state.run_id,
                    event_type=EventType.RUN_FAILED,
                    event_kind=EventKind.run_cancelled.value,
                    ts=time.time(),
                    step=state.step,
                    payload={"reason": "cancelled"},
                )
            )
            await self._save_checkpoint(state)
            return state
        except Exception as exc:
            await self._mark_failed(state, exc, run_start)
            return state
        finally:
            self._cancel_events.pop(state.run_id, None)

    async def restore(self, run_id: str) -> AgentState:
        state = self.checkpoint_store.load(run_id)
        if state is None:
            raise ValueError(f"checkpoint not found: {run_id}")
        snapshot = state.metadata.get("tool_ledger", {})
        if isinstance(snapshot, dict):
            self.idempotency_store.load_snapshot(snapshot)
        return state

    async def resume(self, run_id: str, human_decision: dict[str, Any] | None = None) -> AgentState:
        run_start = now()
        state = await self.restore(run_id)
        if state.status in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}:
            return state
        if state.phase == Phase.WAITING_HUMAN:
            if human_decision is None:
                raise ValueError("human_decision is required while waiting for approval")
            approved = bool(human_decision.get("approved", False))
            edited_arguments = human_decision.get("edited_arguments")
            approved_by = str(human_decision.get("approved_by", "human"))
            if not approved:
                state.status = RunStatus.COMPLETED
                state.phase = Phase.COMPLETED
                state.final_output = "human approval rejected; execution stopped"
                state.pending_human_request = None
                await self.event_bus.publish(
                    AgentEvent(
                        run_id=state.run_id,
                        event_type=EventType.HUMAN_REJECTED,
                        ts=time.time(),
                        step=state.step,
                        payload={"reason": "approval rejected"},
                    )
                )
                await self._finalize(state)
                return state
            if state.pending_tool_call is None:
                raise ValueError("missing pending_tool_call while waiting for human input")
            if edited_arguments is not None:
                if not isinstance(edited_arguments, dict):
                    raise ValueError("edited_arguments must be a dict")
                self.guardrail_engine.validate_tool_args(state.pending_tool_call["tool_name"], edited_arguments)
                state.pending_tool_call["arguments"] = edited_arguments
            state.pending_tool_call.setdefault("metadata", {})
            state.pending_tool_call["metadata"]["approved"] = True
            state.pending_tool_call["metadata"]["approved_by"] = approved_by
            state.pending_human_request = None
            state.status = RunStatus.RUNNING
            state.phase = Phase.TOOL_PENDING
            # 重置计时器，排除等待人工审批所花费的时间
            state.started_at = time.time()
            await self.event_bus.publish(
                AgentEvent(
                    run_id=state.run_id,
                    event_type=EventType.HUMAN_APPROVED,
                    ts=time.time(),
                    step=state.step,
                    payload={"tool_name": state.pending_tool_call["tool_name"], "approved_by": approved_by},
                )
            )
        await self.event_bus.publish(
            AgentEvent(
                run_id=state.run_id,
                event_type=EventType.RUN_RESUMED,
                ts=time.time(),
                step=state.step,
                payload={"phase": state.phase.value},
            )
        )
        cancel_ev = asyncio.Event()
        self._cancel_events[state.run_id] = cancel_ev
        await self._save_checkpoint(state)
        try:
            result = await self._continue(state, cancel_ev)
            self._refresh_total_timing(result, run_start)
            await self._save_checkpoint(result)
            return result
        except RunCancelled:
            state.status = RunStatus.CANCELLED
            state.phase = Phase.COMPLETED
            state.updated_at = time.time()
            await self.event_bus.publish(
                AgentEvent(
                    run_id=state.run_id,
                    event_type=EventType.RUN_FAILED,
                    event_kind=EventKind.run_cancelled.value,
                    ts=time.time(),
                    step=state.step,
                    payload={"reason": "cancelled by approval rejection"},
                )
            )
            await self._save_checkpoint(state)
            return state
        except Exception as exc:
            await self._mark_failed(state, exc, run_start)
            return state
        finally:
            self._cancel_events.pop(state.run_id, None)

    async def orchestrate(self, user_id: str, session_id: str, goal: str, max_turns: int = 3) -> OrchestrationResult:
        state = await self.chat(user_id=user_id, session_id=session_id, message=goal)
        turn = OrchestrationTurn(
            turn_index=0,
            output=state.final_output or state.failure_reason or "",
            run_id=state.run_id,
            status=state.status.value,
        )
        return OrchestrationResult(turns=[turn], completed=state.status == RunStatus.COMPLETED, final_output=turn.output)

    async def _continue(self, state: AgentState, cancel_ev: asyncio.Event | None = None) -> AgentState:
        while True:
            # A6: 每步循环入口检查外部取消信号
            if cancel_ev is not None and cancel_ev.is_set():
                raise RunCancelled("run cancelled externally")
            state.updated_at = time.time()
            self.budget_controller.check(state)
            runtime_messages = self._refresh_system_prompt(state, [deserialize_message(m) for m in state.runtime_messages])

            if state.phase == Phase.DECIDING:
                # Phase B: compaction before model call
                _stats_before = state.metadata.get("compaction_stats", {})
                compaction_start = now()
                await self.compaction_pipeline.prepare(state)
                record_timing(state.metadata, "compaction_ms", elapsed_ms(compaction_start), step=state.step)
                _stats_after = state.metadata.get("compaction_stats", {})
                if _stats_after.get("auto_deleted_tokens", 0) > _stats_before.get("auto_deleted_tokens", 0):
                    await self.event_bus.publish(AgentEvent(
                        run_id=state.run_id, event_type=EventType.AUTOCOMPACT_APPLIED, ts=time.time(), step=state.step,
                        payload={"saved_tokens": _stats_after["auto_deleted_tokens"] - _stats_before.get("auto_deleted_tokens", 0)},
                    ))
                if _stats_after.get("snip_deleted_tokens", 0) > _stats_before.get("snip_deleted_tokens", 0):
                    await self.event_bus.publish(AgentEvent(
                        run_id=state.run_id, event_type=EventType.CONTEXT_SNIPPED, ts=time.time(), step=state.step,
                        payload={"deleted_tokens": _stats_after["snip_deleted_tokens"] - _stats_before.get("snip_deleted_tokens", 0)},
                    ))
                if _stats_after.get("micro_deleted_tokens", 0) > _stats_before.get("micro_deleted_tokens", 0):
                    await self.event_bus.publish(AgentEvent(
                        run_id=state.run_id, event_type=EventType.MICROCOMPACT_APPLIED, ts=time.time(), step=state.step,
                        payload={"saved_tokens": _stats_after["micro_deleted_tokens"] - _stats_before.get("micro_deleted_tokens", 0)},
                    ))
                # rebuild runtime_messages after compaction
                runtime_messages = self._refresh_system_prompt(state, [deserialize_message(m) for m in state.runtime_messages])
                await self.event_bus.publish(AgentEvent(run_id=state.run_id, event_type=EventType.STEP_STARTED, ts=time.time(), step=state.step))
                model_start = now()
                response = await self.llm_client.invoke(runtime_messages, tool_schemas=self.registry.openai_tools(self._visible_tool_names(state)))
                model_ms = elapsed_ms(model_start)
                content, tool_calls, token_usage = self.llm_client.extract_content_and_tool_calls(response)
                record_timing(
                    state.metadata,
                    "model_invoke_ms",
                    model_ms,
                    step=state.step,
                    tool_calls=len(tool_calls),
                    model_name=self.llm_client.model_name,
                )
                cost_tracker = self._get_cost_tracker(state)
                cost_tracker.add(self.llm_client.model_name, token_usage)
                await self.event_bus.publish(
                    AgentEvent(
                        run_id=state.run_id,
                        event_type=EventType.MODEL_OUTPUT,
                        ts=time.time(),
                        step=state.step,
                        payload={"content": content, "tool_calls": tool_calls},
                    )
                )
                state.history.append(
                    StepRecord(
                        step=state.step,
                        phase=state.phase.value,
                        raw_content=content,
                        raw_tool_calls=tool_calls,
                        model_name=self.llm_client.model_name,
                    )
                )
                runtime_messages.append(response)
                state.runtime_messages = [serialize_message(m) for m in runtime_messages]
                await self._save_checkpoint(state)
                if not tool_calls:
                    answer = (content or "").strip() or "no valid answer was produced"
                    self.guardrail_engine.validate_final_output(answer)
                    state.final_output = answer
                    state.status = RunStatus.COMPLETED
                    state.phase = Phase.COMPLETED
                    await self._finalize(state)
                    return state

                # ── 并行执行多个 tool_calls ──────────────────────────────────
                # 对每个 tool_call 逐一检查是否需要人工审批：
                #   • 需要审批 → 退回到单步 TOOL_PENDING 流程（只处理第一个需要审批的）
                #   • 全部低风险 → 并行执行，一次性将所有 ToolMessage 追加到 history
                # 检测阶段：找出第一个需要审批的 call
                approval_idx: int | None = None
                for _i, _rc in enumerate(tool_calls):
                    _spec = self.registry._specs.get(_rc["name"])
                    if _spec is not None and getattr(_spec, "approval_policy", "never") == "always":
                        approval_idx = _i
                        break

                if approval_idx is not None:
                    # 有需要审批的工具：只处理 approval_idx 之前的低风险调用（若有），
                    # 然后把需要审批的工具交给单步流程。
                    # 为保证 AIMessage tool_call_id 与 ToolMessage 一一对应，
                    # 先把 response 替换为只含当前批次的裁剪版本。
                    batch = tool_calls[:approval_idx]  # 审批前的低风险批次
                    pending_raw = tool_calls[approval_idx]  # 需要审批的

                    if batch:
                        # 先并行跑前面的低风险调用
                        batch_calls = [
                            ToolCall(
                                call_id=rc["id"],
                                tool_name=rc["name"],
                                arguments=rc["arguments"],
                                idempotency_key=f"{state.run_id}:{rc['name']}:{safe_json_dumps(rc['arguments'])}",
                            )
                            for rc in batch
                        ]
                        # 裁剪 AIMessage 只含这批 tool_calls（转换为 LangChain args 格式）
                        lc_batch = [{"id": rc["id"], "name": rc["name"], "args": rc["arguments"], "type": "tool_call"} for rc in batch]
                        trimmed = AIMessage(
                            content=content or "",
                            tool_calls=lc_batch,
                            additional_kwargs=self._assistant_reasoning_kwargs(response),
                        )
                        runtime_messages[-1] = trimmed
                        for bc in batch_calls:
                            await self.event_bus.publish(AgentEvent(
                                run_id=state.run_id, event_type=EventType.TOOL_SELECTED,
                                ts=time.time(), step=state.step,
                                payload={"tool_name": bc.tool_name, "arguments": bc.arguments},
                            ))
                        batch_start = now()
                        results = await asyncio.gather(*[
                            self.tool_executor.execute(state, bc) for bc in batch_calls
                        ])
                        record_timing(state.metadata, "tool_batch_ms", elapsed_ms(batch_start), step=state.step, count=len(batch_calls))
                        for bc, br in zip(batch_calls, results):
                            state.tool_results.append(br)
                            runtime_messages.append(ToolMessage(
                                content=normalize_tool_message(br), tool_call_id=bc.call_id
                            ))
                            self._apply_dynamic_tool_loading(state, br)
                            await self.event_bus.publish(AgentEvent(
                                run_id=state.run_id, event_type=EventType.STEP_FINISHED,
                                ts=time.time(), step=state.step,
                                payload={"tool_name": bc.tool_name, "ok": br.ok},
                            ))
                        state.metadata["tool_ledger"] = self.idempotency_store.export_snapshot()
                        runtime_messages = self._refresh_system_prompt(state, runtime_messages)
                        state.runtime_messages = [serialize_message(m) for m in runtime_messages]

                    # 现在把需要审批的工具单独作为下一个 AIMessage 交给单步流程
                    pending_call = ToolCall(
                        call_id=pending_raw["id"],
                        tool_name=pending_raw["name"],
                        arguments=pending_raw["arguments"],
                        idempotency_key=f"{state.run_id}:{pending_raw['name']}:{safe_json_dumps(pending_raw['arguments'])}",
                    )
                    # 只含这一个 tool_call 的 AIMessage（转换为 LangChain args 格式）
                    _pr = tool_calls[approval_idx]
                    approval_ai_msg = AIMessage(
                        content="",
                        tool_calls=[{"id": _pr["id"], "name": _pr["name"], "args": _pr["arguments"], "type": "tool_call"}],
                        additional_kwargs=self._assistant_reasoning_kwargs(response),
                    )
                    if batch:
                        # batch 已执行并有对应 ToolMessage，approval_ai_msg 作为新轮次追加
                        runtime_messages.append(approval_ai_msg)
                    else:
                        # batch 为空：原始 AIMessage（含所有 tool_call_id）直接替换为只含审批项
                        # 避免 history 中存在无 ToolMessage 的 tool_call_id 导致 400
                        runtime_messages[-1] = approval_ai_msg
                    state.runtime_messages = [serialize_message(m) for m in runtime_messages]
                    state.pending_tool_call = to_jsonable(pending_call)
                    state.phase = Phase.TOOL_PENDING
                    await self.event_bus.publish(AgentEvent(
                        run_id=state.run_id, event_type=EventType.TOOL_SELECTED,
                        ts=time.time(), step=state.step,
                        payload={"tool_name": pending_call.tool_name, "arguments": pending_call.arguments},
                    ))
                    await self._save_checkpoint(state)
                    continue

                # 全部低风险：并行执行所有 tool_calls
                all_calls = [
                    ToolCall(
                        call_id=rc["id"],
                        tool_name=rc["name"],
                        arguments=rc["arguments"],
                        idempotency_key=f"{state.run_id}:{rc['name']}:{safe_json_dumps(rc['arguments'])}",
                    )
                    for rc in tool_calls
                ]
                for ac in all_calls:
                    await self.event_bus.publish(AgentEvent(
                        run_id=state.run_id, event_type=EventType.TOOL_SELECTED,
                        ts=time.time(), step=state.step,
                        payload={"tool_name": ac.tool_name, "arguments": ac.arguments},
                    ))
                state.status = RunStatus.TOOL_RUNNING
                batch_start = now()
                all_results = await asyncio.gather(*[
                    self.tool_executor.execute(state, ac) for ac in all_calls
                ])
                record_timing(state.metadata, "tool_batch_ms", elapsed_ms(batch_start), step=state.step, count=len(all_calls))
                state.status = RunStatus.RUNNING
                for ac, ar in zip(all_calls, all_results):
                    state.tool_results.append(ar)
                    runtime_messages.append(ToolMessage(
                        content=normalize_tool_message(ar), tool_call_id=ac.call_id
                    ))
                    self._apply_dynamic_tool_loading(state, ar)
                    await self.event_bus.publish(AgentEvent(
                        run_id=state.run_id, event_type=EventType.STEP_FINISHED,
                        ts=time.time(), step=state.step,
                        payload={"tool_name": ac.tool_name, "ok": ar.ok},
                    ))
                state.metadata["tool_ledger"] = self.idempotency_store.export_snapshot()
                runtime_messages = self._refresh_system_prompt(state, runtime_messages)
                state.runtime_messages = [serialize_message(m) for m in runtime_messages]
                state.pending_tool_call = None
                state.pending_tool_result = None
                state.phase = Phase.DECIDING
                state.step += 1
                await self._save_checkpoint(state)
                continue

            if state.phase == Phase.TOOL_PENDING:
                if state.pending_tool_call is None:
                    raise RuntimeError("TOOL_PENDING without pending_tool_call")
                call = ToolCall(**state.pending_tool_call)
                state.status = RunStatus.TOOL_RUNNING
                try:
                    self._hit_failpoint("before_tool_execute")
                    tool_start = now()
                    result = await self.tool_executor.execute(state, call)
                    record_timing(state.metadata, "tool_pending_ms", elapsed_ms(tool_start), step=state.step, tool_name=call.tool_name)
                except HumanInterventionRequired as exc:
                    state.status = RunStatus.WAITING_HUMAN
                    state.phase = Phase.WAITING_HUMAN
                    state.pending_human_request = to_jsonable(exc.request)
                    await self.event_bus.publish(
                        AgentEvent(
                            run_id=state.run_id,
                            event_type=EventType.HUMAN_REQUIRED,
                            ts=time.time(),
                            step=state.step,
                            payload={"reason": exc.request.reason, "tool_name": call.tool_name},
                        )
                    )
                    await self._save_checkpoint(state)
                    return state
                state.status = RunStatus.RUNNING
                state.tool_results.append(result)
                state.pending_tool_result = to_jsonable(result)
                state.metadata["tool_ledger"] = self.idempotency_store.export_snapshot()
                state.phase = Phase.TOOL_EXECUTED
                await self._save_checkpoint(state)
                self._hit_failpoint("after_tool_executed_checkpoint")
                continue

            if state.phase == Phase.TOOL_EXECUTED:
                if state.pending_tool_call is None or state.pending_tool_result is None:
                    raise RuntimeError("TOOL_EXECUTED missing pending state")
                call = ToolCall(**state.pending_tool_call)
                result = ToolResult(**state.pending_tool_result)
                runtime_messages.append(ToolMessage(content=normalize_tool_message(result), tool_call_id=call.call_id))
                self._apply_dynamic_tool_loading(state, result)
                runtime_messages = self._refresh_system_prompt(state, runtime_messages)
                state.runtime_messages = [serialize_message(m) for m in runtime_messages]
                state.pending_tool_call = None
                state.pending_tool_result = None
                state.phase = Phase.DECIDING
                state.step += 1
                await self.event_bus.publish(
                    AgentEvent(
                        run_id=state.run_id,
                        event_type=EventType.STEP_FINISHED,
                        ts=time.time(),
                        step=state.step,
                        payload={"tool_name": call.tool_name, "ok": result.ok},
                    )
                )
                await self._save_checkpoint(state)
                continue

            if state.phase in {Phase.WAITING_HUMAN, Phase.COMPLETED}:
                return state
            raise RuntimeError(f"unknown phase: {state.phase}")

    def _visible_tool_names(self, state: AgentState) -> list[str]:
        loaded = state.metadata.get("loaded_tools")
        if not isinstance(loaded, list) or not loaded:
            state.metadata["loaded_tools"] = list(DEFAULT_LOADED_TOOL_NAMES)
            loaded = state.metadata["loaded_tools"]
        return [str(name) for name in loaded]

    def _refresh_system_prompt(self, state: AgentState, runtime_messages: list[Any]) -> list[Any]:
        prompt = self.message_builder.build_system_prompt(state)
        if runtime_messages and isinstance(runtime_messages[0], SystemMessage):
            runtime_messages[0] = SystemMessage(content=prompt)
            return runtime_messages
        return [SystemMessage(content=prompt), *runtime_messages]

    def _apply_dynamic_tool_loading(self, state: AgentState, result: ToolResult) -> None:
        if result.tool_name != "tool_search" or not result.ok or not result.output:
            return
        try:
            payload = json.loads(result.output)
        except json.JSONDecodeError:
            return
        recommended = payload.get("recommended_tools")
        if not isinstance(recommended, list):
            return
        loaded = self._visible_tool_names(state)
        for item in recommended:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            if isinstance(name, str) and item.get("load") and self.registry.has_tool(name) and name not in loaded:
                loaded.append(name)
        state.metadata["loaded_tools"] = loaded

    def _assistant_reasoning_kwargs(self, response: Any) -> dict[str, Any]:
        additional_kwargs = getattr(response, "additional_kwargs", {}) or {}
        if not isinstance(additional_kwargs, dict):
            additional_kwargs = {}
        preserved = {}
        for key in ("reasoning_content", "reasoning"):
            if key in additional_kwargs:
                preserved[key] = additional_kwargs[key]
        response_metadata = getattr(response, "response_metadata", {}) or {}
        if isinstance(response_metadata, dict):
            for key in ("reasoning_content", "reasoning"):
                if key in response_metadata and key not in preserved:
                    preserved[key] = response_metadata[key]
        return preserved

    async def _save_checkpoint(self, state: AgentState) -> None:
        self.checkpoint_store.save(state)
        await self.event_bus.publish(AgentEvent(run_id=state.run_id, event_type=EventType.CHECKPOINT_SAVED, ts=time.time(), step=state.step))

    async def _finalize(self, state: AgentState) -> None:
        finalize_start = now()
        await self._save_checkpoint(state)
        await self.event_bus.publish(
            AgentEvent(
                run_id=state.run_id,
                event_type=EventType.RUN_COMPLETED,
                ts=time.time(),
                step=state.step,
                payload={"final_output": state.final_output},
            )
        )
        try:
            await self.session_store.append_message(state.session_id, "assistant", state.final_output or "")
        except Exception as exc:
            state.metadata.setdefault("errors", []).append(
                {"type": type(exc).__name__, "message": str(exc), "stage": "session_append_assistant"}
            )
        if self.memory_manager is not None:
            memory_start = now()
            try:
                entries = await self.memory_manager.remember_run(state)
            except Exception as exc:
                record_timing(state.metadata, "memory_store_ms", elapsed_ms(memory_start), stored=0)
                state.metadata.setdefault("errors", []).append(
                    {"type": type(exc).__name__, "message": str(exc), "stage": "memory_store"}
                )
            else:
                record_timing(state.metadata, "memory_store_ms", elapsed_ms(memory_start), stored=len(entries))
                await self.event_bus.publish(AgentEvent(
                    run_id=state.run_id,
                    event_type=EventType.MEMORY_STORED,
                    ts=time.time(),
                    step=state.step,
                    payload={"stored": len(entries)},
                ))
        record_timing(state.metadata, "finalize_ms", elapsed_ms(finalize_start))

    def _refresh_total_timing(self, state: AgentState, run_start: float) -> None:
        total_ms = elapsed_ms(run_start)
        summary = state.metadata.setdefault("timing_summary", {})
        summary["total_ms"] = total_ms
        summary["runtime_wall_ms"] = total_ms
        summary["tool_total_ms"] = sum(result.latency_ms for result in state.tool_results)
        cost_tracker = self._get_cost_tracker(state)
        cost_total = cost_tracker.total()
        if cost_total["total_tokens"] > 0:
            state.metadata["cost_summary"] = cost_total

    def _get_cost_tracker(self, state: AgentState) -> CostTracker:
        if "cost_tracker" not in state.metadata:
            state.metadata["cost_tracker"] = CostTracker()
        tracker = state.metadata["cost_tracker"]
        if isinstance(tracker, CostTracker):
            return tracker
        if isinstance(tracker, dict):
            restored = CostTracker()
            for rec_data in tracker.get("records", []):
                usage_data = rec_data.get("usage", {})
                usage = TokenUsage(
                    input_tokens=usage_data.get("input_tokens", 0),
                    output_tokens=usage_data.get("output_tokens", 0),
                    total_tokens=usage_data.get("total_tokens", 0),
                )
                restored.add(rec_data.get("model_name", "unknown"), usage)
            state.metadata["cost_tracker"] = restored
            return restored
        state.metadata["cost_tracker"] = CostTracker()
        return state.metadata["cost_tracker"]

    async def _mark_failed(self, state: AgentState, exc: Exception, run_start: float) -> None:
        state.status = RunStatus.FAILED
        state.failure_reason = f"unhandled error: {exc}"
        state.phase = Phase.COMPLETED
        state.updated_at = time.time()
        state.metadata.setdefault("errors", []).append(
            {"type": type(exc).__name__, "message": str(exc), "step": state.step, "phase": state.phase.value}
        )
        self._refresh_total_timing(state, run_start)
        await self.event_bus.publish(AgentEvent(
            run_id=state.run_id,
            event_type=EventType.RUN_FAILED,
            ts=time.time(),
            step=state.step,
            payload={"failure_reason": state.failure_reason, "timing_summary": state.metadata.get("timing_summary", {})},
        ))
        await self.event_bus.publish(AgentEvent(
            run_id=state.run_id,
            event_type=EventType.RUN_COMPLETED,
            ts=time.time(),
            step=state.step,
            payload={"final_output": None, "failure_reason": state.failure_reason},
        ))
        await self._save_checkpoint(state)
