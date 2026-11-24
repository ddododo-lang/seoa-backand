"""지금 어떤 Gemini 모델이 답하는지 빠르게 확인한다. 키는 .env에서 읽고 화면에 찍지 않는다.

실행 (가상 환경을 켜고):
    python check_models.py                      # .env의 CHAT_MODELS·JUDGE_MODELS + 기본 후보
    python check_models.py gemini-3.5-flash ... # 직접 고른 모델만

각 모델에 "안녕" 한 번만 보내고, SDK 자체 재시도는 끈다(503이면 바로 '안 됨').
"""
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

DEFAULT_CANDIDATES = [
    "gemini-3.5-flash", "gemini-3.6-flash", "gemini-3.7-flash",
    "gemini-3.5-flash-lite", "gemini-3.1-flash-lite",
]


def candidates() -> list[str]:
    if len(sys.argv) > 1:
        return sys.argv[1:]
    names = []
    for env in ("CHAT_MODELS", "CHAT_MODEL", "JUDGE_MODELS", "JUDGE_MODEL"):
        names += [m.strip() for m in os.getenv(env, "").split(",") if m.strip()]
    return list(dict.fromkeys(names + DEFAULT_CANDIDATES))


def main():
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        sys.exit(".env 파일에 GEMINI_API_KEY가 없습니다.")
    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(retry_options=types.HttpRetryOptions(attempts=1)),
    )
    for m in candidates():
        t0 = time.time()
        try:
            client.models.generate_content(
                model=m, contents="안녕",
                config=types.GenerateContentConfig(http_options=types.HttpOptions(timeout=15000)),
            )
            print(f"{m} -> 됨 OK ({time.time() - t0:.1f}초)")
        except Exception as e:
            code = getattr(e, "code", None)
            print(f"{m} -> 안 됨 {code or type(e).__name__}")


if __name__ == "__main__":
    main()
