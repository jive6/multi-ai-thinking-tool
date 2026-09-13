"""Supabaseへ質問と回答を保存し、履歴として復元します。"""

from __future__ import annotations

from typing import Any

from supabase import Client, create_client

from ai_clients import AIResult, ComparisonAnalysis


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
    return AIResult(
        name=str(data.get("name") or "AI"),
        answer=data.get("answer") if isinstance(data.get("answer"), str) else None,
        error=data.get("error") if isinstance(data.get("error"), str) else None,
        analysis=analysis,
    )


def save_history(
    client: Client,
    question: str,
    results: dict[str, AIResult],
    comparison: AIResult | None,
) -> None:
    """1回分の質問、3回答、比較結果をまとめて保存する。"""
    question_summary = ""
    if comparison and comparison.analysis:
        question_summary = comparison.analysis.question_summary

    payload = {
        "question": question,
        "question_summary": question_summary,
        "results": {name: _serialize_result(result) for name, result in results.items()},
        "comparison": _serialize_result(comparison),
    }
    client.table(HISTORY_TABLE).insert(payload).execute()


def list_history(client: Client, limit: int = 50) -> list[dict[str, Any]]:
    """一覧表示に必要な軽い項目だけを、新しい順に取得する。"""
    response = (
        client.table(HISTORY_TABLE)
        .select("id,created_at,question,question_summary")
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return response.data or []


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
