"""実際のAIや保存先に接続せず、会話共有と再開・保存失敗を確認する。"""

import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
import streamlit as st
from streamlit.testing.v1 import AppTest
from supabase import ClientOptions, create_client

import ai_clients
import history_store
from ai_clients import AIResult
from prompts import MAX_CONVERSATION_CHARS, build_conversation_input

APP = str(Path(__file__).resolve().parents[1] / "app.py")
NAMES = ("ChatGPT", "Claude", "Gemini")


def make_turn(question="初回の相談"):
    return {
        "question": question,
        "results": {name: AIResult(name=name, answer=f"{name}の独自の提案") for name in NAMES},
        "comparison": AIResult(name="比較", answer="3者の違い"),
    }


class Store:
    """公式SDKから来るHTTP要求を受ける、メモリ内の保存先。"""
    def __init__(self):
        self.rows = []
        self.fail_insert = False
        self.lose_response = False
        self.http = httpx.Client(transport=httpx.MockTransport(self.handle))
        self.client = create_client(
            "https://test.supabase.co", "sb_secret_test",
            options=ClientOptions(httpx_client=self.http, persist_session=False, auto_refresh_token=False),
        )

    def handle(self, request):
        assert request.url.path == "/rest/v1/ai_history"
        if request.method == "POST":
            if self.fail_insert:
                return httpx.Response(503, json={"message": "保存先が一時的に利用不可", "code": "503"})
            row = json.loads(request.content)
            if any(old["id"] == row["id"] for old in self.rows):
                return httpx.Response(409, json={"message": "duplicate", "code": "23505", "hint": None, "details": None})
            self.rows.insert(0, {**row, "created_at": "2026-09-14T10:00:00+00:00"})
            if self.lose_response:
                self.lose_response = False
                raise httpx.ReadTimeout("response lost", request=request)
            return httpx.Response(201, json=[self.rows[0]])
        params = request.url.params
        if "id" in params:
            return httpx.Response(200, json=[row for row in self.rows if "eq." + row["id"] == params["id"]])
        assert "conversation_id:results->>_conversation_id" in params["select"]
        offset, limit = int(params.get("offset", 0)), int(params.get("limit", 100))
        return httpx.Response(200, json=[{
            **{key: row[key] for key in ("id", "created_at", "question", "question_summary")},
            "conversation_id": row["results"].get("_conversation_id"),
        } for row in self.rows[offset:offset + limit]])


