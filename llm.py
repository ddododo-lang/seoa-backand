"""LLM 호출 부분. 나중에 Claude 등 다른 모델로 바꿀 때는 이 파일만 고치면 된다.

모든 LLM 클래스는 두 가지 메서드만 가지면 된다.
- chat(system, messages) -> str : 서아의 응답 생성
- complete(system, prompt, json_mode) -> str : 판정과 요약 같은 단발성 호출
- chat_json(system, messages, schema) -> str : 서아의 응답을 JSON으로 강제해서 생성 (빈 문자열이면 실패)
messages 형식: [{"role": "user" | "assistant", "content": "..."}]
"""
import os
import random
import re

FALLBACK_REPLY = "*서아가 잠시 말없이 잔 테두리를 손끝으로 훑는다.* …방금 뭐라고 했어, 사장님. 다시 말해 봐."


class GeminiLLM:
    def __init__(self):
        from google import genai  # 설치: pip install google-genai

        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError(".env 파일에 GEMINI_API_KEY가 없습니다.")
        self.client = genai.Client(api_key=api_key)
        self.chat_model = os.getenv("CHAT_MODEL", "gemini-2.5-flash")
        self.judge_model = os.getenv("JUDGE_MODEL", "gemini-2.5-flash-lite")

    @staticmethod
    def _text(response) -> str:
        try:
            return (response.text or "").strip()
        except Exception:  # 안전 필터 등으로 응답이 막힌 경우
            return ""

    def chat(self, system: str, messages: list[dict]) -> str:
        from google.genai import types

        contents = [
            types.Content(
                role="model" if m["role"] == "assistant" else "user",
                parts=[types.Part(text=m["content"])],
            )
            for m in messages
        ]
        response = self.client.models.generate_content(
            model=self.chat_model,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system,
                temperature=0.9,
                max_output_tokens=2048,
            ),
        )
        return self._text(response) or FALLBACK_REPLY

    def chat_json(self, system: str, messages: list[dict], schema: dict) -> str:
        """응답을 JSON(응답 스키마)으로 강제한다. 막히거나 비면 빈 문자열."""
        from google.genai import types

        contents = [
            types.Content(
                role="model" if m["role"] == "assistant" else "user",
                parts=[types.Part(text=m["content"])],
            )
            for m in messages
        ]
        response = self.client.models.generate_content(
            model=self.chat_model,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system,
                temperature=0.9,
                max_output_tokens=1024,
                response_mime_type="application/json",
                response_schema=schema,
            ),
        )
        return self._text(response)

    def complete(self, system: str, prompt: str, json_mode: bool = False) -> str:
        from google.genai import types

        config = types.GenerateContentConfig(
            system_instruction=system,
            temperature=0.2,
            max_output_tokens=2048,
            response_mime_type="application/json" if json_mode else None,
        )
        response = self.client.models.generate_content(
            model=self.judge_model, contents=prompt, config=config
        )
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

    def chat_json(self, system: str, messages: list[dict], schema: dict) -> str:
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

    def complete(self, system: str, prompt: str, json_mode: bool = False) -> str:
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
