"""대화 엔진. 한 턴마다 프롬프트 조립 → 서아 응답 → 판정 → 상태 갱신 → 저장을 한다.

게임 규칙(단계 조건, 호감도 상한, 사건 목록)은 맨 위 상수에서 바꾸면 된다.
"""
import json
import random
import re

from db import Store
from prompt_loader import load_blocks, render

# ---------- 게임 규칙 (여기서 조정) ----------
STAGE_NAMES = {1: "신경전", 2: "단골", 3: "끌림", 4: "고백 직전", 5: "엇갈림", 6: "연인"}
AFFECTION_CAP = {1: 30, 2: 50, 3: 70, 4: 85, 5: 95, 6: 100}
STAGE_EVENTS = {
    1: ["INJURY", "TAEO"],
    2: ["INJURY", "MARI", "TAEO", "BLACKOUT"],
    3: ["MARI", "STORM", "DATE", "ROOFTOP", "BLACKOUT"],
    4: ["STORM", "ROOFTOP", "TAEO"],
    5: ["TAEO"],
    6: ["MARI", "STORM", "DATE", "ROOFTOP", "BLACKOUT", "INJURY", "TAEO"],
}
ALLOWED_FLAGS = {"care", "protect", "outside", "sincere", "pursue", "reveal_secret1"}
SHORT_MESSAGE_LEN = 20      # 이보다 짧은 유저 메시지는 '짧은 답'
SHORT_STREAK_TRIGGER = 3    # 짧은 답이 이만큼 연속되면 사건 발동
QUIET_TURNS_TRIGGER = 15    # 사건 없이 이만큼 지나면 사건 발동
EVENT_COOLDOWN = 20         # 같은 사건을 다시 쓰기까지 필요한 턴 수
DEPARTURE_DAYS = 7          # 5단계 출발까지 남은 날
SUMMARY_TRIGGER = 40        # 요약 안 된 메시지가 이만큼 쌓이면 요약
KEEP_RECENT = 20            # 요약 후에도 원문으로 남겨 둘 최근 메시지 수

SUMMARY_SYSTEM = (
    "너는 롤플레이 대화 요약기다. 기존 요약과 새 대화를 합쳐 하나의 요약으로 만든다. "
    "중요한 사건, 관계 변화, 서아가 한 약속이나 고백, 유저가 기억해야 할 디테일을 포함한다. "
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
        "summarized_upto": 0,     # 이 메시지 id까지는 요약에 들어감
        "ending": None,           # None | "lovers" | "departed"
    }


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

        event = self._pick_event(state, user_message)
        system = self._build_system(state, event)
        history = self.store.messages_after(sid, state["summarized_upto"])
        messages = self._to_llm_messages(history) + [{"role": "user", "content": user_message}]

        reply = self.llm.chat(system, messages)  # 실패하면 예외 → 아무것도 저장하지 않음

        self.store.add_message(sid, "user", user_message)
        self.store.add_message(sid, "assistant", reply)

        judge = self._judge(state, user_message, reply)
        changes = self._apply(state, judge, event)
        self._maybe_summarize(sid, state)
        self.store.save_state(sid, state)

        return {"reply": reply, "event": event, "judge": judge, "changes": changes, "state": state}

    # ---------- 프롬프트 조립 ----------
    def _values(self, state: dict) -> dict:
        days = state["departure_days_left"]
        return {
            "stage": state["stage"],
            "stage_name": STAGE_NAMES[state["stage"]],
            "affection": state["affection"],
            "day": state["day"],
            "time_of_day": state["time_of_day"],
            "location": state["location"],
            "revealed_secrets": ", ".join(state["revealed_secrets"]) or "없음",
            "departure_days_left": "-" if days is None else days,
            "summary": state["summary"] or "아직 없음 (이야기의 시작)",
        }

    def _build_system(self, state: dict, event: str | None) -> str:
        values = self._values(state)
        parts = [
            self.blocks["BASE_SYSTEM"],
            render(self.blocks["STATE_BLOCK"], values),
            render(self.blocks[f"STAGE_{state['stage']}"], values),
        ]
        if event and f"EVENT_{event}" in self.blocks:
            parts.append(self.blocks[f"EVENT_{event}"])
        return "\n\n".join(parts)

    @staticmethod
    def _to_llm_messages(history: list[dict]) -> list[dict]:
        msgs = [{"role": m["role"], "content": m["content"]} for m in history]
        # Gemini는 대화가 user로 시작해야 하므로 앞에 시작 표시를 붙인다.
        if not msgs or msgs[0]["role"] == "assistant":
            msgs.insert(0, {"role": "user", "content": "(이야기 시작)"})
        return msgs

    # ---------- 사건 ----------
    def _pick_event(self, state: dict, user_message: str) -> str | None:
        state["turn"] += 1
        state["turns_since_event"] += 1
        if len(user_message) < SHORT_MESSAGE_LEN:
            state["short_streak"] += 1
        else:
            state["short_streak"] = 0

        if state["pending_event"]:
            event, state["pending_event"] = state["pending_event"], None
        elif state["ending"]:
            return None
        elif (
            state["short_streak"] >= SHORT_STREAK_TRIGGER
            or state["turns_since_event"] >= QUIET_TURNS_TRIGGER
        ):
            candidates = [
                e for e in STAGE_EVENTS[state["stage"]]
                if state["turn"] - state["used_events"].get(e, -10_000) >= EVENT_COOLDOWN
            ]
            if not candidates:
                return None
            event = random.choice(candidates)
        else:
            return None

        state["used_events"][event] = state["turn"]
        state["short_streak"] = 0
        state["turns_since_event"] = 0
        return event

    # ---------- 판정 ----------
    def _judge(self, state: dict, user_message: str, reply: str) -> dict:
        prompt = (
            f"[현재 상태]\n단계: {state['stage']} ({STAGE_NAMES[state['stage']]}), "
            f"호감도: {state['affection']}, {state['day']}일째 {state['time_of_day']}, "
            f"장소: {state['location']}\n\n[유저]\n{user_message}\n\n[서아]\n{reply}"
        )
        neutral = {"affection_delta": 0, "flags": [], "time_advance": False,
                   "location": "", "time_of_day": "", "reason": "판정 실패"}
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
            "location": str(data.get("location") or "").strip(),
            "time_of_day": str(data.get("time_of_day") or "").strip(),
            "reason": str(data.get("reason", "")),
        }

    # ---------- 상태 갱신 ----------
    def _apply(self, state: dict, judge: dict, event: str | None) -> list[str]:
        changes = []

        cap = AFFECTION_CAP[state["stage"]]
        before = state["affection"]
        state["affection"] = max(0, min(cap, before + judge["affection_delta"]))
        if state["affection"] != before:
            changes.append(f"호감도 {before} → {state['affection']}")

        for flag in judge["flags"]:
            if flag not in state["stage_flags"]:
                state["stage_flags"].append(flag)

        if judge["location"]:
            state["location"] = judge["location"]
        if judge["time_of_day"]:
            state["time_of_day"] = judge["time_of_day"]

        if judge["time_advance"]:
            state["day"] += 1
            changes.append(f"{state['day']}일째로 넘어감")
            if state["stage"] == 5 and state["departure_days_left"] is not None:
                state["departure_days_left"] = max(0, state["departure_days_left"] - 1)
                changes.append(f"출발까지 {state['departure_days_left']}일")

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
        if st == 1 and aff >= 20 and flags & {"care", "protect"}:
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
        changes.append(f"단계 상승 → {nxt}. {STAGE_NAMES[nxt]}")
        if nxt == 4 and "비밀1" not in state["revealed_secrets"]:
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
