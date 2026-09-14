"""Supabaseへ質問と回答を保存し、履歴として復元します。"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from supabase import Client, create_client

from ai_clients import AIResult, ComparisonAnalysis
from usage_data import UsageRecord, turn_usage


HISTORY_TABLE = "ai_history"


def make_client(url: str, secret_key: str) -> Client:
    """Streamlit Secretsに保存したサーバー用キーで接続する。"""
    return create_client(url, secret_key)


def _serialize_result(result: AIResult | None) -> dict[str, Any] | None:
    if result is None:
        return None
    return {
        "name": result.name,
        "answer": result.answer,
        "error": result.error,
        "analysis": result.analysis.model_dump(mode="json") if result.analysis else None,
        "usage": result.usage.model_dump(mode="json") if result.usage else None,
    }


def _deserialize_result(data: Any) -> AIResult | None:
    if not isinstance(data, dict):
        return None
    analysis_data = data.get("analysis")
    analysis = (
        ComparisonAnalysis.model_validate(analysis_data)
        if isinstance(analysis_data, dict)
        else None
    )
    try:
        usage = UsageRecord.model_validate(data["usage"]) if data.get("usage") else None
    except ValueError:
        usage = None  # 使用量が不正でも回答原文を開けるようにする
    return AIResult(
        name=str(data.get("name") or "AI"),
        answer=data.get("answer") if isinstance(data.get("answer"), str) else None,
        error=data.get("error") if isinstance(data.get("error"), str) else None,
        analysis=analysis,
        usage=usage,
    )


def save_history(
    client: Client,
    question: str,
    results: dict[str, AIResult],
    comparison: AIResult | None,
    *,
    turns: list[dict] | None = None,
    conversation_id: str | None = None,
    record_id: str | None = None,
) -> str:
    """1回分の質問、3回答、比較結果をまとめて保存する。"""
    question_summary = ""
    if comparison and comparison.analysis:
        question_summary = comparison.analysis.question_summary

    payload = {
        "id": record_id or str(uuid4()),
        "question": question,
        "question_summary": question_summary,
        "results": {name: _serialize_result(result) for name, result in results.items()},
        "comparison": _serialize_result(comparison),
    }
    if turns:
        payload["results"]["_conversation_id"] = conversation_id or payload["id"]
        payload["results"]["_turns"] = [
            {
                "question": turn["question"],
                "results": {name: _serialize_result(result) for name, result in turn["results"].items()},
                "comparison": _serialize_result(turn.get("comparison")),
            }
            for turn in turns
        ]
    current_turn = turns[-1] if turns else {"results": results, "comparison": comparison}
    payload["results"]["_usage_events"] = turn_usage(current_turn)
    try:
        client.table(HISTORY_TABLE).insert(payload).execute()
    except Exception as error:
        # 通信切断後に保存を再試行しても同じ回を重複登録しない。
        if str(getattr(error, "code", "")) != "23505":
            raise
        existing = get_history(client, payload["id"])
        if not existing or any(existing.get(key) != value for key, value in payload.items()):
            raise
    return payload["id"]


def list_usage(client: Client, since: str) -> list[dict]:
    """質問・回答本文を取得せず、今月の各API実行の使用量だけ読む。"""
    events = []
    offset = 0
    while True:
        response = (
            client.table(HISTORY_TABLE)
            .select("usage_events:results->_usage_events")
            .gte("created_at", since)
            .order("created_at").order("id")
            .range(offset, offset + 99)
            .execute()
        )
        rows = response.data or []
        for row in rows:
            if isinstance(row.get("usage_events"), list):
                events.extend(row["usage_events"])
        if len(rows) < 100:
            return events
        offset += 100


def list_history(client: Client, limit: int = 20) -> list[dict[str, Any]]:
    """一覧は新しい20件の軽量なメタデータだけを、1回で取得する。"""
    response = (
        client.table(HISTORY_TABLE)
        .select("id,created_at,question_summary,conversation_id:results->>_conversation_id")
        .order("created_at", desc=True)
        .order("id", desc=True)
        .range(0, limit - 1)
        .execute()
    )
    # 取得するのは最大20行の小さなメタデータだけ。会話の保存時点の重複をここで隠す。
    items, seen = [], set()
    for row in response.data or []:
        conversation_id = row.get("conversation_id") or row["id"]
        if conversation_id not in seen:
            seen.add(conversation_id)
            items.append(row)
    return items


def get_history(client: Client, history_id: str) -> dict[str, Any] | None:
    """選択された1件だけ、回答全文を含めて取得する。"""
    response = (
        client.table(HISTORY_TABLE)
        .select("id,created_at,question,question_summary,results,comparison")
        .eq("id", history_id)
        .limit(1)
        .execute()
    )
    rows = response.data or []
    return rows[0] if rows else None


def restore_history(
    record: dict[str, Any],
) -> tuple[str, dict[str, AIResult], AIResult | None]:
    """DBのJSONを既存画面がそのまま表示できる型へ戻す。"""
    question = record.get("question")
    if not isinstance(question, str) or not question.strip():
        raise ValueError("履歴の質問本文がありません")

    raw_results = record.get("results")
    if not isinstance(raw_results, dict):
        raise ValueError("履歴のAI回答が不正です")

    results: dict[str, AIResult] = {}
    for name in ("ChatGPT", "Claude", "Gemini"):
        result = _deserialize_result(raw_results.get(name))
        if result:
            results[name] = result

    if not results:
        raise ValueError("履歴に表示できるAI回答がありません")

    comparison = _deserialize_result(record.get("comparison"))
    return question, results, comparison


def restore_conversation(record: dict[str, Any]) -> tuple[str, list[dict]]:
    """以前の単発履歴も1往復の相談として開ける。"""
    raw_results = record.get("results") or {}
    raw_turns = raw_results.get("_turns")
    if raw_turns is None:
        raw_turns = [record]
    if not isinstance(raw_turns, list) or not raw_turns:
        raise ValueError("会話履歴の形式が不正です")
    turns = []
    for raw_turn in raw_turns:
        question, results, comparison = restore_history(raw_turn)
        turns.append({"question": question, "results": results, "comparison": comparison})
    return str(raw_results.get("_conversation_id") or record["id"]), turns
