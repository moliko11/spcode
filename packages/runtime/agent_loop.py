from __future__ import annotations

import json
import time
import uuid
from typing import Any

from langchain_core.messages import SystemMessage, ToolMessage

from .budget import BudgetController, IdempotencyStore
from .config import DEFAULT_LOADED_TOOL_NAMES
from .events import EventBus
from .executor import ToolExecutor
from .guardrail import GuardrailEngine
from .llm_client import NativeToolCallingLLMClient
from .message_builder import MessageBuilder
from .models import (
    AgentEvent,
    AgentState,
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
from packages.memory.compaction import CompactionPipeline
from .registry import ToolRegistry
from .store import FileCheckpointStore, FileSessionStore


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

    def set_failpoint(self, name: str | None) -> None:
        self.failpoint = name

    def _hit_failpoint(self, name: str) -> None:
        if self.failpoint == name:
            raise RuntimeError(f"injected failpoint: {name}")

    async def chat(self, user_id: str, session_id: str, message: str) -> AgentState:
        self.guardrail_engine.validate_user_input(message)
        previous = await self.session_store.load_messages(session_id)
        await self.session_store.append_message(session_id, "user", message)
        recall_text: str | None = None
        if self.memory_manager is not None:
            recall_pack = await self.memory_manager.recall(message, user_id)
            if recall_pack.injected_text:
                recall_text = recall_pack.injected_text
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
        await self._save_checkpoint(state)
        return await self._continue(state)

    async def restore(self, run_id: str) -> AgentState:
        state = self.checkpoint_store.load(run_id)
        if state is None:
            raise ValueError(f"checkpoint not found: {run_id}")
        snapshot = state.metadata.get("tool_ledger", {})
        if isinstance(snapshot, dict):
            self.idempotency_store.load_snapshot(snapshot)
        return state

    async def resume(self, run_id: str, human_decision: dict[str, Any] | None = None) -> AgentState:
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
        await self._save_checkpoint(state)
        return await self._continue(state)

    async def orchestrate(self, user_id: str, session_id: str, goal: str, max_turns: int = 3) -> OrchestrationResult:
        state = await self.chat(user_id=user_id, session_id=session_id, message=goal)
        turn = OrchestrationTurn(
            turn_index=0,
            output=state.final_output or state.failure_reason or "",
            run_id=state.run_id,
            status=state.status.value,
        )
        return OrchestrationResult(turns=[turn], completed=state.status == RunStatus.COMPLETED, final_output=turn.output)

    async def _continue(self, state: AgentState) -> AgentState:
        while True:
            state.updated_at = time.time()
            self.budget_controller.check(state)
            runtime_messages = self._refresh_system_prompt(state, [deserialize_message(m) for m in state.runtime_messages])

            if state.phase == Phase.DECIDING:
                # Phase B: compaction before model call
                _stats_before = state.metadata.get("compaction_stats", {})
                self.compaction_pipeline.prepare(state)
                _stats_after = state.metadata.get("compaction_stats", {})
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
                response = await self.llm_client.invoke(runtime_messages, tool_schemas=self.registry.openai_tools(self._visible_tool_names(state)))
                content, tool_calls = self.llm_client.extract_content_and_tool_calls(response)
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
                raw_call = tool_calls[0]
                call = ToolCall(
                    call_id=raw_call["id"],
                    tool_name=raw_call["name"],
                    arguments=raw_call["arguments"],
                    idempotency_key=f"{state.run_id}:{raw_call['name']}:{safe_json_dumps(raw_call['arguments'])}",
                )
                state.pending_tool_call = to_jsonable(call)
                state.phase = Phase.TOOL_PENDING
                await self.event_bus.publish(
                    AgentEvent(
                        run_id=state.run_id,
                        event_type=EventType.TOOL_SELECTED,
                        ts=time.time(),
                        step=state.step,
                        payload={"tool_name": call.tool_name, "arguments": call.arguments},
                    )
                )
                await self._save_checkpoint(state)
                continue

            if state.phase == Phase.TOOL_PENDING:
                if state.pending_tool_call is None:
                    raise RuntimeError("TOOL_PENDING without pending_tool_call")
                call = ToolCall(**state.pending_tool_call)
                state.status = RunStatus.TOOL_RUNNING
                try:
                    self._hit_failpoint("before_tool_execute")
                    result = await self.tool_executor.execute(state, call)
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

    async def _save_checkpoint(self, state: AgentState) -> None:
        self.checkpoint_store.save(state)
        await self.event_bus.publish(AgentEvent(run_id=state.run_id, event_type=EventType.CHECKPOINT_SAVED, ts=time.time(), step=state.step))

    async def _finalize(self, state: AgentState) -> None:
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
        await self.session_store.append_message(state.session_id, "assistant", state.final_output or "")
        if self.memory_manager is not None:
            await self.memory_manager.remember_run(state)
