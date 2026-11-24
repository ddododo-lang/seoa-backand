"""LLM 호출 부분. 나중에 Claude 등 다른 모델로 바꿀 때는 이 파일만 고치면 된다.

모든 LLM 클래스는 두 가지 메서드만 가지면 된다.
- chat(system, messages) -> str : 서아의 응답 생성
- complete(system, prompt, json_mode, budget_sec) -> str : 판정과 요약 같은 단발성 호출
- chat_json(system, messages, schema, budget_sec) -> str : 서아의 응답을 JSON으로 강제해서 생성 (빈 문자열이면 실패)
messages 형식: [{"role": "user" | "assistant", "content": "..."}]

Google이 바쁠 때(503·429·시간 초과) 대비
- 503(과부하)이면 같은 모델로 1초, 2초 쉬고 최대 2번 다시 시도한다.
  429(한도 초과)·시간 초과는 같은 모델을 기다려도 소용없어서 바로 다음 모델로 넘긴다.
- 그래도 안 되면 .env의 CHAT_MODELS / JUDGE_MODELS(쉼표로 구분) 순서대로 다음 모델로 넘어간다.
  값이 없으면 CHAT_MODEL / JUDGE_MODEL 하나만 쓴다. 404(없는 모델)는 다시 시도하지 않고 바로 넘긴다.
- 한 번의 요청은 LLM_BUDGET_SEC(기본 15초) 안에서 끝낸다. 모두 실패하면 LLMUnavailable을 던지고,
  엔진이 대체 응답(FALLBACK)을 쓴다.
- 어느 모델로 답했는지 로그에 한 줄 남긴다 (키는 찍지 않는다).
"""
import logging
import os
import random
import re
import time
from collections import Counter

FALLBACK_REPLY = "*서아가 잠시 말없이 잔 테두리를 손끝으로 훑는다.* …방금 뭐라고 했어, 사장님. 다시 말해 봐."

BUDGET_SEC = float(os.getenv("LLM_BUDGET_SEC", "15"))  # 요청 하나가 기다리는 최대 시간
ATTEMPT_TIMEOUT_SEC = 8.0     # 호출 한 번이 이보다 오래 걸리면 끊고 다음 모델로
SERVER_DEADLINE_SEC = 15      # Google에 알려 주는 기한 (Google은 10초 미만을 거부한다)
RETRY_DELAYS = (1, 2)         # 같은 모델로 다시 시도하기 전에 쉬는 시간(초)
RETRY_CODES = {503}           # 과부하 → 같은 모델로 다시 시도
QUOTA_CODES = {429}           # 한도 초과 → 기다려도 안 풀리니 바로 다음 모델
TIMEOUT_CODES = {408, 504}    # 시간 초과 → 바로 다음 모델
SKIP_CODES = {404}            # 없는 모델 → 바로 다음 모델

log = logging.getLogger("seoa.llm")
if not log.handlers:  # uvicorn·Render 로그에 보이도록 직접 출력
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(_handler)
    log.setLevel(logging.INFO)
    log.propagate = False


class LLMUnavailable(RuntimeError):
    """모든 모델이 바쁘거나 시간 안에 답하지 못했다."""


def model_list(list_env: str, single_env: str, default: str) -> list[str]:
    """CHAT_MODELS="a,b,c" → ["a", "b", "c"]. 없으면 CHAT_MODEL 하나."""
    names = [m.strip() for m in os.getenv(list_env, "").split(",") if m.strip()]
    if not names:
        names = [os.getenv(single_env, "").strip() or default]
    return list(dict.fromkeys(names))  # 중복 제거, 순서 유지


def _failure_kind(e: Exception) -> tuple[str, str] | None:
    """넘길 수 있는 실패면 ("retry" | "next", 짧은 이유), 아니면 None (키 오류 등은 그대로 올린다)."""
    code = getattr(e, "code", None)
    if isinstance(code, int):
        if code in RETRY_CODES:
            return "retry", str(code)
        if code in QUOTA_CODES:
            return "next", f"{code} 한도"
        if code in TIMEOUT_CODES:
            return "next", f"{code} 시간 초과"
        if code in SKIP_CODES:
            return "next", f"{code} 없는 모델"
        return None
    if "Timeout" in type(e).__name__:  # httpx / httpx2 TimeoutException 계열
        return "next", "시간 초과"
    return None


