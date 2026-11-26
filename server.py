"""FastAPI 서버. 실행: uvicorn server:app --reload

엔드포인트
- POST /sessions               새 대화 시작 → session_id와 오프닝
- POST /sessions/{id}/chat     메시지 보내기 → 서아의 응답
- GET  /sessions/{id}          대화 기록 (DEBUG=1이면 상태도)
- POST /chat, /judge           moti 프런트엔드용 (상태 없음, 앱이 상태를 보관)
- GET  /usage                  Gemini 사용량 합계 (호출 수·토큰·예상 비용, 서버가 켜진 뒤부터)
"""
import inspect
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from db import Store
from engine import Engine, to_text
from llm import make_llm
from usage import USAGE

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


# ---------- moti 프런트엔드 연결용 (상태 없음) ----------
# 상태는 앱(localStorage)이 보관하고 매 요청에 보낸다. 서버는 아무것도 저장하지 않는다.
#   POST /chat  : 서아 응답만 바로 만든다 → {action, dialogue, motion_id, emotion, location, reply}
#                 reply는 예전 형식 "*action* dialogue" (action이 없으면 dialogue만)
#                 S5: state 에 photo_ask_v=1 · photo_asks(목록)가 있으면 photo_ask(보통 null)도 붙는다
#                     {"id": "photo_3"|"photo_6", "route", "pose", "face", "camera_face"}. 없으면 예전 응답 그대로
#   POST /judge : 판정·단계 상승·사건 결정 → {state, stage_up, flags, pending_event}
SUPPORTED_CHARS = {"seoa"}  # 지금은 서아만 연결. 다른 캐릭터는 404 → 프런트가 미리 써 둔 대사로 대답


class Turn(BaseModel):
    model_config = {"extra": "ignore"}
    r: str
    t: str = ""


class Motion(BaseModel):
    model_config = {"extra": "ignore"}
    id: str
    name: str = ""
    emo: str = ""


class FrontChatIn(BaseModel):
    model_config = {"extra": "ignore"}
    char: str
    message: str = Field(min_length=1, max_length=1000)
    history: list[Turn] = []
    state: dict = {}
    motion_list: list[Motion] = []


class JudgeIn(BaseModel):
    model_config = {"extra": "ignore"}
    char: str
    state: dict = {}
    last_user: str = Field(default="", max_length=1000)
    last_reply: str = Field(default="", max_length=2000)
    need_summary: bool = False
    recent: list[Turn] = []  # need_summary일 때 요약에 쓸 최근 대화
    reply_ok: Any = None  # S4-1-1(선택): last_user 메시지마다 서아 답장이 정상이었는지 [true, false, …]. 모양이 틀리면 서버가 무시


def check_char(char: str) -> None:
    if char not in SUPPORTED_CHARS:
        raise HTTPException(404, f"{char} 캐릭터는 아직 서버에 연결되지 않았습니다.")


@app.post("/chat")
def front_chat(body: FrontChatIn):
    check_char(body.char)
    try:
        result = engine.reply(
            body.state,
            body.message,
            [t.model_dump() for t in body.history],
            [m.model_dump() for m in body.motion_list],
        )
        # 예전 앱 호환: 마이 페이지 「이전 버전(v1) 보기」가 읽는 필드
        result["reply"] = to_text(result)
        return result
    except Exception as e:  # LLM 호출 실패 (한도 초과, 네트워크 등)
        raise HTTPException(502, f"응답 생성에 실패했습니다: {type(e).__name__}")


@app.post("/judge")
def front_judge(body: JudgeIn):
    check_char(body.char)
    result = engine.judge(
        body.state, body.last_user, body.last_reply,
        need_summary=body.need_summary, recent=[t.model_dump() for t in body.recent], reply_ok=body.reply_ok,
    )
    response = {k: result[k] for k in ("state", "stage_up", "flags", "pending_event", "progress")}
    if DEBUG:
        response.update(judge=result["judge"], changes=result["changes"])
    return response


@app.get("/usage")
def usage():
    """Gemini 사용량 합계. 숫자만 있고 키·대화 내용은 없다."""
    return USAGE.report()


@app.get("/sessions/{sid}")
def get_session(sid: str):
    state = engine.store.get_state(sid)
    if state is None:
        raise HTTPException(404, "세션을 찾을 수 없습니다.")
    messages = [
        {"role": m["role"], "content": m["content"]} for m in engine.store.messages_after(sid)
    ]
    return {"messages": messages, "state": state if DEBUG else public_state(state)}
