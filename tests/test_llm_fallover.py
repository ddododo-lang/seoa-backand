"""키 없이 도는 테스트: Google이 503·429·시간 초과·404를 낼 때 다시 시도하고 다음 모델로 넘어가는지 확인.

실행: python -m unittest discover -s tests -v
가짜 client와 가짜 시계를 써서 실제로 기다리지도, 네트워크에 나가지도 않는다.
"""
import json
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from google.genai import errors  # noqa: E402

import llm as llm_mod  # noqa: E402
from db import Store  # noqa: E402
from engine import FALLBACK, Engine  # noqa: E402
from llm import GeminiLLM, LLMUnavailable  # noqa: E402

GOOD = json.dumps({"action": "서아가 잔을 내려놓는다.", "dialogue": "왔어, 사장님.",
                   "motion_id": None, "emotion": "평온", "location": None}, ensure_ascii=False)


def api_error(code: int):
    cls = errors.ServerError if code >= 500 else errors.ClientError
    return cls(code, {"error": {"code": code, "message": f"fake {code}", "status": "FAKE"}})


class FakeTimeout(Exception):
    """httpx / httpx2 TimeoutException과 이름 규칙이 같은 가짜 예외."""


FakeTimeout.__name__ = "ReadTimeout"


class FakeClock:
    def __init__(self):
        self.t = 0.0
        self.sleeps = []

    def __call__(self):
        return self.t

    def sleep(self, sec):
        self.sleeps.append(sec)
        self.t += sec


class FakeClient:
    """plan: {모델 이름: [그 모델의 n번째 호출 결과 ...]}. 결과가 예외면 던지고, 문자열이면 text로 돌려준다.
    목록이 끝나면 마지막 결과를 계속 쓴다. latency만큼 가짜 시계를 앞으로 돌린다."""

    def __init__(self, plan, clock, latency=0.3):
        self.plan, self.clock, self.latency = plan, clock, latency
        self.calls = []
        self.models = SimpleNamespace(generate_content=self._generate)

    def _generate(self, model, contents, config):
        self.calls.append(model)
        timeout = config.http_options.timeout / 1000  # 실제 SDK처럼 호출마다 걸린 시간 제한을 지킨다
        if self.latency > timeout:
            self.clock.t += timeout
            raise FakeTimeout("read timeout")
        self.clock.t += self.latency
        outcomes = self.plan.get(model, [api_error(404)])
        n = sum(1 for m in self.calls if m == model) - 1
        result = outcomes[min(n, len(outcomes) - 1)]
        if isinstance(result, Exception):
            raise result
        return SimpleNamespace(text=result)


def make_llm(plan, chat_models, judge_models=("judge-a",), latency=0.3):
    clock = FakeClock()
    client = FakeClient(plan, clock, latency)
    env = {"CHAT_MODELS": ",".join(chat_models), "JUDGE_MODELS": ",".join(judge_models)}
    with mock.patch.dict(os.environ, env):
        llm = GeminiLLM(client=client, sleep=clock.sleep, clock=clock)
    return llm, client, clock


