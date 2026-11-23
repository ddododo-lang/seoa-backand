"""LLM 호출 부분. 나중에 Claude 등 다른 모델로 바꿀 때는 이 파일만 고치면 된다.

모든 LLM 클래스는 두 가지 메서드만 가지면 된다.
- chat(system, messages) -> str : 서아의 응답 생성
- complete(system, prompt, json_mode) -> str : 판정과 요약 같은 단발성 호출
messages 형식: [{"role": "user" | "assistant", "content": "..."}]
"""
import logging
import os
import re
import time

FALLBACK_REPLY = "*서아가 잠시 말없이 잔 테두리를 손끝으로 훑는다.* …방금 뭐라고 했어, 사장님. 다시 말해 봐."

# uvicorn 로그와 같은 곳에 찍혀서 Render Logs에서 보인다
log = logging.getLogger("uvicorn.error")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


class GeminiLLM:
    """Gemini 호출. 실패하면 다시 시도하고, 그래도 안 되면 예비 모델로 한 번 더 부른다.

    Render 환경 변수로 바꿀 수 있는 값 (없으면 괄호 안 기본값)
    - CHAT_MODEL (gemini-3.5-flash-lite): 서아 대사용 모델
    - JUDGE_MODEL (gemini-3.5-flash-lite): 판정·요약용 모델
    - FALLBACK_MODEL (gemini-2.5-flash-lite): 주 모델이 계속 실패할 때 쓸 예비 모델. 비우면 예비 모델 안 씀
    - LLM_RETRIES (2): 바쁨·일시 장애·시간 초과일 때 같은 모델로 더 시도하는 횟수
    - LLM_CALL_TIMEOUT (12): 한 번 부를 때 기다리는 최대 초
    - LLM_TOTAL_TIMEOUT (25): 다시 시도·예비 모델까지 합쳐 쓰는 최대 초. 앱이 30초에서 포기하므로 그보다 짧게
    """

    def __init__(self):
        from google import genai  # 설치: pip install google-genai

        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError(".env 파일에 GEMINI_API_KEY가 없습니다.")
        self.client = genai.Client(api_key=api_key)
        self.chat_model = os.getenv("CHAT_MODEL", "gemini-3.5-flash-lite")
        self.judge_model = os.getenv("JUDGE_MODEL", "gemini-3.5-flash-lite")
        self.fallback_model = os.getenv("FALLBACK_MODEL", "gemini-2.5-flash-lite").strip()
        self.retries = int(_env_float("LLM_RETRIES", 2))
        self.call_timeout = _env_float("LLM_CALL_TIMEOUT", 12)
        self.total_timeout = _env_float("LLM_TOTAL_TIMEOUT", 25)

    @staticmethod
    def _text(response) -> str:
        try:
            return (response.text or "").strip()
        except Exception:  # 안전 필터 등으로 응답이 막힌 경우
            return ""

    @staticmethod
    def _why(e: Exception) -> str:
        """로그에 남길 실패 이유 한 줄 (구글이 보낸 코드와 메시지)"""
        code = getattr(e, "code", None)
        msg = getattr(e, "message", None) or str(e)
        msg = " ".join(str(msg).split())[:300]
        return f"{type(e).__name__}" + (f" {code}" if code else "") + f": {msg}"

    @staticmethod
    def _retryable(e: Exception) -> bool:
        """같은 모델로 다시 해 볼 만한 실패인가: 구글 서버 오류(500·503 등)나 시간 초과"""
        from google.genai import errors

        if isinstance(e, (errors.ServerError, TimeoutError, ConnectionError)):
            return True
        # 시간 초과·연결 끊김: 라이브러리 버전마다 httpx / httpx2 등 이름이 달라서 오류 이름으로 확인
        names = {c.__name__ for c in type(e).__mro__}
        return bool(names & {"TimeoutException", "TransportError", "NetworkError", "RemoteProtocolError"})

    def _generate(self, model: str, contents, config, what: str):
        """model 로 부르고, 실패하면 다시 시도 → 예비 모델. 모두 실패하면 마지막 오류를 그대로 올린다(서버가 502로 바꿈)."""
        from google.genai import types

        config.http_options = types.HttpOptions(timeout=int(self.call_timeout * 1000))
        t0 = time.monotonic()
        models = [model] + ([self.fallback_model] if self.fallback_model and self.fallback_model != model else [])
        last = None
        for m in models:
            for attempt in range(self.retries + 1):
                left = self.total_timeout - (time.monotonic() - t0)
                if left <= 1:
                    log.warning("[LLM] %s 시간 다 씀(%.0f초) → 포기", what, self.total_timeout)
                    raise last or TimeoutError("LLM 시간 초과")
                config.http_options.timeout = int(min(self.call_timeout, left) * 1000)
                try:
                    response = self.client.models.generate_content(model=m, contents=contents, config=config)
                    if last is not None:  # 앞에서 한 번이라도 실패했다가 살아난 경우만 알림
                        log.info("[LLM] %s 성공: %s (시도 %d, %.1f초)", what, m, attempt + 1, time.monotonic() - t0)
                    return response
                except Exception as e:
                    last = e
                    log.warning("[LLM] %s 실패: %s 시도 %d → %s", what, m, attempt + 1, self._why(e))
                    if not self._retryable(e):
                        break  # 한도 초과(429)·키 오류·모델 이름 오류 등은 같은 모델로 다시 해도 소용없음 → 예비 모델로
                    if attempt < self.retries:
                        time.sleep(min(2 ** attempt, max(0, self.total_timeout - (time.monotonic() - t0) - 1)))  # 1초, 2초
        log.error("[LLM] %s 최종 실패 (%.1f초) → %s", what, time.monotonic() - t0, self._why(last))
        raise last

    def chat(self, system: str, messages: list[dict]) -> str:
        from google.genai import types

        contents = [
            types.Content(
                role="model" if m["role"] == "assistant" else "user",
                parts=[types.Part(text=m["content"])],
            )
            for m in messages
        ]
        config = types.GenerateContentConfig(
            system_instruction=system,
            temperature=0.9,
            max_output_tokens=2048,
        )
        response = self._generate(self.chat_model, contents, config, "대사")
        return self._text(response) or FALLBACK_REPLY

    def complete(self, system: str, prompt: str, json_mode: bool = False) -> str:
        from google.genai import types

        config = types.GenerateContentConfig(
            system_instruction=system,
            temperature=0.2,
            max_output_tokens=2048,
            response_mime_type="application/json" if json_mode else None,
        )
        response = self._generate(self.judge_model, prompt, config, "판정" if json_mode else "요약")
        return self._text(response)