class GeminiLLM:
    def __init__(self, client=None, sleep=time.sleep, clock=time.monotonic):
        from google.genai import types

        if client is None:
            from google import genai  # 설치: pip install google-genai

            api_key = os.getenv("GEMINI_API_KEY")
            if not api_key:
                raise RuntimeError(".env 파일에 GEMINI_API_KEY가 없습니다.")
            # SDK 자체 재시도(최대 5번, 길게 기다림)는 끄고 아래 _generate에서 직접 다시 시도한다.
            client = genai.Client(
                api_key=api_key,
                http_options=types.HttpOptions(retry_options=types.HttpRetryOptions(attempts=1)),
            )
        self.client = client
        self._sleep = sleep
        self._clock = clock
        self.chat_models = model_list("CHAT_MODELS", "CHAT_MODEL", "gemini-2.5-flash")
        self.judge_models = model_list("JUDGE_MODELS", "JUDGE_MODEL", "gemini-2.5-flash-lite")
        self.chat_model = self.chat_models[0]
        self.judge_model = self.judge_models[0]
        self.stats = Counter()  # chat_fallover, judge_fallover, chat_unavailable, judge_unavailable
        self.last_model = {}    # {"chat": 마지막으로 답한 모델, "judge": ...}

    @staticmethod
    def _text(response) -> str:
        try:
            return (response.text or "").strip()
        except Exception:  # 안전 필터 등으로 응답이 막힌 경우
            return ""

    def _generate(self, kind: str, models: list[str], contents, config: dict,
                  budget_sec: float | None = None):
        """models 순서대로 시도해서 첫 응답을 돌려준다. 모두 실패하면 LLMUnavailable."""
        from google.genai import types

        start = self._clock()
        deadline = start + min(BUDGET_SEC, budget_sec if budget_sec is not None else BUDGET_SEC)
        failures = []
        for i, model in enumerate(models):
            for attempt in range(len(RETRY_DELAYS) + 1):
                remaining = deadline - self._clock()
                if remaining < 0.5:
                    break
                timeout_ms = int(min(remaining, ATTEMPT_TIMEOUT_SEC) * 1000)
                try:
                    response = self.client.models.generate_content(
                        model=model,
                        contents=contents,
                        config=types.GenerateContentConfig(
                            **config, http_options=types.HttpOptions(
                                timeout=timeout_ms,  # 우리 쪽에서 끊는 시간
                                # SDK는 timeout을 Google 기한으로도 보내는데 10초 미만이면 400이 난다.
                                # Google 쪽 기한은 넉넉히 주고, 끊는 건 위의 timeout으로 한다.
                                headers={"X-Server-Timeout": str(SERVER_DEADLINE_SEC)},
                            ),
                        ),
                    )
                except Exception as e:
                    kind_reason = _failure_kind(e)
                    if kind_reason is None:
                        raise
                    action, reason = kind_reason
                    failures.append(f"{model} {reason}")
                    if action == "next" or attempt == len(RETRY_DELAYS):
                        break
                    wait = RETRY_DELAYS[attempt]
                    if self._clock() + wait >= deadline - 0.5:
                        break  # 기다릴 시간이 없으면 다음 모델로
                    self._sleep(wait)
                    continue
                sec = self._clock() - start
                self.last_model[kind] = model
                if i > 0:
                    self.stats[f"{kind}_fallover"] += 1
                note = f" (넘김: {'; '.join(failures)})" if failures else ""
                log.info("[llm] %s 응답 모델=%s %.1f초%s", kind, model, sec, note)
                return response
            if deadline - self._clock() < 0.5:
                break
        self.stats[f"{kind}_unavailable"] += 1
        self.last_model[kind] = None
        sec = self._clock() - start
        log.warning("[llm] %s 모든 모델 실패 %.1f초: %s", kind, sec, "; ".join(failures) or "시간 부족")
        raise LLMUnavailable(f"{kind}: " + ("; ".join(failures) or "시간 부족"))

    @staticmethod
    def _contents(messages: list[dict]):
        from google.genai import types

        return [
            types.Content(
                role="model" if m["role"] == "assistant" else "user",
                parts=[types.Part(text=m["content"])],
            )
            for m in messages
        ]

    def chat(self, system: str, messages: list[dict]) -> str:
        try:
            response = self._generate("chat", self.chat_models, self._contents(messages), dict(
                system_instruction=system,
                temperature=0.9,
                max_output_tokens=2048,
            ))
        except LLMUnavailable:
            return FALLBACK_REPLY
        return self._text(response) or FALLBACK_REPLY

    def chat_json(self, system: str, messages: list[dict], schema: dict,
                  budget_sec: float | None = None) -> str:
        """응답을 JSON(응답 스키마)으로 강제한다. 막히거나 비면 빈 문자열, 모든 모델이 바쁘면 LLMUnavailable."""
        response = self._generate("chat", self.chat_models, self._contents(messages), dict(
            system_instruction=system,
            temperature=0.9,
            max_output_tokens=1024,
            response_mime_type="application/json",
            response_schema=schema,
        ), budget_sec)
        return self._text(response)

    def complete(self, system: str, prompt: str, json_mode: bool = False,
                 budget_sec: float | None = None) -> str:
        response = self._generate("judge", self.judge_models, prompt, dict(
            system_instruction=system,
            temperature=0.2,
            max_output_tokens=2048,
            response_mime_type="application/json" if json_mode else None,
        ), budget_sec)
        return self._text(response)