class FalloverTest(unittest.TestCase):
    def test_first_model_503_then_second_model_answers(self):
        """요청 5번: 첫 모델이 503을 내면 두 번째 모델로 답한다."""
        llm, client, clock = make_llm(
            {"model-a": [api_error(503)], "model-b": [GOOD]}, ["model-a", "model-b"])
        out = llm.chat_json("system", [{"role": "user", "content": "안녕"}], {})
        self.assertEqual(json.loads(out)["dialogue"], "왔어, 사장님.")
        self.assertEqual(client.calls, ["model-a", "model-a", "model-a", "model-b"])  # 처음 + 다시 2번
        self.assertEqual(clock.sleeps, [1, 2])
        self.assertEqual(llm.last_model["chat"], "model-b")
        self.assertEqual(llm.stats["chat_fallover"], 1)

    def test_engine_reply_uses_second_model_not_fallback(self):
        """앱이 부르는 경로(engine.reply)에서도 대체 응답 대신 두 번째 모델의 답이 나온다."""
        llm, client, _ = make_llm(
            {"model-a": [api_error(503)], "model-b": [GOOD]}, ["model-a", "model-b"])
        engine = Engine(llm=llm, store=Store(":memory:"), prompt_path=str(BASE_DIR / "yoon_seoa_prompt.md"))
        reply = engine.reply({}, "안녕", [], [])
        self.assertEqual(reply["dialogue"], "왔어, 사장님.")
        self.assertEqual(client.calls[-1], "model-b")

    def test_429_retries_same_model(self):
        llm, client, clock = make_llm(
            {"model-a": [api_error(429), GOOD]}, ["model-a", "model-b"])
        llm.chat_json("system", [{"role": "user", "content": "안녕"}], {})
        self.assertEqual(client.calls, ["model-a", "model-a"])
        self.assertEqual(clock.sleeps, [1])
        self.assertEqual(llm.stats["chat_fallover"], 0)

    def test_timeout_goes_straight_to_next_model(self):
        llm, client, clock = make_llm(
            {"model-a": [FakeTimeout("read timeout")], "model-b": [GOOD]}, ["model-a", "model-b"])
        llm.chat_json("system", [{"role": "user", "content": "안녕"}], {})
        self.assertEqual(client.calls, ["model-a", "model-b"])
        self.assertEqual(clock.sleeps, [])

    def test_404_model_is_skipped_without_retry(self):
        llm, client, _ = make_llm(
            {"gemini-2.5-flash": [api_error(404)], "model-b": [GOOD]}, ["gemini-2.5-flash", "model-b"])
        llm.chat_json("system", [{"role": "user", "content": "안녕"}], {})
        self.assertEqual(client.calls, ["gemini-2.5-flash", "model-b"])

    def test_all_models_busy_gives_fallback_within_budget(self):
        llm, client, clock = make_llm(
            {"model-a": [api_error(503)], "model-b": [api_error(503)], "model-c": [api_error(503)]},
            ["model-a", "model-b", "model-c"])
        with self.assertRaises(LLMUnavailable):
            llm.chat_json("system", [{"role": "user", "content": "안녕"}], {})
        self.assertLessEqual(clock.t, llm_mod.BUDGET_SEC)
        self.assertEqual(llm.stats["chat_unavailable"], 1)

        engine = Engine(llm=llm, store=Store(":memory:"), prompt_path=str(BASE_DIR / "yoon_seoa_prompt.md"))
        self.assertEqual(engine.reply({}, "안녕", [], []), FALLBACK)

    def test_slow_failures_never_exceed_budget(self):
        """호출마다 6초씩 걸리고 503이 나도, 기다린 시간 합계는 15초를 넘지 않는다."""
        llm, client, clock = make_llm(
            {"model-a": [api_error(503)], "model-b": [api_error(503)]}, ["model-a", "model-b"], latency=6)
        with self.assertRaises(LLMUnavailable):
            llm.chat_json("system", [{"role": "user", "content": "안녕"}], {})
        self.assertLessEqual(clock.t, llm_mod.BUDGET_SEC)

    def test_judge_uses_judge_models(self):
        verdict = '{"affection_delta": 2, "flags": [], "time_advance": false}'
        llm, client, _ = make_llm(
            {"judge-a": [api_error(503)], "judge-b": [verdict]}, ["model-a"], ["judge-a", "judge-b"])
        self.assertEqual(llm.complete("system", "prompt", json_mode=True), verdict)
        self.assertEqual(llm.last_model["judge"], "judge-b")
        self.assertEqual(llm.stats["judge_fallover"], 1)

    def test_other_errors_are_not_swallowed(self):
        """키가 틀린 경우(400·403 등)는 다른 모델로 넘기지 않고 그대로 알린다."""
        llm, client, _ = make_llm({"model-a": [api_error(403)], "model-b": [GOOD]}, ["model-a", "model-b"])
        with self.assertRaises(errors.ClientError):
            llm.chat_json("system", [{"role": "user", "content": "안녕"}], {})
        self.assertEqual(client.calls, ["model-a"])

    def test_log_names_model_and_never_the_key(self):
        llm, _, _ = make_llm({"model-a": [api_error(503)], "model-b": [GOOD]}, ["model-a", "model-b"])
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "AIzaSECRET123"}), \
                self.assertLogs("seoa.llm", level="INFO") as logs:
            llm.chat_json("system", [{"role": "user", "content": "안녕"}], {})
        text = "\n".join(logs.output)
        self.assertIn("모델=model-b", text)
        self.assertNotIn("AIza", text)


class RealSdkRequestTest(unittest.TestCase):
    """가짜 client가 아니라 실제 google-genai SDK로 요청을 만들고, 네트워크 대신 MockTransport로 받는다.
    Google은 기한(X-Server-Timeout)이 10초 미만이면 400을 내므로 그 값을 확인한다."""

    def test_request_headers_and_fallover_through_sdk(self):
        try:
            import httpx2 as httpx_mod
        except ImportError:
            import httpx as httpx_mod
        from google import genai
        from google.genai import types

        seen = []

        def handler(request):
            seen.append((request.url.path, request.headers.get("x-server-timeout"),
                         request.extensions.get("timeout", {}).get("read")))
            if "model-a" in request.url.path:
                return httpx_mod.Response(503, json={"error": {"code": 503, "message": "busy", "status": "UNAVAILABLE"}})
            body = {"candidates": [{"content": {"role": "model", "parts": [{"text": GOOD}]}}]}
            return httpx_mod.Response(200, json=body)

        client = genai.Client(api_key="dummy", http_options=types.HttpOptions(
            retry_options=types.HttpRetryOptions(attempts=1),
            httpx_client=httpx_mod.Client(transport=httpx_mod.MockTransport(handler))))
        with mock.patch.dict(os.environ, {"CHAT_MODELS": "model-a,model-b"}):
            llm = GeminiLLM(client=client, sleep=lambda sec: None)
        out = llm.chat_json("system", [{"role": "user", "content": "안녕"}],
                            {"type": "OBJECT", "properties": {"dialogue": {"type": "STRING"}}})
        self.assertEqual(json.loads(out)["dialogue"], "왔어, 사장님.")
        self.assertEqual([p.rsplit("/", 1)[-1] for p, _, _ in seen],
                         ["model-a:generateContent"] * 3 + ["model-b:generateContent"])
        for _, deadline, read_timeout in seen:
            self.assertGreaterEqual(int(deadline), 10)              # Google 최소 기한
            self.assertLessEqual(read_timeout, llm_mod.ATTEMPT_TIMEOUT_SEC)  # 우리 쪽은 짧게 끊음


class ModelListTest(unittest.TestCase):
    def test_models_env_wins(self):
        with mock.patch.dict(os.environ, {"CHAT_MODELS": " a , b,,a ", "CHAT_MODEL": "x"}):
            self.assertEqual(llm_mod.model_list("CHAT_MODELS", "CHAT_MODEL", "d"), ["a", "b"])

    def test_single_model_when_list_missing(self):
        with mock.patch.dict(os.environ, {"CHAT_MODEL": "x"}, clear=True):
            self.assertEqual(llm_mod.model_list("CHAT_MODELS", "CHAT_MODEL", "d"), ["x"])
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(llm_mod.model_list("CHAT_MODELS", "CHAT_MODEL", "d"), ["d"])


if __name__ == "__main__":
    unittest.main()
