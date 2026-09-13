"""同じ画面にAI別の使用量と任意の目安メーターを表示する。"""

from datetime import datetime, timezone

import streamlit as st

from history_store import list_usage
from usage_data import PROVIDERS, month_start, summarize_usage


@st.cache_data(ttl=60, show_spinner=False)
def load_usage(url: str, secret_key: str, since: str, _client) -> list[dict]:
    # 接続そのものを除外し、接続先とキーをキャッシュの識別に使う。
    return list_usage(_client, since)


def show_usage_panel(client, url: str, secret_key: str, local_events: list[dict]) -> None:
    now = datetime.now(timezone.utc)
    with st.expander("AIの使用量", expanded=True):
        st.caption(f"{month_start(now):%Y年%m月}・このアプリで記録した分（日本時間）")
        events = []
        if client is not None:
            try:
                events = load_usage(url, secret_key, month_start(now).isoformat(), client)
            except Exception:
                st.warning("保存済みの使用量を読み込めません。表示は現在の画面で記録できた分のみです。")
        else:
            st.caption("保存済みの使用量は未接続です。現在の画面で記録できた分を表示します。")
        totals = summarize_usage(events + local_events, now)

        with st.expander("月の目安を設定（任意）"):
            st.caption("トークンはAIが読み書きした量の単位です。目安はこの画面を開いている間だけ保持します。0は未設定です。")
            st.caption("各社の契約上限・残高とは別の目安です。超えても自動停止しません。")
            targets = {
                name: st.number_input(
                    f"{name}：今月の目安（トークン）", min_value=0, value=0,
                    step=100_000, key=f"usage_target_{name}",
                ) for name in PROVIDERS
            }

        for name in PROVIDERS:
            bucket = totals[name]
            total, target = bucket["total"], targets[name]
            st.metric(name, f"{total:,} トークン" if bucket["measured"] else "未計測")
            if target and bucket["measured"]:
                percent = total / target * 100
                st.progress(min(total / target, 1.0), text=f"目安 {target:,} に対して {percent:.1f}%")
                if total > target:
                    st.caption(f"目安を {total - target:,} トークン超えています。")
                else:
                    st.caption(f"目安まであと {target - total:,} トークン")
            else:
                st.caption("メーターは目安の設定と使用量の取得後に表示します。")
            if bucket["unknown"]:
                st.caption(f"使用量を取得できなかった実行：{bucket['unknown']}件（合計には含めていません）")

        st.caption("ChatGPTは回答・要約・比較を含みます。この機能追加後に取得できた使用量を集計します。過去の未計測分や他アプリの使用量、APIの再試行分などは含まれない場合があります。")
        st.caption("料金・実際の残高は各社の管理画面で確認してください。トークン単価はAIごとに異なります。")
