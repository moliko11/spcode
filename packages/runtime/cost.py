from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


MODEL_PRICING: dict[str, dict[str, float]] = {
    "qwen3": {"input_per_1m": 0.0, "output_per_1m": 0.0, "note": "本地模型免费"},
    "qwen3.5-plus": {"input_per_1m": 0.8, "output_per_1m": 2.0},
    "qwen3.5-max": {"input_per_1m": 2.0, "output_per_1m": 6.0},
    "deepseek-chat": {"input_per_1m": 1.0, "output_per_1m": 2.0},
    "deepseek-v4-pro": {"input_per_1m": 2.0, "output_per_1m": 8.0},
    "deepseek-reasoner": {"input_per_1m": 4.0, "output_per_1m": 16.0},
}

CNY_TO_USD = 0.14


@dataclass
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0

    def __add__(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
        )


@dataclass
class CostRecord:
    model_name: str
    usage: TokenUsage
    cost_cny: float = 0.0
    cost_usd: float = 0.0


@dataclass
class CostTracker:
    records: list[CostRecord] = field(default_factory=list)

    def add(self, model_name: str, usage: TokenUsage) -> CostRecord:
        pricing = MODEL_PRICING.get(model_name, {"input_per_1m": 0.0, "output_per_1m": 0.0})
        cost_cny = (usage.input_tokens / 1_000_000) * pricing["input_per_1m"] + (usage.output_tokens / 1_000_000) * pricing["output_per_1m"]
        cost_usd = cost_cny * CNY_TO_USD
        record = CostRecord(model_name=model_name, usage=usage, cost_cny=cost_cny, cost_usd=cost_usd)
        self.records.append(record)
        return record

    def total(self) -> dict[str, Any]:
        total_usage = TokenUsage()
        total_cny = 0.0
        total_usd = 0.0
        model_names: set[str] = set()
        for r in self.records:
            total_usage = total_usage + r.usage
            total_cny += r.cost_cny
            total_usd += r.cost_usd
            if r.model_name:
                model_names.add(r.model_name)
        return {
            "input_tokens": total_usage.input_tokens,
            "output_tokens": total_usage.output_tokens,
            "total_tokens": total_usage.total_tokens,
            "cost_cny": round(total_cny, 6),
            "cost_usd": round(total_usd, 6),
            "model_calls": len(self.records),
            "model_name": ",".join(sorted(model_names)) if model_names else "unknown",
        }

    def snapshot(self) -> dict[str, Any]:
        return self.total()

    def format_summary(self) -> str:
        t = self.total()
        if t["total_tokens"] == 0:
            return ""
        pricing = MODEL_PRICING.get(t.get("model_name", ""), {})
        is_free = pricing.get("input_per_1m", 0) == 0 and pricing.get("output_per_1m", 0) == 0
        lines = [
            f"💰 token用量: input={t['input_tokens']}, output={t['output_tokens']}, total={t['total_tokens']}",
        ]
        if is_free:
            lines.append(f"💰 花费: 本地模型免费 ({t['model_calls']}次调用)")
        else:
            lines.append(f"💰 花费: ¥{t['cost_cny']:.6f} / ${t['cost_usd']:.6f} ({t['model_calls']}次调用)")
        return "\n".join(lines)


def extract_usage_from_response(response: Any) -> TokenUsage:
    usage_meta = getattr(response, "usage_metadata", None)
    if usage_meta is not None:
        if isinstance(usage_meta, dict):
            return TokenUsage(
                input_tokens=int(usage_meta.get("input_tokens", 0) or 0),
                output_tokens=int(usage_meta.get("output_tokens", 0) or 0),
                total_tokens=int(usage_meta.get("total_tokens", 0) or 0),
            )
        if hasattr(usage_meta, "input_tokens"):
            try:
                return TokenUsage(
                    input_tokens=int(getattr(usage_meta, "input_tokens", 0) or 0),
                    output_tokens=int(getattr(usage_meta, "output_tokens", 0) or 0),
                    total_tokens=int(getattr(usage_meta, "total_tokens", 0) or 0),
                )
            except (TypeError, ValueError):
                pass
    response_metadata = getattr(response, "response_metadata", None)
    if isinstance(response_metadata, dict):
        token_usage = response_metadata.get("token_usage") or response_metadata.get("usage")
        if isinstance(token_usage, dict):
            try:
                return TokenUsage(
                    input_tokens=int(token_usage.get("prompt_tokens", 0) or token_usage.get("input_tokens", 0) or 0),
                    output_tokens=int(token_usage.get("completion_tokens", 0) or token_usage.get("output_tokens", 0) or 0),
                    total_tokens=int(token_usage.get("total_tokens", 0) or 0),
                )
            except (TypeError, ValueError):
                pass
    return TokenUsage()
