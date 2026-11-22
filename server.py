"""FastAPI 서버. 실행: uvicorn server:app --reload

엔드포인트
- POST /sessions               새 대화 시작 → session_id와 오프닝
- POST /sessions/{id}/chat     메시지 보내기 → 서아의 응답
- GET  /sessions/{id}          대화 기록 (DEBUG=1이면 상태도)
- POST /chat                   moti 프런트엔드(app.js의 askAI)용 주소
"""
import inspect
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from db import Store
from engine import Engine
from llm import make_llm

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

DEBUG = os.getenv("DEBUG", "0") == "1"
engine = Engine(
    llm=make_llm(),
    store=Store(os.getenv("DB_PATH", str(BASE_DIR / "seoa.db"))),
    prompt_path=os.getenv("PROMPT_PATH", str(BASE_DIR / "yoon_seoa_prompt.md")),
)

app = FastAPI(title="윤서아 캐릭터챗 백엔드")

# 프런트엔드에서 호출할 수 있게 허용 (배포 시 주소를 좁히세요)
cors = dict(
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)
# 인터넷에 있는 페이지(GitHub Pages 등)가 내 컴퓨터 서버를 부를 수 있게 허용 (Chrome)
if "allow_private_network" in inspect.signature(CORSMiddleware.__init__).parameters:
    cors["allow_private_network"] = True
app.add_middleware(CORSMiddleware, **cors)


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=1000)


def public_state(state: dict) -> dict:
    """화면에 보여 줘도 되는 값만 고른다."""
    return {k: state[k] for k in ("stage", "affection", "day", "time_of_day", "location", "ending")}


@app.post("/sessions")
def create_session():
    sid, opening = engine.start_session()
    return {"session_id": sid, "opening": opening}


@app.post("/sessions/{sid}/chat")
def chat(sid: str, body: ChatIn):
    try:
        result = engine.chat(sid, body.message)
    except KeyError:
        raise HTTPException(404, "세션을 찾을 수 없습니다.")
    except Exception as e:  # LLM 호출 실패 (한도 초과, 네트워크 등)
        raise HTTPException(502, f"응답 생성에 실패했습니다: {type(e).__name__}")

    response = {"reply": result["reply"], "ending": result["state"]["ending"]}
    if DEBUG:
        response.update(
            event=result["event"],
            judge=result["judge"],
            changes=result["changes"],
            state=result["state"],
        )
    return response


# ---------- moti 프런트엔드 연결용 ----------
# app.js의 askAI()가 보내는 형식을 그대로 받고, 기대하는 형식으로 돌려준다.
SUPPORTED_CHARS = {"seoa"}  # 지금은 서아만 연결. 다른 캐릭터는 404 → 프런트가 미리 써 둔 대사로 대답

SCENE_RULES = [  # 판정 모델이 알려 준 장소 → 프런트엔드 배경 이름
    (("옥상", "하늘", "루프"), "옥상"),
    (("비", "빗"), "비"),
    (("골목", "거리", "밖"), "골목"),
]


class FrontChatIn(BaseModel):
    model_config = {"extra": "ignore"}
    char: str
    message: str = Field(min_length=1, max_length=1000)
    session_id: str | None = None
    scenes: list[str] | None = None


def pick_scene(location: str, scenes: list[str] | None) -> str | None:
    if not location or not scenes:
        return None
    for keywords, key in SCENE_RULES:
        if any(k in location for k in keywords):
            for name in scenes:
                if key in name:
                    return name
    return None


@app.post("/chat")
def front_chat(body: FrontChatIn):
    if body.char not in SUPPORTED_CHARS:
        raise HTTPException(404, f"{body.char} 캐릭터는 아직 서버에 연결되지 않았습니다.")

    sid = body.session_id
    if not sid or engine.store.get_state(sid) is None:
        sid, _ = engine.start_session()  # 처음이거나 세션이 사라졌으면 새로 시작

    try:
        result = engine.chat(sid, body.message)
    except Exception as e:
        raise HTTPException(502, f"응답 생성에 실패했습니다: {type(e).__name__}")

    judge, changes, state = result["judge"], result["changes"], result["state"]
    stage_up = any(c.startswith("단계 상승") for c in changes)
    key_moment = min(60, max(0, judge["affection_delta"]) * 5 + (30 if stage_up else 0))

    response = {
        "session_id": sid,
        "reply": result["reply"],
        "key_moment": key_moment,          # 0~60. 프런트의 '결정적 순간' 게이지에 쓰임
        "scene": pick_scene(judge["location"], body.scenes),
        "emotion": None,
        "motions": [],                     # 비워 두면 프런트가 모션을 알아서 고름
        "stage": state["stage"],
        "affection": state["affection"],
        "ending": state["ending"],
    }
    if DEBUG:
        response.update(event=result["event"], judge=judge, changes=changes)
    return response


@app.get("/sessions/{sid}")
def get_session(sid: str):
    state = engine.store.get_state(sid)
    if state is None:
        raise HTTPException(404, "세션을 찾을 수 없습니다.")
    messages = [
        {"role": m["role"], "content": m["content"]} for m in engine.store.messages_after(sid)
    ]
    return {"messages": messages, "state": state if DEBUG else public_state(state)}
