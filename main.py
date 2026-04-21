from __future__ import annotations

import argparse
import asyncio
import json
import os
from typing import Any

from packages.runtime.bootstrap import build_runtime, build_llm
from packages.runtime.config import PLANS_DIR, PLAN_RUNS_DIR
from packages.planner.planner import Planner
from packages.planner.store import PlanStore
from packages.orchestrator.orchestrator import Orchestrator
from packages.orchestrator.store import PlanRunStore


def _find_waiting_step(plan_run: Any) -> Any | None:
    return next((step for step in plan_run.step_runs if step.status.value == "waiting_human"), None)


def _prompt_approval_action(pending_request: dict[str, Any]) -> tuple[bool, dict[str, Any] | None, str]:
    tool_name = pending_request.get("context", {}).get("tool_name", "tool")
    print("\n审批请求：")
    print(json.dumps(pending_request, ensure_ascii=False, indent=2))
    print("\n请选择操作：")
    print("  a = approve（批准）")
    print("  r = reject（拒绝）")
    print("  e = edit（编辑参数后批准）")

    while True:
        action = input(f"approval[{tool_name}] (a/r/e): ").strip().lower()
        if action in {"a", "approve"}:
            return True, None, "human"
        if action in {"r", "reject"}:
            return False, None, "human"
        if action in {"e", "edit"}:
            while True:
                raw = input("请输入新的 arguments JSON: ").strip()
                try:
                    edited = json.loads(raw)
                except json.JSONDecodeError as exc:
                    print(f"JSON 解析失败：{exc}")
                    continue
                if not isinstance(edited, dict):
                    print("edited arguments 必须是 JSON 对象")
                    continue
                return True, edited, "human"
        print("无效输入，请输入 a / r / e")


def configure_provider(args: argparse.Namespace) -> None:
    if getattr(args, "provider", None):
        os.environ["MOLIKO_LLM_PROVIDER"] = args.provider


async def run_chat(args: argparse.Namespace) -> None:
    configure_provider(args)
    runtime = build_runtime()

    if args.message:
        state = await runtime.chat(user_id=args.user_id, session_id=args.session_id, message=args.message)
        print(state.final_output or state.failure_reason or "")
        print(f"run_id={state.run_id} status={state.status.value}")
        return

    print(f"session_id={args.session_id} user_id={args.user_id}")
    print("Type 'exit' to stop.")
    print("Use prompts like path=README.md, query=memory, or message=hello fail_times=1 to exercise tools in mock mode.")
    while True:
        message = input("you> ").strip()
        if not message:
            continue
        if message.lower() in {"exit", "quit"}:
            break
        state = await runtime.chat(user_id=args.user_id, session_id=args.session_id, message=message)
        print(f"assistant> {state.final_output or state.failure_reason or ''}")
        print(f"run_id={state.run_id} status={state.status.value}")


async def run_orchestrate(args: argparse.Namespace) -> None:
    configure_provider(args)
    runtime = build_runtime()
    llm = build_llm()
    planner = Planner(llm=llm)
    plan_store = PlanStore(PLANS_DIR)
    plan_run_store = PlanRunStore(PLAN_RUNS_DIR)
    orchestrator = Orchestrator(
        runtime=runtime,
        planner=planner,
        plan_store=plan_store,
        plan_run_store=plan_run_store,
        user_id=args.user_id,
    )
    plan_run = await orchestrator.run(goal=args.goal, context=args.context or "")
    print(json.dumps(plan_run.to_dict(), ensure_ascii=False, indent=2))
    print(f"\nplan_run_id={plan_run.plan_run_id}  status={plan_run.status}  steps={len(plan_run.step_runs)}")

    while True:
        pending = _find_waiting_step(plan_run)
        if pending is None:
            break
        print("approval_required=true")
        approved, edited_arguments, approved_by = _prompt_approval_action(pending.pending_human_request or {})
        plan_run = await orchestrator.resume(
            plan_run_id=plan_run.plan_run_id,
            approved=approved,
            approved_by=approved_by,
            edited_arguments=edited_arguments,
        )
        print(json.dumps(plan_run.to_dict(), ensure_ascii=False, indent=2))
        print(f"\nplan_run_id={plan_run.plan_run_id}  status={plan_run.status}  steps={len(plan_run.step_runs)}")
        if not approved:
            break


