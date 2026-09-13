"""複数のAIへ同じ質問を送り、回答の違いを読むためのStreamlitアプリです。"""

from __future__ import annotations

import hmac

import streamlit as st

from ai_clients import AIResult, ComparisonAnalysis, ask_all, compare_answers


st.set_page_config(page_title="AI壁打ち", page_icon="💭", layout="centered")

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
    defaults = {"question": "", "results": {}, "comparison": None, "authenticated": False}
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


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


initialize_state()
require_password()

st.title("AI壁打ち")
st.caption("ひとつの問いを複数のAIに投げて、考える材料を集めます。")

question = st.text_area(
    "いま何について考えたい？",
    value=st.session_state.question,
    height=190,
    placeholder="たとえば、個人事業でサービスを増やすべきか、一つに絞るべきか？",
)

if st.button("3つのAIに聞く", type="primary"):
    cleaned_question = question.strip()
    if not cleaned_question:
        st.warning("質問を入力してください")
    else:
        st.session_state.question = cleaned_question
        keys = {
            "OPENAI_API_KEY": get_secret("OPENAI_API_KEY"),
            "ANTHROPIC_API_KEY": get_secret("ANTHROPIC_API_KEY"),
            "GEMINI_API_KEY": get_secret("GEMINI_API_KEY"),
        }
        with st.spinner("3つのAIに聞いています……"):
            st.session_state.results = ask_all(cleaned_question, keys)
        successful_count = sum(result.succeeded for result in st.session_state.results.values())
        if successful_count >= 2:
            with st.spinner("回答の違いを整理しています……"):
                st.session_state.comparison = compare_answers(
                    cleaned_question,
                    st.session_state.results,
                    keys["OPENAI_API_KEY"],
                )
        else:
            st.session_state.comparison = AIResult(
                name="回答の違い", error="比較には2つ以上のAI回答が必要です"
            )

if st.session_state.results:
    comparison = st.session_state.comparison
    analysis = comparison.analysis if comparison and comparison.succeeded else None

    st.divider()
    st.subheader("質問の要約")
    if analysis:
        st.markdown(analysis.question_summary)
    else:
        st.caption("要約を取得できませんでした。質問原文を確認してください。")

    with st.expander("質問原文", expanded=False):
        st.write(st.session_state.question)

    answer_summaries = analysis.answer_summaries if analysis else None
    for name, summary_key in (
        ("ChatGPT", "chatgpt"),
        ("Claude", "claude"),
        ("Gemini", "gemini"),
    ):
        result = st.session_state.results.get(name)
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
