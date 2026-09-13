import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from streamlit.testing.v1 import AppTest

import ai_clients
import history_store
from ai_clients import AIResult
from usage_data import UsageRecord, read_usage, summarize_usage, turn_usage
from test_conversation import Store, make_turn


class UsageTests(unittest.TestCase):
    def test_provider_accounting(self):
        openai = read_usage(UsageRecord(provider="ChatGPT", model="test"), {
            "input_tokens": 100, "output_tokens": 50, "total_tokens": 150,
            "input_tokens_details": {"cached_tokens": 80},
            "output_tokens_details": {"reasoning_tokens": 40},
        })
        self.assertEqual(openai.total_tokens, 150)
        claude = read_usage(UsageRecord(provider="Claude", model="test"), {
            "input_tokens": 100, "output_tokens": 50,
            "cache_creation_input_tokens": 20, "cache_read_input_tokens": 80,
        })
        self.assertEqual(claude.input_tokens, 200)
        self.assertEqual(claude.total_tokens, 250)
        gemini = read_usage(UsageRecord(provider="Gemini", model="test"), {
            "total_input_tokens": 100, "total_output_tokens": 50,
            "total_thought_tokens": 30, "total_tokens": 180,
        })
        self.assertEqual(gemini.total_tokens, 180)
        missing = read_usage(UsageRecord(provider="Gemini", model="test"), {"total_input_tokens": 100})
        self.assertIsNone(missing.total_tokens)

    def test_usage_is_extracted_from_all_four_calls(self):
        usage = SimpleNamespace(input_tokens=10, output_tokens=5, total_tokens=15)
        response = SimpleNamespace(output_text="本文", incomplete_details=None, usage=usage, output_parsed=None)
        openai = MagicMock()
        openai.responses.create.return_value = response
        openai.responses.parse.return_value = response
        anthropic = MagicMock()
        anthropic.messages.create.return_value = SimpleNamespace(
            usage=usage, stop_reason="end_turn", content=[SimpleNamespace(type="text", text="本文")],
        )
        gemini = MagicMock()
        gemini.__enter__.return_value.interactions.create.return_value = SimpleNamespace(
            usage=SimpleNamespace(total_input_tokens=10, total_output_tokens=5, total_tokens=20),
            output_text="本文", status="completed",
        )
        with patch.object(ai_clients, "OpenAI", return_value=openai), \
             patch.object(ai_clients, "Anthropic", return_value=anthropic), \
             patch.object(ai_clients.genai, "Client", return_value=gemini):
            results = ai_clients.ask_all("テスト", {"OPENAI_API_KEY": "test", "ANTHROPIC_API_KEY": "test", "GEMINI_API_KEY": "test"})
            comparison = ai_clients.compare_answers("テスト", results, "test")
        events = turn_usage({"results": results, "comparison": comparison})
        totals = summarize_usage(events, datetime.now(timezone.utc))
        self.assertEqual(totals["ChatGPT"]["total"], 30)
        self.assertEqual(totals["ChatGPT"]["measured"], 2)
        self.assertEqual(totals["Claude"]["total"], 15)
        self.assertEqual(totals["Gemini"]["total"], 20)
        self.assertTrue(all(result.succeeded for result in results.values()))

    def test_failed_response_retains_known_usage(self):
        client = MagicMock()
        client.responses.create.return_value = SimpleNamespace(
            usage=SimpleNamespace(input_tokens=10, output_tokens=5, total_tokens=15),
            output_text="", incomplete_details=None,
        )
        with patch.object(ai_clients, "OpenAI", return_value=client):
            result = ai_clients.ask_openai("test", "test-key")
        self.assertFalse(result.succeeded)
        self.assertEqual(result.usage.total_tokens, 15)
        self.assertIsNone(ai_clients.ask_openai("test", "").usage)

    def test_month_boundary_unknown_and_deduplication(self):
        event = UsageRecord(provider="Claude", model="test", recorded_at="2026-08-31T15:00:00+00:00", total_tokens=20).model_dump()
        previous = UsageRecord(provider="Claude", model="test", recorded_at="2026-08-31T14:59:00+00:00", total_tokens=999).model_dump()
        unknown = UsageRecord(provider="Gemini", model="test", recorded_at="2026-09-01T00:00:00+00:00").model_dump()
        result = summarize_usage([event, event, previous, unknown], datetime(2026, 9, 14, tzinfo=timezone.utc))
        self.assertEqual(result["Claude"]["total"], 20)
        self.assertEqual(result["Claude"]["measured"], 1)
        self.assertEqual(result["Gemini"]["measured"], 0)
        self.assertEqual(result["Gemini"]["unknown"], 1)

    def test_storage_keeps_usage_and_snapshots_do_not_duplicate(self):
        store = Store()
        self.addCleanup(store.http.close)
        first, second = make_turn(), make_turn("追加")
        first["results"]["ChatGPT"].usage = UsageRecord(provider="ChatGPT", model="test", total_tokens=10)
        second["results"]["ChatGPT"].usage = UsageRecord(provider="ChatGPT", model="test", total_tokens=20)
        for i, turns in enumerate(([first], [first, second])):
            history_store.save_history(store.client, first["question"], first["results"], first["comparison"], turns=turns, conversation_id="one", record_id=str(i))
        restored = history_store.restore_conversation(store.rows[0])[1]
        self.assertEqual(restored[0]["results"]["ChatGPT"].usage.total_tokens, 10)
        events = history_store.list_usage(store.client, "2026-09-01T00:00:00+00:00")
        result = summarize_usage(events + turn_usage(second), datetime.now(timezone.utc))
        self.assertEqual(result["ChatGPT"]["total"], 30)
        self.assertEqual(len(events), 2)

    def test_panel_unknown_and_target_overflow(self):
        def panel():
            from usage_panel import show_usage_panel
            from usage_data import UsageRecord
            show_usage_panel(None, "", "", [UsageRecord(provider="ChatGPT", model="test", total_tokens=200_000).model_dump()])
        app = AppTest.from_function(panel).run()
        self.assertFalse(app.exception)
        self.assertEqual(app.metric[0].value, "200,000 トークン")
        self.assertEqual(app.metric[1].value, "未計測")
        app.number_input[0].set_value(100_000).run()
        self.assertFalse(app.exception)
        self.assertTrue(any("100,000 トークン超え" in item.value for item in app.caption))


if __name__ == "__main__":
    unittest.main()
