__all__ = ["AgentRuntime", "build_runtime"]


def __getattr__(name: str):
	if name == "AgentRuntime":
		from .agent_loop import AgentRuntime

		return AgentRuntime
	if name == "build_runtime":
		from .bootstrap import build_runtime

		return build_runtime
	raise AttributeError(name)