async def run_approve(args: argparse.Namespace) -> None:
    configure_provider(args)
    runtime = build_runtime()
    llm = build_llm()
    planner = Planner(llm=llm)
    plan_store = PlanStore(PLANS_DIR)
    plan_run_store = PlanRunStore(PLAN_RUNS_DIR)
    orchestrator = Orchestrator(
        runtime=runtime,
        planner=planner,
        plan_store=plan_store,
        plan_run_store=plan_run_store,
        user_id=args.user_id,
    )
    edited_arguments = json.loads(args.edited_arguments) if args.edited_arguments else None
    plan_run = await orchestrator.resume(
        plan_run_id=args.plan_run_id,
        approved=not args.reject,
        approved_by=args.approved_by,
        edited_arguments=edited_arguments,
    )
    print(json.dumps(plan_run.to_dict(), ensure_ascii=False, indent=2))
    print(f"\nplan_run_id={plan_run.plan_run_id}  status={plan_run.status}  steps={len(plan_run.step_runs)}")


async def run_show_session(args: argparse.Namespace) -> None:
    configure_provider(args)
    runtime = build_runtime()
    if runtime.session_store is None:
        raise ValueError("session_store is not configured")
    messages = await runtime.session_store.load_messages(args.session_id)
    for message in messages:
        print(json.dumps({"role": message.role, "content": message.content}, ensure_ascii=False))


async def run_plan(args: argparse.Namespace) -> None:
    configure_provider(args)
    llm = build_llm()
    planner = Planner(llm=llm)
    store = PlanStore(PLANS_DIR)
    plan = await planner.create_plan(goal=args.goal, context=args.context or "")
    store.save(plan)
    print(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2))
    print(f"\nplan_id={plan.plan_id}  steps={len(plan.steps)}  saved to {PLANS_DIR / plan.plan_id}.json")


async def run_show_memory(args: argparse.Namespace) -> None:
    configure_provider(args)
    runtime = build_runtime()
    if runtime.memory_manager is None:
        raise ValueError("memory_manager is not configured")
    memories = await runtime.memory_manager.store.list_recent(args.user_id, limit=args.limit)
    for memory in memories:
        print(json.dumps({"content": memory.content, "tags": memory.tags, "metadata": memory.metadata}, ensure_ascii=False))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Minimal agent loop harness demo")
    subparsers = parser.add_subparsers(dest="command", required=True)

    chat_parser = subparsers.add_parser("chat", help="Run one turn or an interactive multi-turn chat")
    chat_parser.add_argument("--provider", choices=["mock", "openai_compatible"], default="mock")
    chat_parser.add_argument("--user-id", default="demo-user")
    chat_parser.add_argument("--session-id", default="demo-session")
    chat_parser.add_argument("message", nargs="?")

    orchestrate_parser = subparsers.add_parser("orchestrate", help="Plan then sequentially execute a goal")
    orchestrate_parser.add_argument("--provider", choices=["mock", "openai_compatible"], default="openai_compatible")
    orchestrate_parser.add_argument("goal")
    orchestrate_parser.add_argument("--user-id", default="demo-user")
    orchestrate_parser.add_argument("--context", default="", help="Optional background context")

    approve_parser = subparsers.add_parser("approve", help="Approve or reject a paused plan run and continue execution")
    approve_parser.add_argument("--provider", choices=["mock", "openai_compatible"], default="openai_compatible")
    approve_parser.add_argument("plan_run_id")
    approve_parser.add_argument("--user-id", default="demo-user")
    approve_parser.add_argument("--approved-by", default="human")
    approve_parser.add_argument("--edited-arguments", default="", help="Optional JSON object to override pending tool arguments")
    approve_parser.add_argument("--reject", action="store_true", help="Reject the pending approval instead of approving it")

    show_session_parser = subparsers.add_parser("show-session", help="Print persisted session messages")
    show_session_parser.add_argument("--provider", choices=["mock", "openai_compatible"], default="mock")
    show_session_parser.add_argument("--session-id", default="demo-session")

    show_memory_parser = subparsers.add_parser("show-memory", help="Print remembered run summaries")
    show_memory_parser.add_argument("--provider", choices=["mock", "openai_compatible"], default="mock")
    show_memory_parser.add_argument("--user-id", default="demo-user")
    show_memory_parser.add_argument("--limit", type=int, default=5)

    plan_parser = subparsers.add_parser("plan", help="Generate a task plan without executing any tools")
    plan_parser.add_argument("--provider", choices=["mock", "openai_compatible"], default="openai_compatible")
    plan_parser.add_argument("goal", help="The goal to decompose into steps")
    plan_parser.add_argument("--context", default="", help="Optional background context for the planner")

    return parser


def main() -> None:
    args = build_parser().parse_args()
    handlers = {
        "chat": run_chat,
        "orchestrate": run_orchestrate,
        "approve": run_approve,
        "show-session": run_show_session,
        "show-memory": run_show_memory,
        "plan": run_plan,
    }
    asyncio.run(handlers[args.command](args))


if __name__ == "__main__":
    main()