class ConversationTests(unittest.TestCase):
    def setUp(self):
        st.cache_data.clear()
        st.cache_resource.clear()
        self.store = Store()
        self.addCleanup(self.store.http.close)

    def save(self, turns, record_id="record-1", conversation_id="consultation-1"):
        first = turns[0]
        return history_store.save_history(
            self.store.client, first["question"], first["results"], first["comparison"],
            turns=turns, conversation_id=conversation_id, record_id=record_id,
        )

    def app(self):
        app = AppTest.from_file(APP, default_timeout=10)
        # 本物のSecretsをテスト用の値で置き換える。
        for name, value in {
            "APP_PASSWORD": "test-password", "SUPABASE_URL": "https://test.supabase.co",
            "SUPABASE_SECRET_KEY": "sb_secret_test", "OPENAI_API_KEY": "test",
            "ANTHROPIC_API_KEY": "test", "GEMINI_API_KEY": "test",
        }.items():
            app.secrets[name] = value
        app.run()
        app.text_input[0].set_value("test-password")
        app.button[0].click().run()
        self.assertFalse(app.exception)
        return app

    def click(self, app, label):
        next(button for button in app.button if button.label == label).click().run()
        self.assertFalse(app.exception)

    def test_all_providers_receive_each_others_prior_answers(self):
        turns = [make_turn()]
        with patch.object(ai_clients, "ask_openai", return_value=AIResult("ChatGPT", "a")) as a, \
             patch.object(ai_clients, "ask_anthropic", return_value=AIResult("Claude", "b")) as b, \
             patch.object(ai_clients, "ask_gemini", return_value=AIResult("Gemini", "c")) as c:
            ai_clients.ask_all("Claudeの提案を広げたい", {}, turns)
        for name, call in zip(NAMES, (a, b, c)):
            call.assert_called_once()
            text = call.call_args.args[0]
            self.assertIn(f"今回回答するAI: {name}", text)
            for other in NAMES:
                self.assertIn(f"{other}の独自の提案", text)
            self.assertIn("Claudeの提案を広げたい", text)

    def test_context_keeps_originals_and_excludes_error_details(self):
        turns = [make_turn()]
        turns[0]["results"]["Gemini"] = AIResult("Gemini", error="private diagnostic")
        before = copy.deepcopy(turns)
        self.assertNotIn("private diagnostic", build_conversation_input("続きを", turns))
        with self.assertRaises(ValueError):
            build_conversation_input("長" * MAX_CONVERSATION_CHARS, turns)
        self.assertEqual(turns, before)

    def test_snapshot_roundtrip_grouping_and_legacy_history(self):
        first = make_turn()
        history_store.save_history(self.store.client, first["question"], first["results"], first["comparison"], record_id="legacy")
        legacy = copy.deepcopy(self.store.rows[0])
        cid, old_turns = history_store.restore_conversation(legacy)
        self.assertEqual(cid, "legacy")
        turns = old_turns + [make_turn("続きを考える")]
        self.save(turns, conversation_id=cid)
        items = history_store.list_history(self.store.client)
        self.assertEqual(len(items), 1)
        record = history_store.get_history(self.store.client, items[0]["id"])
        self.assertEqual(history_store.restore_conversation(record), (cid, turns))
        self.assertEqual(self.store.rows[1], legacy)

    def test_lost_save_response_retries_without_duplicates(self):
        turns = [make_turn()]
        self.store.lose_response = True
        with self.assertRaises(httpx.ReadTimeout):
            self.save(turns)
        self.save(turns)
        self.assertEqual(len(self.store.rows), 1)

    def test_followup_new_session_resume_and_save_retry(self):
        with patch.object(history_store, "make_client", return_value=self.store.client), \
             patch.object(ai_clients, "ask_all", side_effect=lambda q, k, turns: make_turn(q)["results"]) as ask, \
             patch.object(ai_clients, "compare_answers", return_value=AIResult("比較", "比較本文")) as compare:
            app = self.app()
            app.text_area[0].set_value("最初の相談")
            self.click(app, "3つのAIに聞く")
            app.text_area[0].set_value("Claudeの料金案を広げたい")
            self.click(app, "3つのAIに続きを聞く")
            self.assertEqual(len(app.session_state["turns"]), 2)
            self.assertEqual(len(ask.call_args.args[2]), 1)
            self.assertIn("ChatGPTの独自の提案", compare.call_args.args[0])
            self.assertEqual(app.text_area[0].value, "")
            self.assertEqual(len(history_store.list_history(self.store.client)), 1)

            # 別セッションで履歴を開き、3回目を続ける。
            reopened = self.app()
            reopened.selectbox[0].set_value(self.store.rows[0]["id"]).run()
            count_before = ask.call_count
            self.click(reopened, "この履歴を開く")
            self.assertEqual(ask.call_count, count_before)
            self.assertEqual(len(reopened.session_state["turns"]), 2)
            self.store.fail_insert = True
            reopened.text_area[0].set_value("そこから閃いた考え")
            self.click(reopened, "3つのAIに続きを聞く")
            self.assertEqual(len(reopened.session_state["turns"]), 3)
            self.assertTrue(reopened.session_state["pending_save_id"])
            self.assertTrue(any(button.disabled for button in reopened.button if button.label == "新しい相談"))
            self.store.fail_insert = False
            count_before = ask.call_count
            self.click(reopened, "履歴の保存を再試行")
            self.assertEqual(ask.call_count, count_before)
            self.assertFalse(reopened.session_state["pending_save_id"])
            self.assertEqual(len(self.store.rows[0]["results"]["_turns"]), 3)
            self.click(reopened, "新しい相談")
            self.assertEqual(reopened.session_state["turns"], [])
            reopened.text_area[0].set_value("別のテーマ")
            self.click(reopened, "3つのAIに聞く")
            self.assertEqual(ask.call_args.args[2], [])
            self.assertEqual(len(history_store.list_history(self.store.client)), 2)


if __name__ == "__main__":
    unittest.main()