class FakeLLM:
    """API 키 없이 흐름을 테스트하는 가짜 모델. 유저 메시지의 키워드로 판정한다."""

    KEYWORDS = {
        "care": ["치료", "챙겨", "괜찮아", "약", "기억해"],
        "protect": ["지켜", "막아", "물러서", "건드리지"],
        "outside": ["옥상", "나가자", "산책", "데이트", "밖에"],
        "sincere": ["진심", "좋아", "소중"],
        "pursue": ["붙잡", "가지 마", "찾아왔"],
        "tease": ["놀리", "너야말로", "웃기네"],
        "comfort": ["토닥", "고생했", "쉬어"],
    }

    def chat(self, system: str, messages: list[dict]) -> str:
        last = messages[-1]["content"]
        stage = re.search(r"단계: (\d)", system)
        event = re.search(r"# 사건 지시\n(.{0,20})", system)
        tag = f"[단계 {stage.group(1) if stage else '?'}"
        tag += f" / 사건: {event.group(1)}…]" if event else "]"
        return f"*서아가 긴 보라색 머리칼을 쓸어 넘긴다.* {tag} '{last[:20]}'라니, 재밌네. 사장님."

    def chat_json(self, system: str, messages: list[dict], schema: dict,
                  budget_sec: float | None = None) -> str:
        import json

        last = messages[-1]["content"]
        ids = re.findall(r"^([a-z]\d{2}) ", system, re.M)  # MOTION_BLOCK의 "id 이름 (감정)" 줄
        stage = re.search(r"단계: (\d)", system)
        event = re.search(r"# 사건 지시\n(.{0,20})", system)
        tag = f"[단계 {stage.group(1) if stage else '?'}" + (f" / 사건: {event.group(1)}…]" if event else "]")
        return json.dumps({
            "action": "서아가 긴 보라색 머리칼을 쓸어 넘긴다. 빗소리가 창을 두드린다.",
            "dialogue": f"{tag} '{last[:20]}'라니, 재밌네. 사장님.",
            "motion_id": random.choice(ids) if ids else None,
            "emotion": "도발",
            "location": "바 옥상" if "옥상" in last else None,
        }, ensure_ascii=False)

    def complete(self, system: str, prompt: str, json_mode: bool = False,
                 budget_sec: float | None = None) -> str:
        if not json_mode:
            return "(가짜 요약) 지금까지 서아와 유저가 바에서 대화를 나눴다."
        user_part = prompt.split("[유저]")[-1].split("[서아]")[0]
        flags = [f for f, words in self.KEYWORDS.items() if any(w in user_part for w in words)]
        delta = 8 if flags else 1
        time_advance = "다음 날" in user_part or "내일" in user_part
        summary = "(가짜 요약) 서아와 유저가 바에서 대화를 이어 갔다." if "[요약 요청]" in prompt else ""
        return (
            '{"affection_delta": %d, "flags": %s, "time_advance": %s, '
            '"time_of_day": "", "summary": "%s", "reason": "가짜 판정"}'
            % (delta, str(flags).replace("'", '"'), "true" if time_advance else "false", summary)
        )


def make_llm():
    provider = os.getenv("LLM_PROVIDER", "gemini").lower()
    if provider == "fake":
        return FakeLLM()
    if provider == "gemini":
        return GeminiLLM()
    raise ValueError(f"지원하지 않는 LLM_PROVIDER: {provider}")
