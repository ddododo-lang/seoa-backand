"""사용량 기록 (feat/usage-log) 키 없는 테스트. 실행: python -m unittest discover -s tests -v"""
import sys
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_llm_fallover import GOOD, FakeClock, api_error  # noqa: E402

from llm import GeminiLLM, LLMUnavailable  # noqa: E402
from usage import KST, Usage  # noqa: E402


def meta(i, o, think=0, cached=0):
    return SimpleNamespace(prompt_token_count=i, candidates_token_count=o,
                           thoughts_token_count=think, cached_content_token_count=cached)


class MetaClient:
    """응답에 usage_metadata 를 붙여 돌려주는 가짜 client. plan: 모델 → 결과(예외면 던짐)."""

    def __init__(self, plan, um):
        self.plan, self.um = plan, um
        self.models = SimpleNamespace(generate_content=self._gen)

    def _gen(self, model, contents, config):
        out = self.plan.get(model, api_error(404))
        if isinstance(out, Exception):
            raise out
        return SimpleNamespace(text=out, usage_metadata=self.um)


def fixed_day(day="2026-11-26"):
    return lambda: datetime.fromisoformat(day + "T12:00:00").replace(tzinfo=KST)


def make(plan, um, usage):
    clock = FakeClock()
    env = {"CHAT_MODELS": "gemini-3.5-flash-lite,gemini-3.1-flash-lite",
           "JUDGE_MODELS": "gemini-3.5-flash-lite", "FALLBACK_MODEL": ""}
    with mock.patch.dict("os.environ", env):
        return GeminiLLM(client=MetaClient(plan, um), sleep=clock.sleep, clock=clock, usage=usage)


class UsageRecordTest(unittest.TestCase):
    def test_chat_judge_summary_counted_by_kind_and_model(self):
        u = Usage(clock=fixed_day())
        llm = make({"gemini-3.5-flash-lite": GOOD}, meta(5000, 120, think=30), u)
        llm.chat_json("sys", [{"role": "user", "content": "안녕"}], {})
        llm.chat_json("sys", [{"role": "user", "content": "또"}], {})
        llm.complete("sys", "판정", json_mode=True)
        llm.complete("sys", "요약")
        r = u.report()
        self.assertEqual(r["totals"]["chat"]["calls"], 2)
        self.assertEqual(r["totals"]["chat"]["input_tokens"], 10000)
        self.assertEqual(r["totals"]["chat"]["output_tokens"], 240)
        self.assertEqual(r["totals"]["chat"]["thinking_tokens"], 60)
        self.assertEqual(r["totals"]["judge"]["calls"], 1)
        self.assertEqual(r["totals"]["summary"]["calls"], 1)
        self.assertIn("gemini-3.5-flash-lite", r["days"]["2026-11-26"]["chat"])
        # 비용: (5000×4 × 0.30 + (150)×4 × 1.50) / 1e6
        self.assertAlmostEqual(r["est_cost_usd"], (20000 * 0.30 + 600 * 1.50) / 1e6, places=6)
        self.assertEqual(r["per_user_message"]["chat_input_tokens"], 5000)
        self.assertEqual(r["per_user_message"]["judge_summary_calls"], 1.0)
        self.assertAlmostEqual(r["per_user_message"]["est_cost_usd_all_in"], r["est_cost_usd"] / 2, places=6)

    def test_fallover_records_model_that_answered(self):
        u = Usage(clock=fixed_day())
        llm = make({"gemini-3.5-flash-lite": api_error(429), "gemini-3.1-flash-lite": GOOD}, meta(100, 10), u)
        llm.chat_json("sys", [{"role": "user", "content": "안녕"}], {})
        self.assertEqual(list(u.report()["by_model"]), ["gemini-3.1-flash-lite"])

    def test_all_failed_counted_without_tokens(self):
        u = Usage(clock=fixed_day())
        llm = make({}, meta(100, 10), u)
        with self.assertRaises(LLMUnavailable):
            llm.chat_json("sys", [{"role": "user", "content": "안녕"}], {})
        r = u.report()
        self.assertEqual(r["totals"]["chat"]["failed"], 1)
        self.assertEqual(r["totals"]["chat"]["calls"], 0)
        self.assertEqual(r["est_cost_usd"], 0)

    def test_missing_metadata_and_unknown_price(self):
        u = Usage(clock=fixed_day())
        u.record("chat", "new-model", None)
        r = u.report()
        self.assertEqual(r["totals"]["chat"]["calls"], 1)
        self.assertEqual(r["unpriced_models"], ["new-model"])

    def test_recording_error_does_not_break_reply(self):
        broken = mock.Mock()
        broken.record.side_effect = RuntimeError("boom")
        llm = make({"gemini-3.5-flash-lite": GOOD}, meta(1, 1), broken)
        self.assertEqual(llm.chat_json("sys", [{"role": "user", "content": "안녕"}], {}), GOOD)

    def test_price_override_from_env(self):
        with mock.patch.dict("os.environ", {"USAGE_PRICES": '{"gemini-3.5-flash-lite": [1, 2]}'}):
            u = Usage(clock=fixed_day())
        self.assertEqual(u.prices["gemini-3.5-flash-lite"], (1.0, 2.0))
        with mock.patch.dict("os.environ", {"USAGE_PRICES": "망가진 값"}):
            self.assertEqual(Usage().prices["gemini-3.5-flash-lite"], (0.30, 1.50))

    def test_days_split_in_korea_time(self):
        now = {"d": "2026-11-26"}
        u = Usage(clock=lambda: fixed_day(now["d"])())
        u.record("chat", "gemini-3.5-flash-lite", meta(10, 1))
        now["d"] = "2026-11-27"
        u.record("chat", "gemini-3.5-flash-lite", meta(10, 1))
        self.assertEqual(sorted(u.report()["days"]), ["2026-11-26", "2026-11-27"])

    def test_report_has_no_secret_or_text(self):
        u = Usage(clock=fixed_day())
        u.record("chat", "gemini-3.5-flash-lite", meta(10, 1))
        self.assertNotIn("KEY", str(u.report()).upper().replace("TOKENS", ""))


class UsageEndpointTest(unittest.TestCase):
    def test_get_usage(self):
        import os

        from fastapi.testclient import TestClient
        os.environ.setdefault("LLM_PROVIDER", "fake")
        if os.environ["LLM_PROVIDER"] != "fake":
            self.skipTest("LLM_PROVIDER 가 fake 가 아님")
        import server
        res = TestClient(server.app).get("/usage")
        self.assertEqual(res.status_code, 200)
        self.assertIn("totals", res.json())
        self.assertIn("per_user_message", res.json())


if __name__ == "__main__":
    unittest.main()
