"""APIが返した使用量を記録する。残高や料金は推定しない。"""

from datetime import datetime, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field


PROVIDERS = ("ChatGPT", "Claude", "Gemini")


class UsageRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    recorded_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    provider: str
    model: str
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)


def _count(raw, field: str) -> int | None:
    value = raw.get(field) if isinstance(raw, dict) else getattr(raw, field, None)
    return value if type(value) is int and value >= 0 else None


def read_usage(record: UsageRecord, raw) -> UsageRecord:
    """思考・キャッシュを二重加算せず、各SDKの正式な項目を読む。"""
    if raw is None:
        return record
    if record.provider == "Gemini":
        incoming = _count(raw, "total_input_tokens")
        outgoing = _count(raw, "total_output_tokens")
        total = _count(raw, "total_tokens")  # 思考などを含むAPI提供の合計を優先
    else:
        incoming = _count(raw, "input_tokens")
        outgoing = _count(raw, "output_tokens")
        if record.provider == "Claude":
            # Claudeではキャッシュ入力がinput_tokensとは別枠。
            if incoming is not None:
                incoming += (_count(raw, "cache_creation_input_tokens") or 0)
                incoming += (_count(raw, "cache_read_input_tokens") or 0)
            total = incoming + outgoing if incoming is not None and outgoing is not None else None
        else:
            total = _count(raw, "total_tokens")
    return record.model_copy(update={"input_tokens": incoming, "output_tokens": outgoing, "total_tokens": total})


def turn_usage(turn: dict) -> list[dict]:
    results = list(turn["results"].values())
    if turn.get("comparison"):
        results.append(turn["comparison"])
    return [result.usage.model_dump(mode="json") for result in results if result.usage is not None]


def month_start(now: datetime) -> datetime:
    local = now.astimezone(ZoneInfo("Asia/Tokyo"))
    return local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def summarize_usage(events: list[dict], now: datetime) -> dict:
    """保存コピーや再読み込みで同じAPI実行を重複加算しない。"""
    totals = {name: {"total": 0, "measured": 0, "unknown": 0} for name in PROVIDERS}
    month = month_start(now)
    seen = set()
    for event in events:
        try:
            record = UsageRecord.model_validate(event)
            when = datetime.fromisoformat(record.recorded_at).astimezone(ZoneInfo("Asia/Tokyo"))
        except (ValueError, TypeError):
            continue
        if record.id in seen or record.provider not in totals or (when.year, when.month) != (month.year, month.month):
            continue
        seen.add(record.id)
        bucket = totals[record.provider]
        if record.total_tokens is None:
            bucket["unknown"] += 1
        else:
            bucket["total"] += record.total_tokens
            bucket["measured"] += 1
    return totals
