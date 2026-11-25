"""대화 엔진.

두 가지 방식으로 쓸 수 있다.
- 상태 없는 방식 (moti 앱): reply()로 서아 응답만 만들고, judge()로 판정·상태 갱신을 한다.
  상태는 클라이언트가 보관해서 매 요청에 보내고, 서버는 아무것도 저장하지 않는다.
- 세션 방식 (play.py, /sessions): chat()이 위 두 단계를 묶어서 SQLite에 저장한다.

게임 규칙(단계 조건, 호감도 상한, 사건 목록)은 맨 위 상수에서 바꾸면 된다.
"""
import json
import random
import re
import time

from db import Store
from llm import BUDGET_SEC, LLMUnavailable
from prompt_loader import load_blocks, render

# ---------- 게임 규칙 (여기서 조정) ----------
STAGE_NAMES = {1: "경계", 2: "티키타카", 3: "썸", 4: "고백 직전", 5: "엇갈림", 6: "연인"}  # 앱 단계 이름과 같게
AFFECTION_CAP = {1: 30, 2: 50, 3: 70, 4: 85, 5: 95, 6: 100}
STAGE_EVENTS = {
    1: ["INJURY", "TAEO"],
    2: ["INJURY", "MARI", "TAEO", "BLACKOUT"],
    3: ["MARI", "STORM", "ROOFTOP", "BLACKOUT"],  # DATE 는 2단계 장난 갈래 사건으로 옮김(앱 51)
    4: ["STORM", "ROOFTOP", "TAEO"],
    5: ["TAEO"],
    6: ["MARI", "STORM", "DATE", "ROOFTOP", "BLACKOUT", "INJURY", "TAEO"],
}
ALLOWED_FLAGS = {"care", "protect", "outside", "sincere", "pursue", "tease", "comfort", "reveal_secret1"}
ROUTES = ("tease", "care")  # 앱 51 갈래
# 크리스마스(s2) 자정 기색: 자정까지 남은 유저 메시지 수 → 그 턴에 한 번 (에피소드 단계 4 미만일 때만, 점점 분명하게)
# 남은 수가 그 구간에 들어오면, 그 구간의 줄을 최근 기록에서 아직 안 했을 때 한 번 (10·5·2 즈음, 점점 분명하게)
XMAS_LATE = [  # (구간 위, 구간 아래, 줄, 기록에서 찾을 핵심 글)
    (10, 6, "「…밤 꽤 깊었네.」", "밤 꽤 깊었네"),
    (5, 3, "「슬슬 들어가야겠다.」", "슬슬 들어가야겠다"),
    (2, 1, "*서아가 장갑을 끼며 한 걸음 물러선다.*", "장갑을 끼며 한 걸음 물러선다"),
]
# 특별 에피소드(앱 42, server-sync.md): state.episode 로 오고, 본 이야기 단계 지침·사건 대신 블록을 넣는다
EPISODES = {
    "s2": {"block": "EPISODE_XMAS", "locations": ["크리스마스 거리", "서아의 자취방"]},
}
LOCATIONS = ["바 '라스트 오더' 1층", "바 '라스트 오더' 2층", "네온 골목", "비 내리는 골목", "바 옥상"]
EMOTIONS = {"평온", "기쁨", "도발", "장난", "진심", "설렘", "수줍음", "놀람", "슬픔", "분노", "질투", "당황"}
POSE_REQUEST = re.compile(r"^\s*(.+?)\s*해\s*줄래\s*\??\s*$")  # 앱 48 포즈 부탁 문장
HISTORY_MESSAGES = 32       # 대화 호출에 넣을 최근 기록 (16턴 = 유저 16 + 서아 16)
FALLBACK = {"action": None, "dialogue": "…", "motion_id": None, "emotion": "평온", "location": None, "gift_moment": None}
REPLY_SCHEMA = {  # Gemini 응답 스키마 (OUTPUT_FORMAT과 같은 모양)
    "type": "OBJECT",
    "properties": {
        "action": {"type": "STRING", "nullable": True},
        "dialogue": {"type": "STRING"},
        "motion_id": {"type": "STRING", "nullable": True},
        "emotion": {"type": "STRING"},
        "location": {"type": "STRING", "nullable": True},
        "gift_moment": {  # 앱 50: 순간 선물을 여는 신호. 보통 null
            "type": "OBJECT", "nullable": True,
            "properties": {"id": {"type": "STRING"}, "reason": {"type": "STRING"}},
        },
    },
    "required": ["action", "dialogue", "motion_id", "emotion", "location"],
}
SHORT_MESSAGE_LEN = 20      # 이보다 짧은 유저 메시지는 '짧은 답'
SHORT_STREAK_TRIGGER = 3    # 짧은 답이 이만큼 연속되면 사건 발동
QUIET_TURNS_TRIGGER = 15    # 사건 없이 이만큼 지나면 사건 발동
EVENT_COOLDOWN = 20         # 같은 사건을 다시 쓰기까지 필요한 턴 수
EVENT_LOCATIONS = {  # 이 사건이 시작되는 답장은 이 장소로 옮김 (앱 배경 전환). DATE·WALK_HOME 은 밖으로 나갈 때 모델이 옮김
    "STORM": "바 '라스트 오더' 2층", "ROOFTOP": "바 옥상", "ALLEY": "비 내리는 골목",
}
# 갈래 사건 (앱 51): 단계 → 갈래 → 사건, 그 단계에서 호감이 기준에 닿으면 한 번
ROUTE_EVENTS = {2: {"tease": "DATE", "care": "WALK_HOME"},
                3: {"tease": "STORM", "care": "ROOFTOP"},
                4: {"tease": "ALLEY", "care": "ALLEY"}}