class FakeLLM:
    """API 키 없이 흐름을 테스트하는 가짜 모델. 유저 메시지의 키워드로 판정한다."""

    KEYWORDS = {
        "care": ["치료", "챙겨", "괜찮아", "약", "기억해"],
        "protect": ["지켜", "막아", "물러서", "건드리지"],
        "outside": ["옥상", "나가자", "산책", "데이트", "밖에"],
        "sincere": ["진심", "좋아", "소중"],
        "pursue": ["붙잡", "가지 마", "찾아왔"],
    }

    def chat(self, system: str, messages: list[dict]) -> str:
        last = messages[-1]["content"]
        stage = re.search(r"단계: (\d)", system)
        event = re.search(r"# 사건 지시\n(.{0,20})", system)
        tag = f"[단계 {stage.group(1) if stage else '?'}"
        tag += f" / 사건: {event.group(1)}…]" if event else "]"
        return f"*서아가 긴 보라색 머리칼을 쓸어 넘긴다.* {tag} '{last[:20]}'라니, 재밌네. 사장님."

    def complete(self, system: str, prompt: str, json_mode: bool = False) -> str:
        if not json_mode:
            return "(가짜 요약) 지금까지 서아와 유저가 바에서 대화를 나눴다."
        user_part = prompt.split("[유저]")[-1].split("[서아]")[0]
        flags = [f for f, words in self.KEYWORDS.items() if any(w in user_part for w in words)]
        delta = 8 if flags else 1
        time_advance = "다음 날" in user_part or "내일" in user_part
        return (
            '{"affection_delta": %d, "flags": %s, "time_advance": %s, '
            '"location": "", "time_of_day": "", "reason": "가짜 판정"}'
            % (delta, str(flags).replace("'", '"'), "true" if time_advance else "false")
        )


def make_llm():
    provider = os.getenv("LLM_PROVIDER", "gemini").lower()
    if provider == "fake":
        return FakeLLM()
    if provider == "gemini":
        return GeminiLLM()
    raise ValueError(f"지원하지 않는 LLM_PROVIDER: {provider}")
