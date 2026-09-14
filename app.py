"""複数のAIへ同じ質問を送り、回答の違いを読むためのStreamlitアプリです。"""

from __future__ import annotations

import hmac
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from uuid import uuid4

import streamlit as st

from ai_clients import AIResult, ComparisonAnalysis, ask_all, compare_answers
from history_store import get_history, list_history, make_client, restore_conversation, save_history
from prompts import build_conversation_input
from usage_data import turn_usage
from usage_panel import load_usage, show_usage_panel


APP_ICON = str(Path(__file__).parent / "app_icon.png")

st.set_page_config(page_title="AI壁打ち", page_icon=APP_ICON, layout="centered")

# スマートフォンで読みやすい余白と、押しやすいボタンだけを追加します。
st.markdown(
    """
    <style>
      .block-container { max-width: 760px; padding-top: 2rem; padding-bottom: 3rem; }
      div.stButton > button { width: 100%; min-height: 3.1rem; font-size: 1.05rem; font-weight: 600; }
      div[data-testid="stExpander"] details { overflow-wrap: anywhere; }
      @media (max-width: 640px) {
        .block-container { padding: 1.2rem 1rem 2.5rem; }
        h1 { font-size: 1.75rem; }
      }
    </style>
    """,
    unsafe_allow_html=True,
)


def get_secret(name: str) -> str:
    """未設定のキーでもアプリが停止しないよう、安全に取り出す。"""
    try:
        return str(st.secrets.get(name, ""))
    except Exception:
        return ""