ROUTE_EVENT_AFFECTION = {2: 40, 3: 55, 4: 65}
ROUTE_SCENES = {"DATE", "WALK_HOME", "STORM", "ROOFTOP", "ALLEY"}
SCENE_JUDGES = 2  # 갈래 장면이 시작된 뒤 판정 몇 번(≈ 3턴씩) 동안 장면 블록을 계속 넣을지
STAGE_UP_SCENES = {2, 3, 4, 6}  # 단계가 오른 다음 답장에 한 번 넣는 상징 장면(STAGE_UP_n). 5는 DEPARTURE
# 대화량 보조 점수 (판정 코드에서 계산, 모델에 맡기지 않음)
TALK_LONG = 40             # 이 글자 수(공백 뺌) 이상이면 긴 메시지
TALK_MAX_COUNT = 150       # 메시지 하나는 이 글자 수까지만 셈
TALK_BONUS_FLAGS = {"care", "sincere", "comfort", "tease"}
TALK_BONUS_STAGE_MAX = 8   # 단계마다 보너스 합계 상한(단계가 오르면 0부터)
MIN_STAGE_MSGS = {2: 15, 3: 20, 4: 15}  # 그 단계에서 이만큼 유저 메시지를 보내야 다음 단계로(1→2·5→6 없음)
NOT_TALK = re.compile(r"[\sㅋㅎㅠㅜㄷㄱ~!?.,…·\-_^*()\[\]<>:;'\"0-9]+")  # 이것만 있으면 「글」이 아님(이모지는 따로 뺌)
GIFT_MOMENT_GAP = 10       # 같은 순간 선물은 준 뒤 유저 메시지 이만큼 지나야 다시 엶
GIFT_MOMENT_QUIET = 3      # 순간 선물을 준 뒤 이만큼은 어떤 순간 선물도 열지 않음(자주 넣지 않기)
# 조연 (앱 54): 갈래마다 한 명, 나올 때마다 한 걸음씩(처음 1 → 두 번째 2 → 마무리 3)
SIDE_CHARS = ("MARI", "TAEO")
SIDE_STEP_KEYS = ("mari_step", "taeo_step")
SIDE_OF_ROUTE = {"tease": "MARI", "care": "TAEO"}
DEPARTURE_DAYS = 3          # 5단계 출발까지 남은 날 (앱 51: 7일 → 사흘)
# 남은 날이 줄어든 다음 답장의 action 맨 앞에 한 번 붙이는 지문 (pending_event 로 전달, 앱이 답장 뒤 비움)
DAY_MARKERS = {
    "DAYS_LEFT_2": "서아가 떠나기까지, 이틀.",
    "DAYS_LEFT_1": "서아가 떠나기까지, 하루.",
    "LAST_NIGHT": "오늘 밤이 지나면, 서아는 떠난다.",
}
SUMMARY_TRIGGER = 40        # 요약 안 된 메시지가 이만큼 쌓이면 요약
KEEP_RECENT = 20            # 요약 후에도 원문으로 남겨 둘 최근 메시지 수

SUMMARY_SYSTEM = (
    "너는 롤플레이 대화 요약기다. 기존 요약과 새 대화를 합쳐 하나의 요약으로 만든다. "
    "중요한 사건, 관계 변화, 서아가 한 약속이나 고백, 유저가 기억해야 할 디테일을 포함한다. "
    "유저가 자기 이름을 말했으면 「유저 이름: ○○」을 꼭 남긴다. "
    "한국어로 10문장 이내, 요약문만 출력한다."
)


def new_state() -> dict:
    return {
        "stage": 1,
        "affection": 0,
        "day": 1,
        "time_of_day": "새벽 2시",
        "location": "바 '라스트 오더' 1층",
        "revealed_secrets": [],
        "departure_days_left": None,
        "summary": "",
        "stage_flags": [],        # 현재 단계에서 나온 플래그 (단계가 오르면 초기화)
        "turn": 0,
        "short_streak": 0,
        "turns_since_event": 0,
        "used_events": {},        # 사건 이름 -> 마지막으로 쓴 턴
        "pending_event": None,    # 다음 턴에 반드시 넣을 사건
        "summarized_upto": 0,     # 이 메시지 id까지는 요약에 들어감 (세션 방식에서만 씀)
        "ending": None,           # None | "lovers" | "departed"
        "recent_lines": [],       # 직전 서아 응답 3개 (반복 방지)
        "route": None,            # 갈래 "tease" | "care" | None (앱 51이 정함, 서버는 읽기만)
        "scene": None,            # 진행 중인 갈래 장면(ROUTE_SCENES). 판정이 정하고 앱이 돌려보냄
        "scene_judges_left": 0,   # 그 장면 블록을 넣을 남은 판정 수
        "last_night_told": False, # 5단계 남은 하루의 밤 지문을 이미 냈는지
        "gift_moments": [],       # 앱 50: 지금 열 수 있는 순간 선물 [{id, name, when}] (앱이 이미 거름)
        "stage_bonus": 0,         # 이 단계에서 받은 대화량 보너스 합계(상한 TALK_BONUS_STAGE_MAX)
        "stage_user_msgs": 0,     # 이 단계에서 보낸 유저 메시지 수(에피소드 메시지 제외)
        "episode": None,          # 특별 에피소드 id (앱 42: "s2"). 없으면 본 이야기
        "episode_stage": None,    # 에피소드만의 단계 1~6 (앱이 정함)
        "episode_stage_name": "", # 에피소드 단계 이름 (앱 이름)
        "episode_turns_left": None,  # 크리스마스: 자정까지 남은 유저 메시지 수 (앱이 셈)
        "mari_step": 0,           # 앱 54 조연 차례: 0 아직 · 1 처음 · 2 두 번째 · 3 마무리(또는 닫힘)
        "taeo_step": 0,
    }


