"""OpenAI、Claude、Gemini への接続処理をまとめます。"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable

from anthropic import Anthropic
from google import genai
from openai import OpenAI
from pydantic import BaseModel

from config import (
    ANTHROPIC_MAX_TOKENS,
    ANTHROPIC_MODEL,
    COMPARE_MAX_OUTPUT_TOKENS,
    COMPARE_MODEL,
    GEMINI_MAX_OUTPUT_TOKENS,
    GEMINI_MODEL,
    OPENAI_MAX_OUTPUT_TOKENS,
    OPENAI_MODEL,
)
from prompts import COMMON_SYSTEM_PROMPT, COMPARISON_SYSTEM_PROMPT, build_comparison_input, build_conversation_input
from usage_data import UsageRecord, read_usage


logger = logging.getLogger(__name__)


class PerAIText(BaseModel):
    """AIごとの短い整理文です。回答がないAIは空文字にします。"""

    chatgpt: str
    claude: str
    gemini: str


class ComparisonAnalysis(BaseModel):
    """比較AIから受け取る構造化結果です。"""

    question_summary: str
    answer_summaries: PerAIText
    common_points: list[str]
    differences: list[str]
    unique_views: PerAIText
    assumptions: list[str]
    next_questions: list[str]


@dataclass
class AIResult:
    """画面に表示する、1社分の問い合わせ結果です。"""

    name: str
    answer: str | None = None
    error: str | None = None
    analysis: ComparisonAnalysis | None = None
    usage: UsageRecord | None = None

    @property
    def succeeded(self) -> bool:
        return bool(self.answer) and not self.error


def _missing_key_result(display_name: str, provider_name: str) -> AIResult:
    """画面の表示名と、API提供元の名称を分けて扱う。"""
    return AIResult(name=display_name, error=f"{provider_name} APIキーが設定されていません")


def _safe_error(error: Exception, api_key: str = "") -> str:
    """APIキーを伏せ、デバッグに必要な例外情報は残す。"""
    error_type = error.__class__.__name__
    status = getattr(error, "status_code", None) or getattr(error, "code", None)
    message = str(error).strip() or error_type
    if api_key:
        message = message.replace(api_key, "[REDACTED]")
    status_text = str(status) if status is not None else "取得できませんでした"
    return f"エラー種別: {error_type}\nHTTPステータス: {status_text}\nAPIメッセージ: {message[:2_000]}"


def ask_openai(question: str, api_key: str) -> AIResult:
    if not api_key:
        return _missing_key_result("ChatGPT", "OpenAI")
    usage = UsageRecord(provider="ChatGPT", model=OPENAI_MODEL)
    try:
        client = OpenAI(api_key=api_key)
        response = client.responses.create(
            model=OPENAI_MODEL,
            instructions=COMMON_SYSTEM_PROMPT,
            input=question,
            max_output_tokens=OPENAI_MAX_OUTPUT_TOKENS,
        )
        usage = read_usage(usage, getattr(response, "usage", None))
        incomplete_reason = getattr(response.incomplete_details, "reason", None)
        if incomplete_reason == "max_output_tokens":
            logger.warning("OpenAI output truncated because of token limit")
        answer = response.output_text.strip()
        if not answer:
            raise RuntimeError("空の回答が返されました")
        return AIResult(name="ChatGPT", answer=answer, usage=usage)
    except Exception as error:  # APIごとの例外を一つの表示形式にそろえる
        details = _safe_error(error, api_key)
        logger.error("OpenAI API error\n%s", details)
        return AIResult(name="ChatGPT", error=details, usage=usage)


def ask_anthropic(question: str, api_key: str) -> AIResult:
    if not api_key:
        return _missing_key_result("Claude", "Claude")
    usage = UsageRecord(provider="Claude", model=ANTHROPIC_MODEL)
    try:
        client = Anthropic(api_key=api_key)
        response = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=ANTHROPIC_MAX_TOKENS,
            system=COMMON_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": question}],
        )
        usage = read_usage(usage, getattr(response, "usage", None))
        if response.stop_reason == "max_tokens":
            logger.warning("Claude output truncated because of token limit")
        answer = "".join(block.text for block in response.content if block.type == "text").strip()
        if not answer:
            raise RuntimeError("空の回答が返されました")
        return AIResult(name="Claude", answer=answer, usage=usage)
    except Exception as error:
        details = _safe_error(error, api_key)
        logger.error("Claude API error\n%s", details)
        return AIResult(name="Claude", error=details, usage=usage)


def ask_gemini(question: str, api_key: str) -> AIResult:
    if not api_key:
        return _missing_key_result("Gemini", "Gemini")
    usage = UsageRecord(provider="Gemini", model=GEMINI_MODEL)
    try:
        with genai.Client(api_key=api_key) as client:
            interaction = client.interactions.create(
                model=GEMINI_MODEL,
                input=question,
                system_instruction=COMMON_SYSTEM_PROMPT,
                generation_config={"max_output_tokens": GEMINI_MAX_OUTPUT_TOKENS},
            )
        usage = read_usage(usage, getattr(interaction, "usage", None))
        interaction_status = getattr(interaction.status, "value", interaction.status)
        if interaction_status == "incomplete":
            logger.warning("Gemini output truncated because of token limit")
        answer = (interaction.output_text or "").strip()
        if not answer:
            raise RuntimeError(
                f"空の回答が返されました。status={interaction_status}, errors={interaction.errors}"
            )
        return AIResult(name="Gemini", answer=answer, usage=usage)
    except Exception as error:
        details = _safe_error(error, api_key)
        logger.error("Gemini API error\n%s", details)
        return AIResult(name="Gemini", error=details, usage=usage)


def ask_all(question: str, keys: dict[str, str], turns: list | None = None) -> dict[str, AIResult]:
    """3社への問い合わせを同時に始め、遅いAPIが他を止めないようにする。"""
    inputs = {name: build_conversation_input(question, turns or [], name) for name in ("ChatGPT", "Claude", "Gemini")}
    jobs: dict[str, Callable[[], AIResult]] = {
        "ChatGPT": lambda: ask_openai(inputs["ChatGPT"], keys.get("OPENAI_API_KEY", "")),
        "Claude": lambda: ask_anthropic(inputs["Claude"], keys.get("ANTHROPIC_API_KEY", "")),
        "Gemini": lambda: ask_gemini(inputs["Gemini"], keys.get("GEMINI_API_KEY", "")),
    }
    results: dict[str, AIResult] = {}
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(job): name for name, job in jobs.items()}
        for future in as_completed(futures):
            name = futures[future]
            try:
                results[name] = future.result()
            except Exception as error:  # 想定外でも、ほかの回答を残す
                results[name] = AIResult(name=name, error=_safe_error(error))
    return results


def compare_answers(question: str, results: dict[str, AIResult], api_key: str) -> AIResult:
    """成功した2件以上の回答だけを、OpenAI に比較してもらう。"""
    answers = {name: result.answer for name, result in results.items() if result.succeeded and result.answer}
    if len(answers) < 2:
        return AIResult(name="比較", error="比較には2つ以上のAI回答が必要です")
    if not api_key:
        return AIResult(name="比較", error="比較分析用の OpenAI APIキーが設定されていません")
    usage = UsageRecord(provider="ChatGPT", model=COMPARE_MODEL)
    try:
        client = OpenAI(api_key=api_key)
        response = client.responses.parse(
            model=COMPARE_MODEL,
            instructions=COMPARISON_SYSTEM_PROMPT,
            input=build_comparison_input(question, answers),
            max_output_tokens=COMPARE_MAX_OUTPUT_TOKENS,
            text_format=ComparisonAnalysis,
        )
        usage = read_usage(usage, getattr(response, "usage", None))
        answer = response.output_text.strip()
        if not answer:
            raise RuntimeError("比較結果が空でした")
        return AIResult(name="比較", answer=answer, analysis=response.output_parsed, usage=usage)
    except Exception as error:
        return AIResult(name="比較", error=f"比較結果を取得できませんでした: {_safe_error(error, api_key)}", usage=usage)