def initialize_state() -> None:
    """再描画しても直近の質問と回答を残す。"""
    defaults = {
        "question": "",
        "question_input": "",
        "results": {},
        "comparison": None,
        "authenticated": False,
        "history_notice": "",
        "turns": [],
        "conversation_id": str(uuid4()),
        "pending_save_id": None,
        "save_error": "",
        "composer_version": 0,
        "usage_events": [],
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value
    # 更新直前に開いていた単発の結果も失わない。
    if not st.session_state["turns"] and st.session_state["results"]:
        st.session_state["turns"] = [{
            "question": st.session_state["question"],
            "results": st.session_state["results"],
            "comparison": st.session_state["comparison"],
        }]
        st.session_state["results"] = {}


def require_password() -> None:
    """Secretsの暗証番号を確認できるまで本体を表示しない。"""
    expected_password = get_secret("APP_PASSWORD")

    if not expected_password:
        st.error("APP_PASSWORD が設定されていません。")
        st.caption(
            ".streamlit/secrets.toml または Streamlit Community Cloud の Secrets に設定してください。"
        )
        st.stop()

    if st.session_state["authenticated"]:
        return

    st.title("AI壁打ち")
    st.caption("暗証番号を入力してください。")
    entered_password = st.text_input("暗証番号", type="password")

    if st.button("開く", type="primary"):
        if hmac.compare_digest(entered_password, expected_password):
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("暗証番号が違います。")

    st.stop()


@st.cache_resource(show_spinner=False)
def get_history_client(url: str, secret_key: str):
    """再描画のたびにSupabase接続を作り直さない。"""
    return make_client(url, secret_key)


@st.cache_data(ttl=30, show_spinner=False)
def get_history_list(url: str, secret_key: str) -> list[dict]:
    """履歴一覧は30秒だけ保持し、画面操作ごとの通信を減らす。"""
    return list_history(get_history_client(url, secret_key))


def safe_history_error(error: Exception, secret_key: str) -> str:
    """Supabaseの秘密鍵を伏せて、設定確認に必要な範囲だけ表示する。"""
    message = str(error).strip() or error.__class__.__name__
    if secret_key:
        message = message.replace(secret_key, "[REDACTED]")
    return f"{error.__class__.__name__}: {message[:500]}"


def format_history_label(item: dict) -> str:
    """スマートフォンでも日時と質問の冒頭が読める表示名にする。"""
    created_at = str(item.get("created_at") or "")
    try:
        parsed = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        date_text = parsed.astimezone(ZoneInfo("Asia/Tokyo")).strftime("%Y/%m/%d %H:%M")
    except ValueError:
        date_text = "日時不明"

    text = str(item.get("question_summary") or "質問の要約なし")
    compact = " ".join(text.split())
    if len(compact) > 42:
        compact = f"{compact[:42]}…"
    return f"{date_text}　{compact}"


def show_history_panel():
    """履歴一覧を表示し、選んだ1件を既存の回答画面へ復元する。"""
    url = get_secret("SUPABASE_URL")
    secret_key = get_secret("SUPABASE_SECRET_KEY")

    with st.expander("過去の質問を見る", expanded=False):
        if not url or not secret_key:
            st.caption("Supabaseを設定すると、アプリを閉じた後も履歴が残ります。")
            return None

        try:
            client = get_history_client(url, secret_key)
            items = get_history_list(url, secret_key)
        except Exception as error:
            st.warning(f"履歴を読み込めませんでした。{safe_history_error(error, secret_key)}")
            return None

        if not items:
            st.caption("保存された質問はまだありません。")
            return client

        labels = {str(item["id"]): format_history_label(item) for item in items}
        if st.session_state.get("selected_history_id", "") not in labels:
            st.session_state["selected_history_id"] = ""
        selected_id = st.selectbox(
            "見たい質問を選ぶ",
            options=[""] + list(labels),
            format_func=lambda value: "選択してください" if not value else labels[value],
            key="selected_history_id",
        )

        if st.button("この履歴を開く", disabled=not selected_id or bool(st.session_state["pending_save_id"]), key="open_history"):
            try:
                record = get_history(client, selected_id)
                if not record:
                    raise ValueError("選択した履歴が見つかりません")
                conversation_id, turns = restore_conversation(record)
                st.session_state["conversation_id"] = conversation_id
                st.session_state["turns"] = turns
                st.session_state["composer_version"] += 1
                st.session_state["save_error"] = ""
                st.session_state["history_notice"] = "相談を開きました。下の入力欄から続きを送れます。"
                st.rerun()
            except Exception as error:
                st.warning(f"履歴を開けませんでした。{safe_history_error(error, secret_key)}")

        return client


def show_answer(result: AIResult, summary: str = "") -> None:
    """成功・失敗どちらも、AIごとの場所に明確に表示する。"""
    st.subheader(result.name)
    if result.succeeded:
        st.markdown("**要約**")
        if summary:
            st.markdown(summary)
        else:
            st.caption("要約を取得できませんでした。回答全文を確認してください。")
        with st.expander("回答全文", expanded=False):
            st.markdown(result.answer)
    else:
        st.warning(f"{result.name}から回答を取得できませんでした")
        if result.error:
            with st.expander("開発確認用のエラー内容"):
                st.code(result.error, language=None)


def show_list(items: list[str]) -> None:
    """比較項目をスマートフォンで読みやすい箇条書きにする。"""
    for item in items:
        st.markdown(f"- {item}")


def show_comparison(analysis: ComparisonAnalysis) -> None:
    """構造化された比較結果を決められた順序で表示する。"""
    st.markdown("#### 共通している点")
    show_list(analysis.common_points)
    st.markdown("#### 意見が分かれた点")
    show_list(analysis.differences)
    st.markdown("#### 各AI特有の視点")
    for name, text in (
        ("ChatGPT", analysis.unique_views.chatgpt),
        ("Claude", analysis.unique_views.claude),
        ("Gemini", analysis.unique_views.gemini),
    ):
        if text:
            st.markdown(f"**{name}**  \n{text}")
    st.markdown("#### 前提の違い")
    show_list(analysis.assumptions)
    st.markdown("#### さらに考えてみる問い")
    show_list(analysis.next_questions)


def show_turn(turn: dict) -> None:
    """1回分の質問・各AI回答・比較を原文付きで表示する。"""
    comparison = turn["comparison"]
    analysis = comparison.analysis if comparison and comparison.succeeded else None

    st.divider()
    st.subheader("質問の要約")
    if analysis:
        st.markdown(analysis.question_summary)
    else:
        st.caption("要約を取得できませんでした。質問原文を確認してください。")

    with st.expander("質問原文", expanded=False):
        st.write(turn["question"])

    answer_summaries = analysis.answer_summaries if analysis else None
    for name, summary_key in (
        ("ChatGPT", "chatgpt"),
        ("Claude", "claude"),
        ("Gemini", "gemini"),
    ):
        result = turn["results"].get(name)
        if result:
            st.divider()
            summary = getattr(answer_summaries, summary_key, "") if answer_summaries else ""
            show_answer(result, summary)

    st.divider()
    st.subheader("回答の違い")
    if analysis:
        show_comparison(analysis)
    elif comparison and comparison.succeeded:
        st.markdown(comparison.answer)
    elif comparison:
        st.info(comparison.error)


def persist_conversation(client) -> None:
    """保存失敗時も会話を保持し、AIを呼ばず保存だけ再試行できる。"""
    turns = st.session_state["turns"]
    first = turns[0]
    try:
        if client is None:
            raise RuntimeError("履歴保存先に接続できません。設定・接続を確認して再試行してください。")
        save_history(
            client, first["question"], first["results"], first["comparison"],
            turns=turns,
            conversation_id=st.session_state["conversation_id"],
            record_id=st.session_state["pending_save_id"],
        )
        st.session_state["pending_save_id"] = None
        st.session_state["save_error"] = ""
        get_history_list.clear()
        load_usage.clear()
        st.session_state["history_notice"] = "今回のやりとりを履歴に保存しました。"
    except Exception as error:
        st.session_state["save_error"] = safe_history_error(error, get_secret("SUPABASE_SECRET_KEY"))


initialize_state()
require_password()

st.title("AI壁打ち")
st.caption("ひとつの問いを複数のAIに投げて、考える材料を集めます。")

history_client = show_history_panel()
show_usage_panel(
    history_client, get_secret("SUPABASE_URL"), get_secret("SUPABASE_SECRET_KEY"),
    st.session_state["usage_events"],
)

if st.session_state["history_notice"]:
    st.success(st.session_state["history_notice"])
    st.session_state["history_notice"] = ""

if st.session_state["pending_save_id"]:
    st.warning("履歴がまだ保存できていません。ページを閉じる前に保存を再試行してください。")
    if st.session_state["save_error"]:
        st.caption(st.session_state["save_error"])
    if st.button("履歴の保存を再試行"):
        persist_conversation(history_client)
        st.rerun()

turns = st.session_state["turns"]
if turns:
    if st.button("新しい相談", disabled=bool(st.session_state["pending_save_id"])):
        st.session_state["turns"] = []
        st.session_state["conversation_id"] = str(uuid4())
        st.session_state["composer_version"] += 1
        st.rerun()
    for index, turn in enumerate(turns):
        if index < len(turns) - 1:
            label = " ".join(turn["question"].split())[:60]
            with st.expander(f"{index + 1}回目：{label}", expanded=False):
                show_turn(turn)
        else:
            st.subheader(f"{index + 1}回目のやりとり")
            show_turn(turn)

st.divider()
if turns:
    st.subheader("この続きで考える")
    st.caption("これまでの質問と3AIの回答を全員が参照し、それぞれの視点で答えます。")

question = st.text_area(
    "さらに聞きたいこと・思いついたこと" if turns else "いま何について考えたい？",
    height=150 if turns else 190,
    placeholder="例：Claudeの提案が気になりました。初回90分で3万円にするとしたら？" if turns else "考えたいテーマを入力してください。",
    key=f"composer_{st.session_state['composer_version']}",
)
if st.button("3つのAIに続きを聞く" if turns else "3つのAIに聞く", type="primary", disabled=bool(st.session_state["pending_save_id"])):
    cleaned_question = question.strip()
    if not cleaned_question:
        st.warning("質問を入力してください")
    else:
        try:
            # 長すぎる場合は、API呼び出しや会話の変更前に停止する。
            for name in ("ChatGPT", "Claude", "Gemini"):
                build_conversation_input(cleaned_question, turns, name)
            comparison_input = build_conversation_input(cleaned_question, turns)
        except ValueError as error:
            st.warning(str(error))
        else:
            keys = {name: get_secret(name) for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY")}
            with st.spinner("3つのAIに聞いています……"):
                results = ask_all(cleaned_question, keys, turns)
            if sum(result.succeeded for result in results.values()) >= 2:
                with st.spinner("回答の違いを整理しています……"):
                    comparison = compare_answers(comparison_input, results, keys["OPENAI_API_KEY"])
            else:
                comparison = AIResult(name="回答の違い", error="比較には2つ以上のAI回答が必要です")
            st.session_state["turns"] = turns + [{"question": cleaned_question, "results": results, "comparison": comparison}]
            st.session_state["usage_events"].extend(turn_usage(st.session_state["turns"][-1]))
            st.session_state["composer_version"] += 1
            if get_secret("SUPABASE_URL") or get_secret("SUPABASE_SECRET_KEY"):
                st.session_state["pending_save_id"] = str(uuid4())
                persist_conversation(history_client)
            else:
                st.session_state["history_notice"] = "今回は画面内だけに保持しています。履歴保存にはSupabaseの設定が必要です。"
            st.rerun()