def normalize_state(raw: dict | None) -> dict:
    """클라이언트가 보낸 상태를 믿을 수 있는 모양으로 맞춘다. 빠진 값은 기본값으로 채운다."""
    state = new_state()
    if not isinstance(raw, dict):
        return state
    for key, default in state.items():
        if key not in raw or raw[key] is None and default is not None:
            continue
        value = raw[key]
        if isinstance(default, bool) or default is None:
            state[key] = value
        elif isinstance(default, int):
            try:
                state[key] = int(value)
            except (TypeError, ValueError):
                pass
        elif isinstance(default, type(value)):
            state[key] = value
    state["stage"] = max(1, min(6, state["stage"]))
    state["affection"] = max(0, min(100, state["affection"]))
    state["gift_moments"] = [
        {k: str(g.get(k) or "").strip()[:60] for k in ("id", "name", "when")}
        for g in (state["gift_moments"] if isinstance(state["gift_moments"], list) else [])
        if isinstance(g, dict) and str(g.get("id") or "").strip()
    ][:6]
    for key in SIDE_STEP_KEYS:  # 조연 차례는 0~3 정수만
        try:
            state[key] = max(0, min(3, int(state[key])))
        except (TypeError, ValueError):
            state[key] = 0
    for key in ("stage_bonus", "stage_user_msgs"):
        try:
            state[key] = max(0, int(state[key]))
        except (TypeError, ValueError):
            state[key] = 0
    if state["route"] not in ROUTES:
        state["route"] = None
    if state["scene"] not in ROUTE_SCENES:
        state["scene"] = None
    state["scene_judges_left"] = max(0, min(SCENE_JUDGES, int(state["scene_judges_left"] or 0))) if state["scene"] else 0
    if state["episode"] not in EPISODES:
        state["episode"] = None
    if state["episode"]:
        try:
            state["episode_stage"] = max(1, min(6, int(state["episode_stage"])))
        except (TypeError, ValueError):
            state["episode_stage"] = 1
        state["episode_stage_name"] = str(state["episode_stage_name"] or "").strip()[:20]
        try:
            state["episode_turns_left"] = max(0, int(state["episode_turns_left"]))
        except (TypeError, ValueError):
            state["episode_turns_left"] = None
    else:
        state["episode_stage"], state["episode_stage_name"], state["episode_turns_left"] = None, "", None
    if state["location"] not in allowed_locations(state):
        state["location"] = allowed_locations(state)[0]
    ev = state["pending_event"]
    if isinstance(ev, str):
        ev = ev.removeprefix("EVENT_")
        known = ({e for v in STAGE_EVENTS.values() for e in v} | {"DEPARTURE"} | ROUTE_SCENES | set(DAY_MARKERS)
                 | {f"STAGE_UP_{n}" for n in STAGE_UP_SCENES})
        state["pending_event"] = ev if ev in known else None
    else:
        state["pending_event"] = None
    state["recent_lines"] = [str(x) for x in state["recent_lines"]][-3:]
    state["stage_flags"] = [f for f in state["stage_flags"] if f in ALLOWED_FLAGS]
    if not isinstance(state["used_events"], dict):
        state["used_events"] = {}
    return state


def allowed_locations(state: dict) -> list[str]:
    """에피소드 중이면 그 에피소드의 장소만, 아니면 본 이야기 다섯 곳."""
    ep = EPISODES.get(state.get("episode") or "")
    return ep["locations"] if ep else LOCATIONS


def to_text(reply: dict) -> str:
    """JSON 응답을 '*행동* 대사' 글로 바꾼다 (세션 방식 저장, 대화 기록용)."""
    action = (reply.get("action") or "").strip()
    return (f"*{action}* " if action else "") + (reply.get("dialogue") or "").strip()


