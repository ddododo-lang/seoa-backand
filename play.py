"""터미널에서 서아와 바로 대화해 보는 테스트용 스크립트.

python play.py              새 대화 시작
python play.py <세션ID>      이어서 하기
대화 중 명령: /state (상태 보기), /quit (종료)
"""
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from db import Store
from engine import Engine
from llm import make_llm

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

engine = Engine(
    llm=make_llm(),
    store=Store(os.getenv("DB_PATH", str(BASE_DIR / "seoa.db"))),
    prompt_path=os.getenv("PROMPT_PATH", str(BASE_DIR / "yoon_seoa_prompt.md")),
)

if len(sys.argv) > 1:
    sid = sys.argv[1]
    if engine.store.get_state(sid) is None:
        sys.exit("그 세션을 찾을 수 없습니다.")
    last = engine.store.messages_after(sid)[-1]
    print(f"(세션 {sid} 이어서)\n\n서아: {last['content']}\n")
else:
    sid, opening = engine.start_session()
    print(f"(새 세션: {sid})\n\n{opening}\n")

while True:
    try:
        text = input("나: ").strip()
    except (EOFError, KeyboardInterrupt):
        break
    if not text:
        continue
    if text == "/quit":
        break
    if text == "/state":
        print(json.dumps(engine.store.get_state(sid), ensure_ascii=False, indent=2))
        continue

    try:
        result = engine.chat(sid, text)
    except Exception as e:
        print(f"[오류] {type(e).__name__}: {e}\n")
        continue

    print(f"\n서아: {result['reply']}\n")
    info = []
    if result["event"]:
        info.append(f"사건 {result['event']}")
    info += result["changes"]
    if info:
        print(f"  ({' / '.join(info)})\n")
    if result["state"]["ending"]:
        print(f"  ★ 엔딩: {result['state']['ending']}\n")

print(f"\n세션 ID: {sid}  (python play.py {sid} 로 이어서 할 수 있어요)")
