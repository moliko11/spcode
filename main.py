from __future__ import annotations

import argparse
import asyncio
import json
import os

from packages.runtime.bootstrap import build_runtime


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
    result = await runtime.orchestrate(
        user_id=args.user_id,
        session_id=args.session_id,
        goal=args.goal,
        max_turns=args.max_turns,
    )
    for turn in result.turns:
        print(f"[turn {turn.turn_index}] {turn.output}")
    print(json.dumps({"completed": result.completed, "final_output": result.final_output}, ensure_ascii=False, indent=2))


async def run_show_session(args: argparse.Namespace) -> None:
    configure_provider(args)
    runtime = build_runtime()
    if runtime.session_store is None:
        raise ValueError("session_store is not configured")
    messages = await runtime.session_store.load_messages(args.session_id)
    for message in messages:
        print(json.dumps({"role": message.role, "content": message.content}, ensure_ascii=False))


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

    orchestrate_parser = subparsers.add_parser("orchestrate", help="Run autonomous multi-turn orchestration")
    orchestrate_parser.add_argument("--provider", choices=["mock", "openai_compatible"], default="mock")
    orchestrate_parser.add_argument("goal")
    orchestrate_parser.add_argument("--user-id", default="demo-user")
    orchestrate_parser.add_argument("--session-id", default="demo-session")
    orchestrate_parser.add_argument("--max-turns", type=int, default=3)

    show_session_parser = subparsers.add_parser("show-session", help="Print persisted session messages")
    show_session_parser.add_argument("--provider", choices=["mock", "openai_compatible"], default="mock")
    show_session_parser.add_argument("--session-id", default="demo-session")

    show_memory_parser = subparsers.add_parser("show-memory", help="Print remembered run summaries")
    show_memory_parser.add_argument("--provider", choices=["mock", "openai_compatible"], default="mock")
    show_memory_parser.add_argument("--user-id", default="demo-user")
    show_memory_parser.add_argument("--limit", type=int, default=5)

    return parser


def main() -> None:
    args = build_parser().parse_args()
    handlers = {
        "chat": run_chat,
        "orchestrate": run_orchestrate,
        "show-session": run_show_session,
        "show-memory": run_show_memory,
    }
    asyncio.run(handlers[args.command](args))


if __name__ == "__main__":
    main()