class Engine:
    def __init__(self, llm, store: Store, prompt_path: str):
        self.llm = llm
        self.store = store
        self.blocks = load_blocks(prompt_path)

    # ---------- 세션 ----------
    def start_session(self) -> tuple[str, str]:
        sid = self.store.create_session(new_state())
        opening = self.blocks["OPENING"]
        self.store.add_message(sid, "assistant", opening)
        return sid, opening

    # ---------- 한 턴 ----------
    def chat(self, sid: str, user_message: str) -> dict:
        state = self.store.get_state(sid)
        if state is None:
            raise KeyError("세션이 없습니다.")
        user_message = user_message.strip()

        event = state["pending_event"]
        history = self.store.messages_after(sid, state["summarized_upto"])
        history = [{"r": "u" if m["role"] == "user" else "c", "t": m["content"]} for m in history]
        parsed = self.reply(state, user_message, history, [])  # 실패하면 예외 → 아무것도 저장하지 않음
        reply = to_text(parsed)

        self.store.add_message(sid, "user", user_message)
        self.store.add_message(sid, "assistant", reply)

        state["pending_event"] = None
        if parsed["location"]:
            state["location"] = parsed["location"]
        state["recent_lines"] = (state.get("recent_lines", []) + [reply])[-3:]
        result = self.judge(state, user_message, reply, need_summary=False)
        state = result["state"]
        self._maybe_summarize(sid, state)
        self.store.save_state(sid, state)

        return {"reply": reply, "parsed": parsed, "event": event, "judge": result["judge"],
                "changes": result["changes"], "state": state}

    # ---------- 상태 없는 방식: 서아 응답 ----------
    def reply(self, state: dict, user_message: str, history: list[dict], motion_list: list[dict]) -> dict:
        """서아 응답 하나를 JSON으로 만들어 검증한다. state는 바꾸지 않는다.

        history: [{"r": "u"|"c", "t": "..."}] 최근 기록. motion_list: [{"id", "name", "emo"}].
        """
        state = normalize_state(state)
        state["gift_moments"] = self._open_moments(state["gift_moments"], history, user_message)
        state["_xmas_late"] = self._xmas_late(state, history)
        event = state["pending_event"]
        system = self._build_system(state, event, motion_list)
        opening = self.blocks["OPENING"].strip()
        # OPENING은 모델이 만들지 않고 고정. 에피소드는 앱이 자기 첫 장면을 기록으로 보내므로 바 오프닝을 넣지 않음
        msgs = [] if state["episode"] else [{"role": "assistant", "content": opening}]
        for m in history[-HISTORY_MESSAGES:]:
            text = str(m.get("t") or "").strip()
            if not text or text == opening or m.get("r") not in ("u", "c"):
                continue
            msgs.append({"role": "user" if m["r"] == "u" else "assistant", "content": text})
        messages = self._merge_same_role(
            self._to_llm_messages(msgs) + [{"role": "user", "content": user_message.strip()}]
        )

        ids = {str(m.get("id")) for m in motion_list if isinstance(m, dict)}
        pose_id = self._pose_request(user_message, motion_list)
        start = time.monotonic()
        for _ in range(2):  # 파싱 실패하면 1번 더 (두 번 합쳐 BUDGET_SEC 안에서)
            remaining = BUDGET_SEC - (time.monotonic() - start)
            if remaining < 1:
                break
            try:
                raw = self.llm.chat_json(system, messages, REPLY_SCHEMA, budget_sec=remaining)
            except LLMUnavailable:  # 모든 모델이 바쁨 → 바로 대체 응답
                break
            data = self._parse_json(raw)
            if data and str(data.get("dialogue") or "").strip():
                out = self._validate_reply(data, ids, allowed_locations(state))
                out["gift_moment"] = self._gift_moment(data.get("gift_moment"), state["gift_moments"])
                if event in DAY_MARKERS:  # 남은 날 지문은 모델에 맡기지 않고 그대로 앞에 붙임
                    out["action"] = f"{DAY_MARKERS[event]} {out['action'] or ''}".strip()
                if not out["location"] and event in EVENT_LOCATIONS:  # 장소를 옮기는 사건인데 모델이 빠뜨리면 서버가 채움
                    out["location"] = EVENT_LOCATIONS[event]
                if pose_id:  # 포즈 부탁: 앱이 그 몸짓을 고정 재생하므로 답장도 같은 몸짓으로
                    out["motion_id"] = pose_id
                return out
        return dict(FALLBACK)

    @staticmethod
    def _merge_same_role(messages: list[dict]) -> list[dict]:
        """앱이 서아 쪽 줄을 연달아 넣을 수 있어서(셀카 메모, 다시 들어올 때 인사) 같은 역할이 이어지면 한 메시지로 합친다."""
        merged: list[dict] = []
        for m in messages:
            if merged and merged[-1]["role"] == m["role"]:
                merged[-1] = {"role": m["role"], "content": merged[-1]["content"] + "\n" + m["content"]}
            else:
                merged.append(dict(m))
        return merged

    @staticmethod
    def _xmas_late(state: dict, history: list[dict]) -> str | None:
        """크리스마스 자정 기색: 남은 수의 구간 줄을, 최근 서아 말에 아직 없을 때만. 에피소드 단계 4 이상이면 없음."""
        left = state.get("episode_turns_left")
        if state.get("episode") != "s2" or left is None or (state.get("episode_stage") or 0) >= 4:
            return None
        recent = " ".join(str(h.get("t") or "") for h in history[-12:] if h.get("r") == "c")
        for top, bottom, line, key in XMAS_LATE:
            if bottom <= left <= top:
                return None if key in recent else line
        return None

    @staticmethod
    def _open_moments(moments: list[dict], history: list[dict], user_message: str = "") -> list[dict]:
        """앱이 보낸 순간 선물 목록에서, 최근에 준 것을 뺀다(대화 기록 + 이번 메시지의 「[유저가 선물함: 이름] · 순간 선물」 줄로 셈)."""
        if not moments:
            return moments
        users = [str(h.get("t") or "") for h in history if h.get("r") == "u"] + [user_message or ""]
        given = [i for i, t in enumerate(users) if "· 순간 선물" in t or "순간 선물]" in t]
        if given and len(users) - 1 - given[-1] < GIFT_MOMENT_QUIET:
            return []  # 방금 순간 선물을 줬으면 잠깐 쉼
        def recent(name: str) -> bool:
            for i in reversed(given):
                if f"[유저가 선물함: {name}" in users[i]:
                    return len(users) - 1 - i < GIFT_MOMENT_GAP
            return False
        return [g for g in moments if not recent(g["name"])]

    @staticmethod
    def _gift_moment(raw, moments: list[dict]) -> dict | None:
        """모델이 고른 순간 선물. 이번 목록 안의 id 만, reason 은 짧게."""
        if not isinstance(raw, dict) or not moments:
            return None
        gid = str(raw.get("id") or "").strip()
        if gid not in {g["id"] for g in moments}:
            return None
        reason = str(raw.get("reason") or "").strip().strip("\"'")[:20]
        return {"id": gid, "reason": reason or next(g["name"] for g in moments if g["id"] == gid)}

    @staticmethod
    def _pose_request(user_message: str, motion_list: list[dict]) -> str | None:
        """앱 포즈 부탁 「{몸짓 이름} 해 줄래?」(48)이면 그 몸짓 id. 목록에 없는 이름이면 None."""
        match = POSE_REQUEST.match(user_message or "")
        if not match:
            return None
        name = match.group(1).strip()
        for m in motion_list:
            if isinstance(m, dict) and str(m.get("name") or "").strip() == name and m.get("id"):
                return str(m["id"])
        return None

    @staticmethod
    def _parse_json(raw: str) -> dict | None:
        if not raw:
            return None
        match = re.search(r"\{.*\}", raw, re.S)
        try:
            data = json.loads(match.group(0)) if match else None
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None

    @staticmethod
    def _validate_reply(data: dict, ids: set[str], locations: list[str] | None = None) -> dict:
        def clean(x):
            x = str(x or "").strip().strip("*").strip()
            return x or None

        mid = data.get("motion_id")
        emotion = str(data.get("emotion") or "").strip()
        location = str(data.get("location") or "").strip()
        dialogue = clean(data.get("dialogue")) or "…"
        if len(dialogue) >= 2 and dialogue[0] in "\"“" and dialogue[-1] in "\"”":
            dialogue = dialogue[1:-1].strip()
        return {
            "action": clean(data.get("action")),
            "dialogue": dialogue,
            "motion_id": mid if isinstance(mid, str) and mid in ids else None,
            "emotion": emotion if emotion in EMOTIONS else "평온",
            "location": location if location in (locations or LOCATIONS) else None,
        }

    # ---------- 상태 없는 방식: 판정 ----------
    def judge(self, state: dict, last_user: str, last_reply: str,
              need_summary: bool = False, recent: list[dict] | None = None) -> dict:
        """판정 호출 → 규칙 적용 → 다음 턴 사건 결정. 갱신된 state를 돌려준다."""
        state = normalize_state(state)
        stage_before = state["stage"]
        judge = self._judge(state, last_user, last_reply, need_summary, recent or [])
        progress = None
        if not state["episode"]:  # 대화량 점수·메시지 수는 본 이야기만
            msgs = [m.strip() for m in (last_user or "").split("\n") if m.strip()]
            judge["talk_bonus"] = min(self._talk_bonus(msgs, judge), TALK_BONUS_STAGE_MAX - state["stage_bonus"])
            state["stage_user_msgs"] += len(msgs)
        changes = self._apply(state, judge, None)
        if not state["episode"]:
            need = MIN_STAGE_MSGS.get(state["stage"], 0)
            progress = {
                "talk_bonus": judge["talk_bonus"], "stage_bonus": state["stage_bonus"],
                "stage_bonus_max": TALK_BONUS_STAGE_MAX, "stage_user_msgs": state["stage_user_msgs"],
                "min_msgs": need, "min_msgs_left": max(0, need - state["stage_user_msgs"]),
            }
            changes.append(f"대화 보너스 +{judge['talk_bonus']} (이 단계 {state['stage_bonus']}/{TALK_BONUS_STAGE_MAX})"
                           f" · 다음 단계까지 최소 메시지 {progress['min_msgs_left']}개 남음")
        if need_summary and judge["summary"]:
            state["summary"] = judge["summary"]
            changes.append("요약 갱신")
        if not state["episode"]:
            self._tick_scene(state)
            route_event = self._route_event(state)
            if route_event:
                self._start_route_scene(state, route_event, changes)
        # 에피소드 중에는 앱이 단계·장면을 정하므로 사건 계산을 건너뛴다 (단계 상승은 _apply에서 건너뜀)
        nxt_event = None if state["episode"] else self._tick_events(state, last_user)
        if nxt_event:
            changes.append(f"다음 턴 사건: {nxt_event}")
        return {
            "state": state,
            "stage_up": state["stage"] > stage_before,
            "flags": judge["flags"],
            "pending_event": state["pending_event"],
            "judge": judge,
            "changes": changes,
            "progress": progress,
        }

    # ---------- 프롬프트 조립 ----------
    def _values(self, state: dict) -> dict:
        days = state["departure_days_left"]
        values = {
            "stage": state["stage"],
            "stage_name": STAGE_NAMES[state["stage"]],
            "affection": state["affection"],
            "day": state["day"],
            "time_of_day": state["time_of_day"],
            "location": state["location"],
            "revealed_secrets": ", ".join(state["revealed_secrets"]) or "없음",
            "departure_days_left": "-" if days is None else days,
            "summary": state["summary"] or "아직 없음 (이야기의 시작)",
            "recent_lines": "\n".join(f"- {x}" for x in state.get("recent_lines", [])[-3:]) or "- 없음",
        }
        if state.get("episode"):  # 에피소드: 본 이야기 단계·호감·요약·비밀 대신 이 밤의 값
            name = state["episode_stage_name"] or STAGE_NAMES[state["episode_stage"]]
            values.update(
                stage=state["episode_stage"], stage_name=name, affection="-",
                revealed_secrets="-", departure_days_left="-",
                summary="(특별 에피소드: 본 이야기와 따로인 하룻밤)",
                episode_stage=state["episode_stage"], episode_stage_name=name,
            )
        return values

    def _build_system(self, state: dict, event: str | None, motion_list: list[dict] | None = None) -> str:
        values = self._values(state)
        lines = [
            f"{m.get('id')} {m.get('name')} ({m.get('emo')})"
            for m in (motion_list or []) if isinstance(m, dict) and m.get("id")
        ]
        values["motion_list"] = "\n".join(lines) or "(지금은 없음. motion_id는 null)"
        parts = [
            self.blocks["BASE_SYSTEM"],
            render(self.blocks["STATE_BLOCK"], values),
        ]
        episode = EPISODES.get(state.get("episode") or "")
        if episode:  # 본 이야기 STAGE_n·EVENT_*·첫날 줄 대신 에피소드 블록 (판정은 그대로)
            if state.get("_xmas_late"):  # reply() 가 구간·기록을 보고 정함
                values["xmas_late"] = state["_xmas_late"]
            parts += [
                render(self.blocks[episode["block"]], values),
                *([render(self.blocks["XMAS_LATE"], values)] if "xmas_late" in values else []),
                render(self.blocks["MOTION_BLOCK"], values),
                self.blocks["OUTPUT_FORMAT"],
            ]
            return "\n\n".join(parts)
        if state["gift_moments"]:  # 앱 50: 열 수 있는 순간 선물이 있을 때만
            values["gift_moments"] = "\n".join(f"- {g['id']} {g['name']}: {g['when']}" for g in state["gift_moments"])
            parts.append(render(self.blocks["GIFT_MOMENT"], values))
        if state["day"] == 1:  # 앱 37 골목 프롤로그: 첫날 밤에만
            parts.append(self.blocks["FIRST_NIGHT"])
        parts += [
            render(self.blocks[f"STAGE_{state['stage']}"], values),
            render(self.blocks["MOTION_BLOCK"], values),
            self.blocks["OUTPUT_FORMAT"],
        ]
        if state["stage"] == 5 and state["route"] and state["departure_days_left"] is not None:
            parts.append(self.blocks[f"ROUTE_PURSUE_{state['route'].upper()}"])
        if state["stage"] == 6 and state["route"]:
            parts.append(self.blocks[f"ROUTE_MORNING_{state['route'].upper()}"])
        if event and f"EVENT_{event}" in self.blocks:  # 이번 답장의 사건(시작 턴)
            parts.append(self.blocks[f"EVENT_{event}"])
        elif event and event.startswith("STAGE_UP_") and event in self.blocks:
            parts.append(self.blocks[event])  # 단계 오른 직후 한 번: 앱이 답장 뒤 pending_event 를 비움
        elif state["scene"]:  # 다른 사건이 없으면 진행 중인 갈래 장면을 이어 감
            parts.append(self.blocks[f"EVENT_{state['scene']}"])
        if "ALLEY" in (event, None if event else state["scene"]) and state["route"] == "care":
            parts.append(self.blocks["EVENT_ALLEY_CARE"])
        return "\n\n".join(parts)

    @staticmethod
    def _to_llm_messages(history: list[dict]) -> list[dict]:
        msgs = [{"role": m["role"], "content": m["content"]} for m in history]
        # Gemini는 대화가 user로 시작해야 하므로 앞에 시작 표시를 붙인다.
        if not msgs or msgs[0]["role"] == "assistant":
            msgs.insert(0, {"role": "user", "content": "(이야기 시작)"})
        return msgs

    # ---------- 사건 ----------
    @staticmethod
    def _talk_bonus(msgs: list[str], judge: dict) -> int:
        """판정 한 번(최대 3턴)의 유저 메시지 글자 수로 주는 보너스. 짧은 메시지에 감점은 없음."""
        if judge["affection_delta"] < 0:
            return 0
        seen, lengths = set(), []
        for m in msgs:
            text = re.sub(r"\s+", " ", m).strip()
            key = re.sub(r"\s+", "", text)
            plain = "".join(ch for ch in NOT_TALK.sub("", text) if ord(ch) < 0x2600 or 0xAC00 <= ord(ch) <= 0xD7A3)
            if (text.startswith("[유저가 선물함:") or POSE_REQUEST.match(text) or not plain or key in seen):
                lengths.append(0)  # 선물 줄·포즈 부탁·ㅋㅎ·기호·이모지만·같은 말 반복은 글자 수에서 뺌
            else:
                lengths.append(min(len(key), TALK_MAX_COUNT))
            seen.add(key)
        long_n = sum(n >= TALK_LONG for n in lengths)
        if len(msgs) >= 3 and long_n == len(msgs) and TALK_BONUS_FLAGS & set(judge["flags"]):
            return 2
        return 1 if long_n >= 2 else 0

    @staticmethod
    def _tick_scene(state: dict) -> None:
        """진행 중인 갈래 장면의 남은 판정 수를 하나 줄이고, 다 되면 장면을 닫는다."""
        if state["scene"]:
            state["scene_judges_left"] -= 1
            if state["scene_judges_left"] <= 0:
                state["scene"], state["scene_judges_left"] = None, 0

    @staticmethod
    def _route_event(state: dict) -> str | None:
        """갈래 사건: 그 단계에서 호감이 기준에 닿으면 한 번(앱 51). route 가 없으면 None(예전 규칙)."""
        route, stage = state["route"], state["stage"]
        if not route or state["ending"] or state["pending_event"] or stage not in ROUTE_EVENTS:
            return None
        event = ROUTE_EVENTS[stage][route]
        if f"ROUTE_{event}" in state["used_events"] or state["affection"] < ROUTE_EVENT_AFFECTION[stage]:
            return None
        if stage == 2 and "outside" in state["stage_flags"]:
            return None  # 유저가 먼저 바 밖으로 나갔으면 그 장면으로 이어 감
        return event

    @staticmethod
    def _start_route_scene(state: dict, event: str, changes: list[str]) -> None:
        state["pending_event"] = event
        state["scene"], state["scene_judges_left"] = event, SCENE_JUDGES
        state["used_events"][f"ROUTE_{event}"] = state["turn"]
        state["used_events"][event] = state["turn"]
        state["short_streak"], state["turns_since_event"] = 0, 0  # 무작위 사건이 바로 뒤따르지 않게
        changes.append(f"갈래 사건: {event}")
        if event == "ALLEY" and "비밀1" not in state["revealed_secrets"]:
            state["revealed_secrets"].append("비밀1")  # 「그 골목」에서 털어놓음. _advance(4) 는 갈래가 있으면 건너뜀
            changes.append("비밀1 공개 (그 골목)")

    def _tick_events(self, state: dict, user_message: str) -> str | None:
        """턴을 세고, 사건 발동 조건이 맞으면 다음 턴에 쓸 사건을 pending_event에 넣는다."""
        state["turn"] += 1
        state["turns_since_event"] += 1
        if len(user_message.strip()) < SHORT_MESSAGE_LEN:
            state["short_streak"] += 1
        else:
            state["short_streak"] = 0

        if state["pending_event"] or state["ending"]:
            return None  # 이미 예약된 사건(DEPARTURE 등)이 있거나 엔딩 이후
        if (
            state["short_streak"] < SHORT_STREAK_TRIGGER
            and state["turns_since_event"] < QUIET_TURNS_TRIGGER
        ):
            return None
        candidates = [
            e for e in STAGE_EVENTS[state["stage"]]
            if state["turn"] - state["used_events"].get(e, -10_000) >= EVENT_COOLDOWN
            # 갈래가 정해지면 2~5단계에서 갈래 사건은 무작위로 나오지 않음(자기 갈래 사건은 정해진 때 한 번, 다른 갈래 것은 안 나옴)
            and not (state["route"] and 2 <= state["stage"] <= 5 and e in ROUTE_SCENES)
        ]
        if not candidates:
            return None
        event = random.choice(candidates)
        state["pending_event"] = event
        state["used_events"][event] = state["turn"]
        state["short_streak"] = 0
        state["turns_since_event"] = 0
        return event

    # ---------- 판정 ----------
    def _judge(self, state: dict, user_message: str, reply: str,
               need_summary: bool = False, recent: list[dict] | None = None) -> dict:
        prompt = (
            f"[현재 상태]\n단계: {state['stage']} ({state['episode_stage_name'] or STAGE_NAMES[state['stage']]}), "
            f"호감도: {state['affection']}, {state['day']}일째 {state['time_of_day']}, "
            f"장소: {state['location']}\n\n[지금까지의 요약]\n{state['summary'] or '없음'}"
        )
        if need_summary:
            convo = "\n".join(
                f"{'유저' if m.get('r') == 'u' else '서아'}: {m.get('t')}"
                for m in (recent or [])[-24:] if m.get("r") in ("u", "c")
            )
            prompt += f"\n\n[요약 요청]\n기존 요약과 아래 최근 대화를 합쳐 summary를 채운다.\n[최근 대화]\n{convo or '없음'}"
        prompt += f"\n\n[유저]\n{user_message}\n\n[서아]\n{reply}"
        neutral = {"affection_delta": 0, "flags": [], "time_advance": False,
                   "time_of_day": "", "summary": "", "reason": "판정 실패"}
        try:
            raw = self.llm.complete(self.blocks["JUDGE_SYSTEM"], prompt, json_mode=True)
            match = re.search(r"\{.*\}", raw, re.S)
            data = json.loads(match.group(0)) if match else {}
        except Exception:
            return neutral

        try:
            delta = int(data.get("affection_delta", 0))
        except (TypeError, ValueError):
            delta = 0
        flags = data.get("flags") or []
        return {
            "affection_delta": max(-10, min(10, delta)),
            "flags": [f for f in flags if f in ALLOWED_FLAGS] if isinstance(flags, list) else [],
            "time_advance": bool(data.get("time_advance", False)),
            "time_of_day": str(data.get("time_of_day") or "").strip(),
            "summary": str(data.get("summary") or "").strip(),
            "reason": str(data.get("reason", "")),
        }

    # ---------- 상태 갱신 ----------
    def _apply(self, state: dict, judge: dict, event: str | None) -> list[str]:
        changes = []

        cap = AFFECTION_CAP[state["stage"]]
        before = state["affection"]
        bonus = judge.get("talk_bonus", 0)
        state["affection"] = max(0, min(cap, before + judge["affection_delta"] + bonus))
        state["stage_bonus"] += bonus
        if state["affection"] != before:
            changes.append(f"호감도 {before} → {state['affection']}")

        for flag in judge["flags"]:
            if flag not in state["stage_flags"]:
                state["stage_flags"].append(flag)

        if judge["time_of_day"]:
            state["time_of_day"] = judge["time_of_day"]

        if judge["time_advance"]:
            state["day"] += 1
            changes.append(f"{state['day']}일째로 넘어감")
            if state["stage"] == 5 and state["departure_days_left"] is not None:
                state["departure_days_left"] = max(0, state["departure_days_left"] - 1)
                changes.append(f"출발까지 {state['departure_days_left']}일")
                marker = {2: "DAYS_LEFT_2", 1: "DAYS_LEFT_1"}.get(state["departure_days_left"])
                if marker and not state["episode"]:
                    state["pending_event"] = marker  # 다음 답장 지문 맨 앞에 한 번

        if (  # 남은 하루의 밤이 되면 한 번: 「오늘 밤이 지나면, 서아는 떠난다.」
            not state["episode"] and state["stage"] == 5 and state["departure_days_left"] == 1
            and not state["last_night_told"] and not state["pending_event"]
            and "밤" in state["time_of_day"]
        ):
            state["pending_event"] = "LAST_NIGHT"
            state["last_night_told"] = True

        if state["episode"]:  # 에피소드: 호감·플래그·시간대만. 비밀·단계 상승·출발·엔딩은 본 이야기 몫
            return changes

        if (
            "reveal_secret1" in judge["flags"]
            and state["stage"] >= 4
            and "비밀1" not in state["revealed_secrets"]
        ):
            state["revealed_secrets"].append("비밀1")
            changes.append("비밀1 공개")

        nxt = self._next_stage(state)
        if nxt:
            self._advance(state, nxt, changes)

        if state["stage"] == 5 and state["departure_days_left"] == 0 and not state["ending"]:
            state["ending"] = "departed"
            changes.append("엔딩: 서아가 도시를 떠남")

        return changes

    @staticmethod
    def _next_stage(state: dict) -> int | None:
        st, aff, flags = state["stage"], state["affection"], set(state["stage_flags"])
        if state.get("stage_user_msgs", 0) < MIN_STAGE_MSGS.get(st, 0):
            return None  # 호감·플래그가 차도 이 단계에서 이만큼은 대화해야 오름
        if st == 1 and aff >= 10 and flags & {"care", "protect"}:  # 첫날 안에 한 번 오르게 (앱과 같은 기준)
            return 2
        if st == 2 and aff >= 40 and "outside" in flags:
            return 3
        if st == 3 and aff >= 60 and "sincere" in flags:
            return 4
        if st == 4 and aff >= 75:
            return 5
        if st == 5 and "pursue" in flags and (state["departure_days_left"] or 0) > 0:
            return 6
        return None

    @staticmethod
    def _advance(state: dict, nxt: int, changes: list[str]) -> None:
        state["stage"] = nxt
        state["stage_flags"] = []
        state["stage_bonus"], state["stage_user_msgs"] = 0, 0  # 단계마다 새로 셈
        changes.append(f"단계 상승 → {nxt}. {STAGE_NAMES[nxt]}")
        if nxt in STAGE_UP_SCENES:
            state["pending_event"] = f"STAGE_UP_{nxt}"  # 다음 답장에 그 단계의 상징 장면 (다른 사건보다 먼저)
        if nxt == 4 and not state["route"] and "비밀1" not in state["revealed_secrets"]:
            state["revealed_secrets"].append("비밀1")  # 4단계 지침대로 1년 전 이야기를 털어놓게 함
            changes.append("비밀1 공개")
        if nxt == 5:
            state["departure_days_left"] = DEPARTURE_DAYS
            state["pending_event"] = "DEPARTURE"  # 다음 턴에 떠난다는 소식을 꺼냄
            changes.append(f"출발까지 {DEPARTURE_DAYS}일")
        if nxt == 6:
            state["departure_days_left"] = None
            state["ending"] = "lovers"

    # ---------- 요약 ----------
    def _maybe_summarize(self, sid: str, state: dict) -> None:
        unsummarized = self.store.messages_after(sid, state["summarized_upto"])
        if len(unsummarized) < SUMMARY_TRIGGER:
            return
        old = unsummarized[:-KEEP_RECENT]
        # 남는 부분이 user 메시지로 시작하도록 맞춘다 (user/assistant 순서 유지).
        while old and old[-1]["role"] == "user":
            old.pop()
        if not old:
            return
        dialogue = "\n".join(
            f"{'유저' if m['role'] == 'user' else '서아'}: {m['content']}" for m in old
        )
        prompt = f"[기존 요약]\n{state['summary'] or '없음'}\n\n[새 대화]\n{dialogue}"
        try:
            summary = self.llm.complete(SUMMARY_SYSTEM, prompt).strip()
        except Exception:
            return  # 요약에 실패하면 다음 턴에 다시 시도
        if summary:
            state["summary"] = summary
            state["summarized_upto"] = old[-1]["id"]
