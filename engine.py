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
STAGE_NAMES = {1: "경계", 2: "티키타카", 3: "썸", 4: "고백 직전", 5: "위기", 6: "연인"}  # 앱 단계 이름과 같게 (S4: 5 엇갈림 → 위기)
RIFT_LABEL = "엇갈림"  # S4: 위기에서 엇갈린 뒤의 stage_label
AFFECTION_CAP = {1: 30, 2: 50, 3: 70, 4: 85, 5: 95, 6: 100}
STAGE_EVENTS = {  # 무작위 사건. 마리·태오는 차례·단계 범위(SIDE_SCENE_STAGES)로 따로 고름
    1: ["INJURY"],
    2: ["INJURY", "BLACKOUT"],
    3: ["STORM", "ROOFTOP", "BLACKOUT"],  # DATE 는 2단계 장난 갈래 사건으로 옮김(앱 51)
    4: ["STORM", "ROOFTOP"],
    5: [],
    6: ["STORM", "ROOFTOP", "BLACKOUT", "INJURY"],  # 연인 일상(S4-1: DATE 위장 연인은 뺌, 겪은 갈래 장면도 뺌). 마리·태오 마무리는 첫 아침 뒤 따로 한 번
}
ALLOWED_FLAGS = {"care", "protect", "outside", "sincere", "pursue", "tease", "comfort", "reveal_secret1",
                 "refuse_name", "apologize"}
# S2 유저 이름: 3단계에 오르며 이름으로 부르고 싶다고 묻고, 거절하면 관계가 흔들린다 (본편만)
NAME_STATES = ("none", "asked", "accepted", "refused", "forgiven", "closed")
NAME_FLAGS = {"refuse_name", "apologize"}  # 판정 flags 로만 쓰고 stage_flags 에는 넣지 않음
NAME_EVENTS = {"NAME_REFUSED", "NAME_FORGIVEN", "NAME_REFUSED_AGAIN"}
NAME_REFUSE_PENALTY = 10        # 첫 거절: 호감 -10, 2단계로
NAME_REFUSE_AGAIN_PENALTY = 25  # 사과받은 뒤 또 거절: 호감 -25, 다시는 이름 안 부름
NAME_FORGIVE_MIN_AFFECTION = 40  # 사과로 3단계에 돌아갈 때 호감 최소
NAME_MAX_LEN = 8
NAME_EVENT_KEYS = {  # 이 원고 대사가 판정한 답장(last_reply)에 이미 있으면 그 사건을 다음 답장에 또 넣지 않음 (S2-1)
    "NAME_REFUSED": "그럼말구", "NAME_FORGIVEN": "넘어가줄게", "NAME_REFUSED_AGAIN": "두번은안물어봐",
}
NAME_QUIET_MSGS = 3  # 이름 사건을 정한 판정 뒤 유저 메시지 이만큼은 무작위 사건 없음
NAME_WATCH_LINES = {"asked": "asked", "accepted": "asked", "refused": "refused", "forgiven": "forgiven"}  # name_state → NAME_WATCH 줄
NAME_STRIP = re.compile(r'[\r\n{}"「」]')
NAME_CALL = {  # STATE_BLOCK 「이름 부르기」
    "none": "아직 안 부름", "asked": "아직 안 부름",
    "accepted": "진지한 순간에 이름", "forgiven": "진지한 순간에 이름",
    "refused": "거절당함, 사과를 기다림. 이름 부르지 않음",
    "closed": "다시는 이름 부르지 않음",
}
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
HISTORY_MESSAGES = 80       # 대화 호출에 넣을 최근 기록 (40턴 = 유저 40 + 서아 40, S3)
PROMISES_MAX = 5            # S3: 서아가 한 약속·제안은 최근 이만큼
MEMO_MAX_LEN = 200          # S3: 단계 기록·약속 한 줄 길이 상한
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
ROUTE_EVENT_AFFECTION = {2: 15, 3: 28, 4: 42}  # S4: 단계 조건(25·40·55)보다 먼저 나오게
ROUTE_SCENES = {"DATE", "WALK_HOME", "STORM", "ROOFTOP", "ALLEY"}
SCENE_JUDGES = 2  # 예전 값(장면 블록을 넣을 판정 수). S4-1 부터 마침은 SCENE_REPLIES_DONE 으로 셈, 키는 호환용
SCENE_REPLIES_DONE = 4  # S4-1: 장면 시작 답장 + 그 뒤 유저 메시지 3개가 정상 응답(예비 지문 아님)으로 지나가면 마침
FALLBACK_TEXTS = {"…", "..."}  # 예비 지문(FALLBACK) 답장. 판정한 답장이 이것이면 장면·사건 답장으로 세지 않음
BAR_LOCATIONS = {"바 '라스트 오더' 1층", "바 '라스트 오더' 2층"}
STAGE_UP_SCENES = {2, 3, 4, 6}  # 단계가 오른 다음 답장에 한 번 넣는 상징 장면(STAGE_UP_n). 5는 DEPARTURE
# 대화량 보조 점수: S3 에서 없앰(언제나 0). 필드는 앱 호환을 위해 남김
TALK_BONUS_STAGE_MAX = 8   # 단계마다 보너스 합계 상한(단계가 오르면 0부터)
MIN_STAGE_MSGS = {1: 3, 2: 9, 3: 9, 4: 6}  # 그 단계에서 이만큼 유저 메시지를 보내야 다음 단계로(S4. 5→6 은 위기 판정)
# S4 속마음 힌트: 최소 수를 넘겼는데 다음 단계 조건이 안 찼으면 다음 답장에 한 번, 그래도 그대로면 HINT_AGAIN_MSGS 뒤 한 번 더
HINT_OF_STAGE = {1: "HINT_1", 2: "HINT_2", 3: "HINT_3", 4: "HINT_4"}
HINT_BLOCKS = {"HINT_1", "HINT_2", "HINT_3", "HINT_4", "HINT_5", "HINT_6"}  # 5: 위기 3턴째 · 6: 엇갈림 → 화해
HINT_MAX = 2
HINT_AGAIN_MSGS = 10
HINT_LOOKBACK = 6          # /chat: 최근 서아 답장 이만큼에 그 줄이 이미 있으면 다시 넣지 않음
# S4-B 위기 → 연인 / 엇갈림 → 화해 (본편 5단계)
CRISIS_STATES = ("none", "on", "rift", "done")
CRISIS_EVENTS = {"CRISIS", "RIFT_START", "RECONCILE_OPEN"}
CRISIS_LOCATIONS = {"tease": "네온 골목", "care": "바 옥상"}
CRISIS_HINT_MSGS = 3       # 위기 유저 메시지 3개째 뒤 HINT_5
CRISIS_MAX_MSGS = 6        # 위기 유저 메시지 6개가 지나도록 마음 확인이 없으면 엇갈림
RIFT_AFFECTION = 40        # 엇갈림이 되면 호감
RIFT_OPEN_MSGS = 6         # 엇갈림(다음 밤부터) 유저 메시지 6개 뒤 RECONCILE_OPEN
RECONCILE_HINT_MSGS = 3    # RECONCILE_OPEN 뒤 3개 지나 HINT_6
RECONCILE_MIN_AFFECTION = 55
# S4-C 밤 넘기기: 경계 → 티키타카, 티키타카 → 썸 뒤(상징 장면 다음부터 메시지 4개) · 엇갈림이 된 뒤
NEXT_NIGHT = "[다음 밤]"       # 앱이 시간 카드 뒤 보내는 유저 메시지 (유저의 말이 아님)
NIGHT_NEXT = {  # night_next → (시간 카드 문구 night_card, 넘기는 날 수, 마무리 블록)
    "NIGHT_START_2": ("2일째 밤", 1, "NIGHT_END_1"),
    "NIGHT_START_3_TEASE": ("며칠 뒤 밤", 3, "NIGHT_END_TEASE"),
    "NIGHT_START_3_CARE": ("며칠 뒤 밤", 3, "NIGHT_END_CARE"),
    "RIFT_NIGHT": ("며칠 뒤 밤", 3, None),  # 마무리는 RIFT_START 가 대신함
}
NIGHT_END_STAGES = (2, 3)
NIGHT_END_AFTER = 5         # 그 단계 유저 메시지(상징 장면을 받은 메시지 + 그 뒤 4개)
NIGHT_WAIT_MAX = 6          # 예전 앱처럼 「[다음 밤]」이 안 오면 유저 메시지 이만큼 뒤 서버가 밤을 넘김(첫마디 없이)
NIGHT_LOCATION = "바 '라스트 오더' 1층"
WON_KEYS = {"WON_TEASE": "연기아니지", "WON_CARE": "내려가자", "WON_RECONCILE": "늦었잖아"}  # 판정한 답장에 이 원고 대사가 있으면 다시 넣지 않음
GIFT_MOMENT_GAP = 10       # 같은 순간 선물은 준 뒤 유저 메시지 이만큼 지나야 다시 엶
FIRST_NIGHT_TURNS = 3     # 첫날 밤 사정(손등의 피)은 유저 메시지 이만큼까지만 넣음. 그 뒤엔 같은 묘사가 반복되지 않게 뺌
GIFT_MOMENT_QUIET = 3      # 순간 선물을 준 뒤 이만큼은 어떤 순간 선물도 열지 않음(자주 넣지 않기)
# 조연 (앱 54): 갈래마다 한 명, 나올 때마다 한 걸음씩(처음 1 → 두 번째 2 → 마무리 3)
SIDE_CHARS = ("MARI", "TAEO")
SIDE_STEP_KEYS = ("mari_step", "taeo_step")
SIDE_OF_ROUTE = {"tease": "MARI", "care": "TAEO"}
SIDE_SCENE_STAGES = {  # 조연 → 차례(1 처음, 2 두 번째) → 나올 수 있는 단계. 범위 밖이면 건너뛰지 않고 기다림
    "MARI": {1: {2, 3}, 2: {3, 4}},
    "TAEO": {1: {1, 2}, 2: {4}},
}
OTHER_ROUTE_FIRST = {"MARI": {3}, "TAEO": {1, 2}}  # 다른 갈래의 조연은 이 단계에서 「처음」 한 번만, 나오면 닫음
SIDE_WEIGHT = 2              # 갈래가 정해지면 그 갈래 조연을 두 배 자주
SIDE_EVENTS = {f"{c}_{n}" for c in SIDE_CHARS for n in (1, 2, 3)}  # pending_event 값: 조연_차례
ENDINGS = ("stayed", "departed")  # 성공(앱 54, 6단계 첫 아침·조연 마무리 뒤) · 실패(5단계 남은 날 0)
STAYED_MAX_MSGS = 15         # 6단계 유저 메시지가 이만큼 되면 조연 마무리 없이 stayed
MORNING_KEYS = {"tease": ("셔츠", "빌렸어"), "care": ("해뜨는것도",)}  # 첫 아침 원고 대사(공백 빼고)가 답장에 있으면 첫 아침이 나온 것
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
    "서아가 직접 한 제안·약속·발언과 유저의 고백·약속을 가장 먼저 남긴다. "
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
        "ending": None,           # None | "stayed"(성공, 그 뒤에도 그대로 둠) | "departed"(실패)
        "morning_done": False,    # 6단계 첫 아침 장면이 나왔는지 (서버가 판정에서 정함)
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
        "user_name": "",          # S2: 앱 시작 화면의 닉네임(1~8자). 서버는 저장하지 않음
        "name_state": "none",     # NAME_STATES
        "judge_soon": False,      # name_state 가 asked·refused 면 true: 앱이 다음 답마다 바로 판정
        "name_quiet": 0,          # S2-1: 이름 사건 뒤 무작위 사건을 쉬는 남은 유저 메시지 수
        "stage_notes": {},        # S3: 끝난 단계마다 한 문장 {"1": "…"} (본편만, 요약이 바뀌어도 그대로)
        "stage_note_due": None,   # S3: 다음 판정에 한 문장을 부탁할 끝난 단계 번호
        "promises": [],           # S3: 서아가 한 제안·약속 최근 PROMISES_MAX 개 (본편만)
        "hint_due": None,         # S4: 다음 답장에 넣을 속마음 힌트 블록 이름. 다음 판정이 비움
        "stage_hints": 0,         # S4: 이 단계에서 낸 속마음 힌트 수 (최대 HINT_MAX)
        "hint_msgs": 0,           # S4: 마지막 힌트를 정할 때의 stage_user_msgs (엇갈림에서는 crisis_msgs)
        "crisis": "none",         # S4: CRISIS_STATES. on 위기 중 · rift 엇갈림 · done 마음 확인(6단계)
        "crisis_msgs": 0,         # S4: 위기·엇갈림에서 보낸 유저 메시지 수 (엇갈림이 되면 0부터)
        "stage_label": "경계",    # S4: 앱 표시용. crisis 가 rift 면 「엇갈림」, 아니면 단계 이름
        "rewind_offer": False,    # S4: 엇갈림이 된 판정에서만 true (앱이 되감기 권유를 한 번)
        "won_line": None,         # S4: 마음을 확인한 답장에 성공 대사가 없었으면 다음 답장에 넣을 WON_* 블록
        "night_next": None,       # S4-C: 다음 밤 첫마디 블록(NIGHT_NEXT). 있으면 앱이 마무리에 한 번 답한 뒤 시간 카드 → 「[다음 밤]」
        "night_card": None,       # S4-C: 시간 카드 문구 「2일째 밤」·「며칠 뒤 밤」
        "night_wait": 0,          # S4-C: night_next 를 정한 뒤 「[다음 밤]」 없이 온 유저 메시지 수
        "scene_msgs": 0,          # S4-1: 열린 갈래 장면에서 정상으로 나간 서아 답장 수(시작 답장 포함). SCENE_REPLIES_DONE 이면 마침
    }


def clean_user_name(raw) -> str:
    """앞뒤 공백을 자르고 줄바꿈·{ } " 「 」를 빼고 8자까지."""
    if not isinstance(raw, str):
        return ""
    return NAME_STRIP.sub("", raw).strip()[:NAME_MAX_LEN].strip()


def name_josa(name: str) -> str:
    """끝 글자가 받침 있는 한글이면 「이라고」, 아니면 「라고」. 도윤이라고 · 민지라고 · Alex라고."""
    last = (name or "")[-1:]
    if last and 0xAC00 <= ord(last) <= 0xD7A3 and (ord(last) - 0xAC00) % 28:
        return "이라고"
    return "라고"


def name_call(state: dict) -> str:
    """「이름 부르기」 문장. 유저 이름이 없는 예전 앱은 4단계부터 대화에서 알게 된 이름을 (예전처럼)."""
    if not state.get("user_name"):
        return NAME_CALL["accepted"] if state.get("stage", 1) >= 4 else NAME_CALL["none"]
    return NAME_CALL.get(state.get("name_state") or "none", NAME_CALL["none"])


def stage_label(state: dict) -> str:
    """S4: 앱 표시용 단계 이름. 엇갈림이면 「엇갈림」."""
    return RIFT_LABEL if state.get("crisis") == "rift" else STAGE_NAMES[state["stage"]]


def judge_soon(state: dict) -> bool:
    """앱이 다음 답마다 바로 판정할 때: 이름을 물은 뒤·사과를 기다릴 때(S2), 위기 중·화해의 문이 열린 뒤(S4)."""
    if state.get("name_state") in ("asked", "refused"):
        return True
    if state.get("episode"):
        return False
    used = state.get("used_events") if isinstance(state.get("used_events"), dict) else {}
    return state.get("crisis") == "on" or (state.get("crisis") == "rift" and "RECONCILE_OPEN" in used)


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
    if state["night_next"] not in NIGHT_NEXT or state["episode"]:
        state["night_next"] = None
    state["night_card"] = NIGHT_NEXT[state["night_next"]][0] if state["night_next"] else None
    for key in ("stage_bonus", "stage_user_msgs", "stage_hints", "hint_msgs", "crisis_msgs", "night_wait"):
        try:
            state[key] = max(0, int(state[key]))
        except (TypeError, ValueError):
            state[key] = 0
    if state["ending"] not in ENDINGS:  # 예전 "lovers" 는 None 으로 (6단계 성공 신호는 이제 stayed)
        state["ending"] = None
    state["morning_done"] = state["morning_done"] is True
    if state["route"] not in ROUTES:
        state["route"] = None
    if state["scene"] not in ROUTE_SCENES:
        state["scene"] = None
    state["scene_judges_left"] = max(0, min(SCENE_JUDGES, int(state["scene_judges_left"] or 0))) if state["scene"] else 0
    if not state["scene"]:
        state["scene_msgs"] = 0
    elif "scene_msgs" not in raw:  # S4 저장(판정 수로 셈): 판정 하나 ≈ 답장 3개로 옮김
        state["scene_msgs"] = (SCENE_JUDGES - state["scene_judges_left"]) * 3 if state["scene_judges_left"] else 0
    state["scene_msgs"] = max(0, state["scene_msgs"])
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
    # S4-B 위기: 본편 5단계는 위기·엇갈림. 예전 5단계(출발까지 남은 날) 저장은 위기 중으로 이어 감
    if state["crisis"] not in CRISIS_STATES:
        state["crisis"] = "none"
    if not state["episode"]:
        state["departure_days_left"] = None  # 키만 남김(앱 호환). 본편에서 더는 쓰지 않음
        if state["stage"] == 5 and state["crisis"] not in ("on", "rift"):
            state["crisis"] = "on"
        elif state["stage"] < 5:
            state["crisis"] = "none"
        elif state["stage"] == 6:
            state["crisis"] = "done"
    state["rewind_offer"] = state["rewind_offer"] is True
    if state["won_line"] not in WON_KEYS:
        state["won_line"] = None
    ev = state["pending_event"]
    if isinstance(ev, str):
        ev = ev.removeprefix("EVENT_")
        if ev == "DEPARTURE" and not state["episode"]:
            ev = "CRISIS"  # 예전 앱이 5단계에 막 오른 상태를 보내면 위기 장면으로
        known = ({e for v in STAGE_EVENTS.values() for e in v} | ROUTE_SCENES | SIDE_EVENTS | set(SIDE_CHARS)
                 | {f"STAGE_UP_{n}" for n in STAGE_UP_SCENES} | NAME_EVENTS | CRISIS_EVENTS | {"NIGHT_END"})
        state["pending_event"] = ev if ev in known else None  # DEPARTURE·DAYS_LEFT_*·LAST_NIGHT 는 버림(S4)
    else:
        state["pending_event"] = None
    state["stage_label"] = stage_label(state)
    state["user_name"] = clean_user_name(state["user_name"])
    if state["name_state"] not in NAME_STATES:
        state["name_state"] = "none"
    state["judge_soon"] = judge_soon(state)
    try:
        state["name_quiet"] = max(0, min(NAME_QUIET_MSGS, int(state["name_quiet"])))
    except (TypeError, ValueError):
        state["name_quiet"] = 0
    state["recent_lines"] = [str(x) for x in state["recent_lines"]][-3:]
    notes = state["stage_notes"] if isinstance(state["stage_notes"], dict) else {}
    state["stage_notes"] = {
        str(k): v.strip()[:MEMO_MAX_LEN] for k, v in notes.items()
        if str(k) in {str(n) for n in STAGE_NAMES} and isinstance(v, str) and v.strip()
    }
    try:
        due = int(state["stage_note_due"]) if state["stage_note_due"] is not None else None
    except (TypeError, ValueError):
        due = None
    state["stage_note_due"] = due if due in STAGE_NAMES else None
    promises = state["promises"] if isinstance(state["promises"], list) else []
    state["promises"] = [x.strip()[:MEMO_MAX_LEN] for x in promises if isinstance(x, str) and x.strip()][-PROMISES_MAX:]
    if state["hint_due"] not in HINT_BLOCKS or state["episode"]:
        state["hint_due"] = None
    state["stage_flags"] = [f for f in state["stage_flags"] if f in ALLOWED_FLAGS - NAME_FLAGS]
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
        state["_first_night"] = sum(1 for h in history if h.get("r") == "u") < FIRST_NIGHT_TURNS
        event = state["pending_event"]
        # S4-C: 앱의 「[다음 밤]」 → 다음 밤 첫마디 (night_next 가 있을 때만, 유저의 말로 다루지 않음)
        night = state["night_next"] if user_message.strip() == NEXT_NIGHT and not state["episode"] else None
        state["_night"] = night
        if night:
            event = None
        state["_hint"] = None if event or night else self._hint_now(state["hint_due"], history)
        system = self._build_system(state, event, motion_list)
        opening = self.blocks["OPENING"].strip()
        # OPENING은 모델이 만들지 않고 고정. 에피소드는 앱이 자기 첫 장면을 기록으로 보내므로 바 오프닝을 넣지 않음
        msgs = [] if state["episode"] else [{"role": "assistant", "content": opening}]
        for m in history[-HISTORY_MESSAGES:]:
            text = str(m.get("t") or "").strip()
            if not text or text == opening or m.get("r") not in ("u", "c") or text == NEXT_NIGHT:
                continue  # 「[다음 밤]」은 빼서 앞뒤 서아 말이 이어지게(절대 규칙 15: 그 사이 시간이 흘렀을 수 있다)
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
                scene = state["scene"]  # S4-1: 시작 답장이 예비 지문이었으면 장면의 첫 정상 답장이 장소를 옮김
                if (not out["location"] and not event and not night and scene in EVENT_LOCATIONS
                        and not state["scene_msgs"] and state["location"] != EVENT_LOCATIONS[scene]):
                    out["location"] = EVENT_LOCATIONS[scene]
                if event == "CRISIS" and not state["episode"]:  # S4: 위기 장소는 갈래로 정함
                    out["location"] = CRISIS_LOCATIONS[state["route"] or "care"]
                if night:  # S4-C: 다음 밤은 바 1층에서
                    out["location"] = NIGHT_LOCATION
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

    def hint_line(self, name: str) -> str:
        """속마음 힌트 블록의 그 줄(「그대로: 」 뒤)."""
        return self.blocks[name].split("그대로: ", 1)[-1].strip()

    def _hint_now(self, name: str | None, history: list[dict]) -> str | None:
        """S4: 힌트가 정해져 있고, 최근 서아 답장에 그 줄이 아직 없으면 그 블록 이름(판정 사이 여러 답장에 되풀이되지 않게)."""
        if not name or name not in self.blocks:
            return None
        key = re.sub(r"\s+", "", self.hint_line(name))[:12]
        recent = [str(h.get("t") or "") for h in history if h.get("r") == "c"][-HINT_LOOKBACK:]
        return None if any(key in re.sub(r"\s+", "", t) for t in recent) else name

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
        lines = [m.strip() for m in (last_user or "").split("\n") if m.strip()]
        msgs = [m for m in lines if m != NEXT_NIGHT]  # S4-C: 「[다음 밤]」은 유저의 말이 아님
        next_night = len(msgs) < len(lines) and not state["episode"]
        real_user = "\n".join(msgs)
        if next_night and not msgs and not need_summary:  # 판정할 말이 없음: 호출하지 않음
            judge = {"affection_delta": 0, "flags": [], "time_advance": False, "time_of_day": "", "summary": "",
                     "stage_note": "", "promise": "", "reason": "다음 밤"}
        else:
            judge = self._judge(state, real_user if next_night else last_user, last_reply, need_summary, recent or [])
        if next_night:  # 날짜는 밤 넘기기가 정함. 「[다음 밤]」만 왔으면 호감·플래그 0
            judge.update(time_advance=False, time_of_day="")
            if not msgs:
                judge.update(affection_delta=0, flags=[], promise="")
        progress = None
        fallback = (last_reply or "").strip() in FALLBACK_TEXTS  # S4-1: 판정한 답장이 예비 지문
        changes: list[str] = []
        if not state["episode"]:  # 대화량 점수·메시지 수는 본 이야기만
            state["name_quiet"] = max(0, min(NAME_QUIET_MSGS, state["name_quiet"]) - len(msgs))
            judge["talk_bonus"] = max(0, min(self._talk_bonus(msgs, judge), TALK_BONUS_STAGE_MAX - state["stage_bonus"]))
            state["stage_user_msgs"] += len(msgs)
            state["hint_due"] = None  # S4: 앞 판정이 정한 힌트는 그 사이 답장에 나갔음
            state["won_line"], state["rewind_offer"] = None, False  # S4: 한 번만
            self._tick_scene(state, len(msgs), fallback, changes)  # 단계 조건이 갈래 장면 「마침」을 보므로 단계 판단보다 먼저
            changes += self._remember(state, judge)
        changes += self._apply(state, judge, None, last_reply)
        if not state["episode"] and state["night_next"]:  # S4-C 밤 넘기기
            if next_night:
                self._next_night(state, changes)
            else:
                state["night_wait"] += len(msgs)
                if state["night_wait"] >= NIGHT_WAIT_MAX:
                    self._next_night(state, changes, f"「{NEXT_NIGHT}」 없이 메시지 {state['night_wait']}개")
        if not state["episode"] and stage_before == 5:
            self._crisis(state, judge, last_reply, len(msgs), changes)
        if not state["episode"] and state["stage"] != stage_before:
            state["stage_note_due"] = stage_before  # S3: 끝난 단계. 다음 판정에 한 문장을 부탁함
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
            route_event = None if self._name_scene_quiet(state) else self._route_event(state, judge["flags"], changes)
            if route_event:
                self._start_route_scene(state, route_event, changes)
            if stage_before == 6:  # 6단계: 첫 아침 → 그 갈래 조연 마무리 → stayed (오른 판정은 STAGE_UP_6 차례)
                self._side_finale(state, last_reply, judge, changes)
            if state["stage"] == stage_before:
                self._night_end(state, changes)
        # 에피소드 중에는 앱이 단계·장면을 정하므로 사건 계산을 건너뛴다 (단계 상승은 _apply에서 건너뜀)
        nxt_event = None if state["episode"] else self._tick_events(state, real_user if next_night else last_user)
        if nxt_event:
            changes.append(f"다음 턴 사건: {nxt_event}")
        if not state["episode"] and state["stage"] == stage_before:
            self._hint(state, changes)
        state["judge_soon"] = judge_soon(state)
        state["stage_label"] = stage_label(state)
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
            "user_name": state.get("user_name") or "없음",
            "stage_notes": "\n".join(
                f"  - {k}단계({STAGE_NAMES[int(k)]}): {v}"
                for k, v in sorted((state.get("stage_notes") or {}).items(), key=lambda kv: int(kv[0]))
            ) or "  - 없음",
            "promises": "\n".join(f"  - {x}" for x in state.get("promises") or []) or "  - 없음",
            "name_josa": name_josa(state.get("user_name") or ""),
            "name_call": name_call(state),
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
        parts = [self.blocks["BASE_SYSTEM"]]
        episode = EPISODES.get(state.get("episode") or "")
        if episode:  # 본편 세계(MAIN_WORLD)·상태·STAGE_n·EVENT_*·첫날 줄 대신 에피소드 블록 (판정은 그대로)
            if state.get("_xmas_late"):  # reply() 가 구간·기록을 보고 정함
                values["xmas_late"] = state["_xmas_late"]
            ep_state = self.blocks["EPISODE_STATE"]
            if state.get("episode_turns_left") is None:  # 앱이 안 보내면 「자정까지」 줄을 통째로 뺀다
                ep_state = "\n".join(x for x in ep_state.split("\n") if "{{episode_turns_left}}" not in x)
            else:
                values["episode_turns_left"] = state["episode_turns_left"]
            parts += [
                render(ep_state, values),
                render(self.blocks[episode["block"]], values),
                *([render(self.blocks["XMAS_LATE"], values)] if "xmas_late" in values else []),
                render(self.blocks["MOTION_BLOCK"], values),
                self.blocks["OUTPUT_FORMAT"],
            ]
            return "\n\n".join(parts)
        parts += [self.blocks["MAIN_WORLD"], render(self.blocks["STATE_BLOCK"], values)]
        if state["gift_moments"]:  # 앱 50: 열 수 있는 순간 선물이 있을 때만
            values["gift_moments"] = "\n".join(f"- {g['id']} {g['name']}: {g['when']}" for g in state["gift_moments"])
            parts.append(render(self.blocks["GIFT_MOMENT"], values))
        if state["day"] == 1 and state.get("_first_night", True):  # 앱 37 골목 프롤로그: 첫날 밤, 처음 몇 턴만(계속 넣으면 매 답장에 손등의 피가 나옴)
            parts.append(self.blocks["FIRST_NIGHT"])
        parts += [
            render(self.blocks[f"STAGE_{state['stage']}"], values),
            render(self.blocks["MOTION_BLOCK"], values),
            self.blocks["OUTPUT_FORMAT"],
        ]
        watch = self._name_watch(state, event, values)
        if watch:
            parts.append(watch)
        parts += self._crisis_parts(state, event)  # S4-B 위기·엇갈림 (ROUTE_PURSUE_* 는 넣지 않음)
        if state["stage"] == 6 and state["route"] and not state["morning_done"] and state["ending"] != "stayed":
            parts.append(self.blocks[f"ROUTE_MORNING_{state['route'].upper()}"])  # 첫 아침이 나오면 다시 넣지 않음
        if event in ("MARI", "TAEO"):  # 예전 값: 차례로 고름
            event = f"{event}_{max(1, min(3, state[event.lower() + '_step']))}"
        night = self._night_block(state, event)
        if night:  # S4-C: 밤 마무리(NIGHT_END) 또는 「[다음 밤]」 첫마디. 이 답장에는 갈래 장면을 잇지 않음
            parts.append(self.blocks[night])
        elif event and f"EVENT_{event}" in self.blocks:  # 이번 답장의 사건(시작 턴)
            parts.append(render(self.blocks[f"EVENT_{event}"], values))  # NAME_FORGIVEN 은 {{user_name}}
        elif event and event.startswith("STAGE_UP_") and event in self.blocks:
            block = self._stage_up_block(state, event)
            if block:  # 단계 오른 직후 한 번: 앱이 답장 뒤 pending_event 를 비움
                parts.append(render(self.blocks[block], values))
        elif state["scene"]:  # 다른 사건이 없으면 진행 중인 갈래 장면을 이어 감
            parts.append(self.blocks[f"EVENT_{state['scene']}"])
        if "ALLEY" in (event, None if event else state["scene"]) and state["route"] == "care":
            parts.append(self.blocks["EVENT_ALLEY_CARE"])
        shown_scene = event if event else state["scene"]  # 이번 답장의 갈래 장면(시작 또는 이어 감)
        if not night and shown_scene in ("DATE", "WALK_HOME") and state["location"] not in BAR_LOCATIONS:
            parts.append(render(self.blocks["ROUTE_OUTSIDE"], values))  # S4-1: 이미 밖이면 바에서 다시 출발하지 않음
        if state.get("_hint") and not event:  # S4 속마음 힌트: 사건·이름 장면이 있는 답장에는 넣지 않음 (reply() 가 정함)
            parts.append(self.blocks[state["_hint"]])
        return "\n\n".join(parts)

    def _name_watch(self, state: dict, event: str | None, values: dict) -> str | None:
        """S2-1: 본편·유저 이름이 있을 때, 이번 유저 메시지로 이름 장면이 바로 나오게 name_state 에 맞는 줄만.
        이름 사건이나 이름을 묻는 장면(STAGE_UP_3)이 이번 답장에 이미 있으면 넣지 않음."""
        key = NAME_WATCH_LINES.get(state.get("name_state") or "")
        if not key or not state.get("user_name") or state.get("episode") or event in NAME_EVENTS or event == "STAGE_UP_3":
            return None
        lines = self.blocks["NAME_WATCH"].split("\n")
        picked = [line.split("] ", 1)[1] for line in lines if line.startswith(f"[{key}] ")]
        head = [line for line in lines if not line.startswith("[")]
        return render("\n".join(head + picked), values)

    @staticmethod
    def _night_block(state: dict, event: str | None) -> str | None:
        """S4-C: 「[다음 밤]」이면 night_next 첫마디 블록, NIGHT_END 면 night_next 에 맞는 마무리 블록."""
        if state.get("_night"):
            return state["_night"]
        if event != "NIGHT_END" or state.get("episode"):
            return None
        nxt = state.get("night_next")
        if nxt in NIGHT_NEXT and NIGHT_NEXT[nxt][2]:
            return NIGHT_NEXT[nxt][2]
        return "NIGHT_END_1" if state["stage"] <= 2 else f"NIGHT_END_{(state.get('route') or 'care').upper()}"

    def _crisis_parts(self, state: dict, event: str | None) -> list[str]:
        """S4-B: 위기 장면(CRISIS_갈래, 시작 답장과 위기 중) + CRISIS_WATCH(갈래 줄만) · 엇갈림 RIFT + RECONCILE_WATCH ·
        마음 확인 답장에 성공 대사가 없었으면 STAGE_UP_6 앞에 WON_*. RIFT_START·RECONCILE_OPEN 은 EVENT_ 블록으로 따로."""
        if state.get("episode"):
            return []
        route = (state.get("route") or "care").upper()
        crisis, used = state.get("crisis"), state.get("used_events") or {}
        if event == "STAGE_UP_6" and state.get("won_line"):
            return [self.blocks[state["won_line"]]]
        if crisis == "on" and state["stage"] == 5:
            parts = [self.blocks[f"CRISIS_{route}"]]
            if event != "CRISIS":  # 소식을 꺼내는 답장은 유저가 아직 그 소식을 모름
                lines = self.blocks["CRISIS_WATCH"].split("\n")
                parts.append("\n".join([x for x in lines if not x.startswith("[")]
                                       + [x.split("] ", 1)[1] for x in lines if x.startswith(f"[{route.lower()}] ")]))
            return parts
        if crisis == "rift" and state["stage"] == 5:
            parts = [] if event == "RIFT_START" else [self.blocks["RIFT"]]
            if "RECONCILE_OPEN" in used and event != "RECONCILE_OPEN":
                parts.append(self.blocks["RECONCILE_WATCH"])
            return parts
        return []

    @staticmethod
    def _name_scene_quiet(state: dict) -> bool:
        """이름을 물은 뒤·사과를 기다리는 동안, 그리고 이름 사건 직후 유저 메시지 몇 개 동안은 사건을 쉼."""
        return state.get("name_state") in ("asked", "refused") or state.get("name_quiet", 0) > 0

    @staticmethod
    def _stage_up_block(state: dict, event: str) -> str | None:
        """S2: 이름을 물을 차례면 STAGE_UP_3_NAMED. 「…너 이름, 아직 안 물어봤네.」(STAGE_UP_4)는 유저 이름이 없고 name_state none 일 때만."""
        named = bool(state.get("user_name"))
        if event == "STAGE_UP_3" and named and state.get("name_state") == "asked":
            return "STAGE_UP_3_NAMED"
        if event == "STAGE_UP_4" and (named or state.get("name_state") != "none"):
            return None
        return event

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
        """S3: 메시지 길이 보너스를 없앰. 언제나 0.
        talk_bonus·stage_bonus 필드는 앱 호환을 위해 그대로 두고 값만 0 (예전 계산은 git 기록 [S3-B] 이전)."""
        return 0

    @staticmethod
    def _tick_scene(state: dict, n_msgs: int, fallback: bool, changes: list[str]) -> None:
        """S4-1: 열린 갈래 장면에서 정상으로 나간 답장을 센다(이번 묶음 메시지 수, 판정한 답장이 예비 지문이면 하나 뺌).
        시작 답장 + 그 뒤 유저 메시지 3개(SCENE_REPLIES_DONE)가 지나면 마침. 예약만 된 장면(앱이 아직 사건 답장을 내지 않음)은 세지 않음."""
        scene = state["scene"]
        if not scene or state["pending_event"] == scene:
            return
        state["scene_msgs"] += max(0, n_msgs - (1 if fallback else 0))
        if state["scene_msgs"] >= SCENE_REPLIES_DONE:
            state["scene"], state["scene_judges_left"], state["scene_msgs"] = None, 0, 0
            changes.append(f"갈래 장면 마침: {scene}")

    @staticmethod
    def _drop_scene(state: dict, changes: list[str], why: str) -> None:
        """S4-1: 마치지 못하고 닫히는 장면(밤 넘기기·단계 하락)은 「마침」으로 치지 않음 → 조건이 되면 다시 시작."""
        scene = state["scene"]
        if not scene:
            return
        state["used_events"].pop(f"ROUTE_{scene}", None)
        if state["pending_event"] == scene:
            state["pending_event"] = None
        state["scene"], state["scene_judges_left"], state["scene_msgs"] = None, 0, 0
        changes.append(f"갈래 장면 {scene} 중단({why}) → 마치지 않은 것으로")

    @staticmethod
    def _pick_route(state: dict) -> str:
        """S4-1: 앱이 갈래를 정하기 전에 갈래 사건 차례가 오면 서버가 정함. 이 단계 플래그에 tease 만 있으면 tease, 그 밖(동점·없음)은 care."""
        flags = set(state["stage_flags"])
        return "tease" if "tease" in flags and "care" not in flags else "care"

    @classmethod
    def _route_event(cls, state: dict, flags: list[str] | None = None, changes: list[str] | None = None) -> str | None:
        """갈래 사건: 그 단계에서 호감이 기준에 닿으면 한 번(앱 51).
        S4-1: 2단계는 유저가 먼저 밖에 나가자고 하면(이번 판정 outside) 호감 기준 전이라도 시작. route 가 없으면 서버가 정함."""
        route, stage = state["route"], state["stage"]
        if state["ending"] or state["pending_event"] or state["scene"] or stage not in ROUTE_EVENTS:
            return None
        if stage in NIGHT_END_STAGES and (f"NIGHT_END_{stage}" not in state["used_events"] or state["night_next"]):
            return None  # S4-C: 2·3단계 갈래 장면은 밤을 넘긴 다음 밤에
        early = stage == 2 and "outside" in (flags or [])
        if state["affection"] < ROUTE_EVENT_AFFECTION[stage] and not early:
            return None
        if not route:
            route = state["route"] = cls._pick_route(state)
            if changes is not None:
                changes.append(f"갈래가 없어 서버가 정함: {route}")
        event = ROUTE_EVENTS[stage][route]
        if f"ROUTE_{event}" in state["used_events"]:
            return None
        return event

    def _crisis(self, state: dict, judge: dict, last_reply: str, n_msgs: int, changes: list[str]) -> None:
        """S4-B 5단계: 위기(on) 중 마음 확인(sincere·pursue)이면 6단계, 메시지 6개가 지나면 엇갈림(rift).
        엇갈림은 다음 밤부터 메시지를 세어 RECONCILE_OPEN, 그 뒤 사과·진심(apologize·sincere)이면 6단계."""
        flags, used, turn = set(judge["flags"]), state["used_events"], state["turn"]
        shown = re.sub(r"\s+", "", last_reply or "")
        route = state["route"] or "care"
        if state["crisis"] == "on":
            if turn < used.get("CRISIS", -10_000) + 2:
                return  # 소식을 꺼낸 답장을 판정하는 차례: 유저가 아직 그 소식에 답하지 않음
            state["crisis_msgs"] += n_msgs
            if flags & {"sincere", "pursue"}:
                self._won(state, f"WON_{route.upper()}", shown, changes, "위기에서 마음 확인")
            elif state["crisis_msgs"] >= CRISIS_MAX_MSGS:
                state["crisis"], state["crisis_msgs"] = "rift", 0
                state["affection"] = RIFT_AFFECTION
                state["pending_event"], state["rewind_offer"] = "RIFT_START", True
                state["stage_hints"], state["hint_msgs"], state["hint_due"] = 0, 0, None
                state["used_events"]["RIFT_START"] = turn
                self._set_night(state, "RIFT_NIGHT")  # RIFT_START 가 그 밤의 마무리: 한 번 답한 뒤 「며칠 뒤 밤」
                changes.append(f"엇갈림: 위기 메시지 {CRISIS_MAX_MSGS}개 동안 마음 확인 없음 → 호감 {RIFT_AFFECTION}")
            elif state["crisis_msgs"] >= CRISIS_HINT_MSGS and not state["stage_hints"]:
                self._set_hint(state, "HINT_5", state["crisis_msgs"], changes)
            return
        if state["crisis"] != "rift" or not self._rift_night_started(state):
            return
        state["crisis_msgs"] += n_msgs
        opened = used.get("RECONCILE_OPEN")
        if opened is not None and turn >= opened + 2 and flags & {"apologize", "sincere"}:
            state["affection"] = max(state["affection"], RECONCILE_MIN_AFFECTION)
            self._won(state, "WON_RECONCILE", shown, changes, "엇갈림에서 화해")
        elif opened is None and state["crisis_msgs"] >= RIFT_OPEN_MSGS and not state["pending_event"]:
            state["pending_event"] = "RECONCILE_OPEN"
            used["RECONCILE_OPEN"] = turn
            state["hint_msgs"] = state["crisis_msgs"]
            changes.append("화해의 문: RECONCILE_OPEN")
        elif (opened is not None and not state["stage_hints"]
              and state["crisis_msgs"] - state["hint_msgs"] >= RECONCILE_HINT_MSGS):
            self._set_hint(state, "HINT_6", state["crisis_msgs"], changes)

    @staticmethod
    def _rift_night_started(state: dict) -> bool:
        """엇갈림의 메시지는 「며칠 뒤 밤」부터 셈 (RIFT_START 뒤 한 번 답할 기회·시간 카드가 지난 뒤)."""
        return not state["night_next"]

    # ---------- 밤 넘기기 (S4-C) ----------
    @staticmethod
    def _set_night(state: dict, nxt: str) -> None:
        state["night_next"], state["night_card"], state["night_wait"] = nxt, NIGHT_NEXT[nxt][0], 0

    @classmethod
    def _night_end(cls, state: dict, changes: list[str]) -> None:
        """1→2, 2→3 로 오른 뒤(상징 장면 다음부터) 유저 메시지 4개가 지나면 다음 답장에 그 밤의 마무리(NIGHT_END) 한 번."""
        st = state["stage"]
        if st not in NIGHT_END_STAGES or state["night_next"] or state["pending_event"] or state["ending"] or state["scene"]:
            return
        if f"NIGHT_END_{st}" in state["used_events"] or state["stage_user_msgs"] < NIGHT_END_AFTER:
            return
        if cls._name_scene_quiet(state):
            return  # 이름 장면 중에는 미룸
        nxt = "NIGHT_START_2" if st == 2 else f"NIGHT_START_3_{(state['route'] or 'care').upper()}"
        state["pending_event"] = "NIGHT_END"
        state["used_events"][f"NIGHT_END_{st}"] = state["turn"]
        cls._set_night(state, nxt)
        changes.append(f"밤 마무리: NIGHT_END → 다음 밤 {nxt} ({state['night_card']})")

    @staticmethod
    def _next_night(state: dict, changes: list[str], why: str = NEXT_NIGHT) -> None:
        """「[다음 밤]」: 날짜·시간대·장소를 다음 밤으로, 열려 있던 갈래 장면은 닫음. 엇갈림은 이 밤부터 메시지를 셈."""
        nxt = state["night_next"]
        state["day"] += NIGHT_NEXT[nxt][1]
        state["time_of_day"], state["location"] = "밤", NIGHT_LOCATION
        state["night_next"], state["night_card"], state["night_wait"] = None, None, 0
        Engine._drop_scene(state, changes, "밤 넘기기")
        if state["crisis"] == "rift":
            state["crisis_msgs"] = 0
        changes.append(f"다음 밤 ({why}): {state['day']}일째 밤, {nxt}")

    def _won(self, state: dict, won: str, shown: str, changes: list[str], why: str) -> None:
        """마음 확인 → 6단계. 성공 대사가 판정한 답장에 없었으면 다음 답장(STAGE_UP_6) 앞에 넣음."""
        state["crisis"] = "done"
        state["won_line"] = None if WON_KEYS[won] in shown else won
        changes.append(f"{why} → 6단계" + ("" if state["won_line"] else " (성공 대사가 이번 답장에 이미 나옴)"))
        self._advance(state, 6, changes)

    @staticmethod
    def _set_hint(state: dict, name: str, msgs: int, changes: list[str]) -> None:
        if state["pending_event"] or state["night_next"]:
            return  # 사건이 있는 답장에는 넣지 않음 → 다음 판정에서 다시 봄
        state["hint_due"], state["hint_msgs"] = name, msgs
        state["stage_hints"] += 1
        changes.append(f"속마음 힌트: {name}")

    @classmethod
    def _hint(cls, state: dict, changes: list[str]) -> None:
        """S4 속마음 힌트 (1~4단계): 최소 수를 넘겼는데 단계가 안 올랐으면 다음 답장에 한 번, 그 뒤 메시지 10개 지나 한 번 더.
        사건이 정해졌거나 갈래 장면이 열려 있거나 이름 장면 중이면 다음 판정으로 미룸."""
        st, msgs = state["stage"], state["stage_user_msgs"]
        name = HINT_OF_STAGE.get(st)
        if (not name or state["ending"] or state["pending_event"] or state["scene"] or state["night_next"]
                or cls._name_scene_quiet(state)):
            return
        if msgs < MIN_STAGE_MSGS.get(st, 0) or state["stage_hints"] >= HINT_MAX:
            return
        if state["stage_hints"] and msgs - state["hint_msgs"] < HINT_AGAIN_MSGS:
            return
        state["hint_due"], state["hint_msgs"] = name, msgs
        state["stage_hints"] += 1
        changes.append(f"속마음 힌트: {name} ({state['stage_hints']}/{HINT_MAX})")

    @staticmethod
    def _start_route_scene(state: dict, event: str, changes: list[str]) -> None:
        state["pending_event"] = event
        state["scene"], state["scene_judges_left"], state["scene_msgs"] = event, SCENE_JUDGES, 0
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
        if not user_message.strip():
            pass  # S4-1: 「[다음 밤]」만 온 판정은 짧은 답으로 세지 않음
        elif len(user_message.strip()) < SHORT_MESSAGE_LEN:
            state["short_streak"] += 1
        else:
            state["short_streak"] = 0

        if state["pending_event"] or state["ending"] == "departed" or state["night_next"]:
            return None  # 이미 예약된 사건이 있거나(예전) 실패 엔딩 뒤, 또는 밤 마무리 중(S4-C, 한 번 답하는 답장에도 사건 없음)
        if state["scene"]:
            return None  # S4-1: 열린 갈래 장면(필수 사건)에는 무작위 사건·조연을 끼우지 않음
        if self._name_scene_quiet(state):
            return None  # S2-1: 이름 장면 중·직후에는 무작위 사건(조연 포함)을 끼우지 않음 stayed 뒤에는 연인 일상 사건이 이어짐
        if state["stage"] == 6 and state["route"] and state["ending"] != "stayed":
            return None  # 6단계 성공 전(첫 아침 → 조연 마무리, 길어야 메시지 15개)에는 무작위 사건을 끼우지 않음
        if (
            state["short_streak"] < SHORT_STREAK_TRIGGER
            and state["turns_since_event"] < QUIET_TURNS_TRIGGER
        ):
            return None
        candidates = [
            e for e in STAGE_EVENTS[state["stage"]]
            if state["turn"] - state["used_events"].get(e, -10_000) >= EVENT_COOLDOWN
            # 2~5단계에서 갈래 사건은 무작위로 나오지 않음(자기 갈래 사건은 정해진 때 한 번. S4-1: 갈래가 없을 때도)
            and not (2 <= state["stage"] <= 5 and e in ROUTE_SCENES)
            # S4-1 연인 단계: 이미 겪은 갈래 장면은 같은 원고로 다시 나오지 않음
            and not (state["stage"] == 6 and f"ROUTE_{e}" in state["used_events"])
        ]
        weights = [1] * len(candidates)
        if not state["scene"]:  # 갈래 장면이 열려 있는 동안에는 조연을 넣지 않음
            for name in SIDE_CHARS:
                n = self._side_next(state, name)
                if n and state["turn"] - state["used_events"].get(name, -10_000) >= EVENT_COOLDOWN:
                    candidates.append(f"{name}_{n}")
                    weights.append(SIDE_WEIGHT if SIDE_OF_ROUTE.get(state["route"] or "") == name else 1)
        if not candidates:
            return None
        event = random.choices(candidates, weights)[0]
        state["pending_event"] = event
        state["used_events"][event.split("_")[0] if event in SIDE_EVENTS else event] = state["turn"]
        if event in SIDE_EVENTS:
            self._side_step(state, event)
        state["short_streak"] = 0
        state["turns_since_event"] = 0
        return event

    # ---------- 조연 (앱 54) ----------
    @staticmethod
    def _side_next(state: dict, name: str) -> int | None:
        """그 조연이 지금 단계에서 나올 수 있으면 다음 차례(1 처음 · 2 두 번째), 아니면 None. 마무리(3)는 _side_finale."""
        step, stage, route = state[f"{name.lower()}_step"], state["stage"], state["route"]
        if route and SIDE_OF_ROUTE[route] != name:  # 다른 갈래의 조연: 「처음」 한 번만
            return 1 if step == 0 and stage in OTHER_ROUTE_FIRST[name] else None
        n = step + 1
        return n if n in SIDE_SCENE_STAGES[name] and stage in SIDE_SCENE_STAGES[name][n] else None

    @staticmethod
    def _side_finale(state: dict, last_reply: str, judge: dict, changes: list[str]) -> None:
        """6단계: 첫 아침이 나온 다음 판정에서 그 갈래 조연 마무리를 넣고, 그게 나온 다음 판정에서 stayed.
        마무리가 늦어져 6단계 유저 메시지가 STAYED_MAX_MSGS 가 되면 마무리 없이 stayed."""
        if state["ending"]:
            return  # stayed 는 한 번 정하면 그대로(앱은 한 번만 띄움), departed 는 6단계에 없음
        route, pend = state["route"], state["pending_event"] or ""
        name = SIDE_OF_ROUTE.get(route or "")
        side_pending = pend in SIDE_EVENTS or pend in SIDE_CHARS
        finale_shown = bool(name) and state[f"{name.lower()}_step"] >= 3 and not side_pending
        if (state["morning_done"] and finale_shown) or (
                state["stage_user_msgs"] >= STAYED_MAX_MSGS and not side_pending):
            why = "조연 마무리 뒤" if state["morning_done"] and finale_shown else f"6단계 메시지 {state['stage_user_msgs']}개"
            state["ending"] = "stayed"
            state["mari_step"] = state["taeo_step"] = 3  # 마무리는 다시 나오지 않음
            state["short_streak"], state["turns_since_event"] = 0, 0  # 에필로그 바로 뒤에 무작위 사건이 붙지 않게
            changes.append(f"엔딩: 서아가 남음 (stayed, {why})")
            return
        if name and state["morning_done"] and state[f"{name.lower()}_step"] < 3 and not pend:
            state["pending_event"] = f"{name}_3"  # 사건 발동 조건(짧은 답·15턴)을 기다리지 않음
            state[f"{name.lower()}_step"] = 3
            state["used_events"][name] = state["turn"]
            state["short_streak"], state["turns_since_event"] = 0, 0
            changes.append(f"조연 마무리: {name}_3")
        if not state["morning_done"]:
            text = re.sub(r"\s+", "", last_reply or "")
            keys = MORNING_KEYS.get(route or "")
            if (keys and all(k in text for k in keys)) or judge.get("time_advance"):
                state["morning_done"] = True  # 마무리는 다음 판정에서 (첫 아침 장면이 닫힌 뒤)
                changes.append("6단계 첫 아침 지남")

    @staticmethod
    def _side_step(state: dict, event: str) -> None:
        """조연 장면을 넣은 판정에서 차례를 올린다. 다른 갈래의 조연은 「처음」 뒤 닫음(3)."""
        name, n = event.split("_")
        key = f"{name.lower()}_step"
        route = state["route"]
        state[key] = 3 if route and SIDE_OF_ROUTE[route] != name else max(state[key], int(n))

    # ---------- 판정 ----------
    @staticmethod
    def _judge_name_call(state: dict) -> str:
        line = name_call(state)
        if state.get("user_name") and state.get("name_state") == "asked":
            line += " (서아가 이름으로 부르고 싶다고 물어본 참)"
        return line

    def _judge(self, state: dict, user_message: str, reply: str,
               need_summary: bool = False, recent: list[dict] | None = None) -> dict:
        prompt = (
            f"[현재 상태]\n단계: {state['stage']} ({state['episode_stage_name'] or STAGE_NAMES[state['stage']]}), "
            f"호감도: {state['affection']}, {state['day']}일째 {state['time_of_day']}, "
            f"장소: {state['location']}"
            + ("" if state["episode"] else f", 이름 부르기: {self._judge_name_call(state)}")
            + f"\n\n[지금까지의 요약]\n{state['summary'] or '없음'}"
        )
        if need_summary:
            convo = "\n".join(
                f"{'유저' if m.get('r') == 'u' else '서아'}: {m.get('t')}"
                for m in (recent or [])[-24:] if m.get("r") in ("u", "c")
            )
            prompt += f"\n\n[요약 요청]\n기존 요약과 아래 최근 대화를 합쳐 summary를 채운다.\n[최근 대화]\n{convo or '없음'}"
        due = None if state["episode"] else state.get("stage_note_due")
        if due:  # S3: 끝난 단계를 한 문장으로
            prompt += f"\n\n[단계 기록 요청] {due}단계({STAGE_NAMES[due]})를 한 문장으로"
        prompt += f"\n\n[유저]\n{user_message}\n\n[서아]\n{reply}"
        neutral = {"affection_delta": 0, "flags": [], "time_advance": False,
                   "time_of_day": "", "summary": "", "stage_note": "", "promise": "", "reason": "판정 실패"}
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
            "stage_note": str(data.get("stage_note") or "").strip() if due else "",
            "promise": str(data.get("promise") or "").strip(),
            "reason": str(data.get("reason", "")),
        }

    # ---------- 상태 갱신 ----------
    @staticmethod
    def _remember(state: dict, judge: dict) -> list[str]:
        """S3 기억(본편만): 부탁한 단계 기록을 stage_notes 에, 서아의 약속·제안을 promises 에."""
        changes = []
        due, note = state.get("stage_note_due"), (judge.get("stage_note") or "")[:MEMO_MAX_LEN]
        if due and note:
            state["stage_notes"][str(due)] = note  # 이미 있으면 덮어씀
            state["stage_note_due"] = None
            changes.append(f"{due}단계 기록 저장")
        promise = (judge.get("promise") or "")[:MEMO_MAX_LEN]
        if promise and promise not in state["promises"]:
            state["promises"] = (state["promises"] + [promise])[-PROMISES_MAX:]
            changes.append("서아의 말·약속 기록")
        return changes

    def _apply(self, state: dict, judge: dict, event: str | None, last_reply: str = "") -> list[str]:
        changes = []

        cap = AFFECTION_CAP[state["stage"]]
        before = state["affection"]
        bonus = judge.get("talk_bonus", 0)
        state["affection"] = max(0, min(cap, before + judge["affection_delta"] + bonus))
        state["stage_bonus"] += bonus
        if state["affection"] != before:
            changes.append(f"호감도 {before} → {state['affection']}")

        for flag in judge["flags"]:
            if flag not in state["stage_flags"] and flag not in NAME_FLAGS:
                state["stage_flags"].append(flag)

        if judge["time_of_day"]:
            state["time_of_day"] = judge["time_of_day"]

        if judge["time_advance"]:
            state["day"] += 1
            changes.append(f"{state['day']}일째로 넘어감")
        # S4: 5단계 남은 날(DAYS_LEFT_2/1)·LAST_NIGHT·실패 엔딩 departed 는 본편에서 더는 쓰지 않음(위기 → 연인 / 엇갈림 → 화해)

        if state["episode"]:  # 에피소드: 호감·플래그·시간대만. 비밀·단계 상승·출발·엔딩은 본 이야기 몫
            return changes

        if (
            "reveal_secret1" in judge["flags"]
            and state["stage"] >= 4
            and "비밀1" not in state["revealed_secrets"]
        ):
            state["revealed_secrets"].append("비밀1")
            changes.append("비밀1 공개")

        self._apply_name(state, judge, changes, last_reply)

        nxt = self._next_stage(state)
        if nxt:
            self._advance(state, nxt, changes)
        return changes

    @staticmethod
    def _set_stage(state: dict, stage: int) -> None:
        """이름 흐름으로 단계를 옮김(올리기·내리기). 단계 상징 장면은 넣지 않고, 이 단계의 셈은 0부터."""
        state["stage"] = stage
        state["stage_flags"] = []
        state["stage_bonus"], state["stage_user_msgs"] = 0, 0
        state["stage_hints"], state["hint_msgs"], state["hint_due"] = 0, 0, None
        Engine._drop_scene(state, [], "이름 흐름으로 단계 이동")  # 갈래 장면이 다른 단계로 따라오지 않게, 마치지 않은 것으로

    @classmethod
    def _apply_name(cls, state: dict, judge: dict, changes: list[str], last_reply: str = "") -> None:
        """S2 이름 흐름 (본편, 유저 이름이 있을 때만). 단계 상승 판단보다 먼저."""
        ns, flags = state["name_state"], set(judge["flags"])
        if not state["user_name"] or ns in ("none", "closed"):
            return  # closed 뒤에는 이름 관련 판정을 무시

        shown = re.sub(r"\s+", "", last_reply or "")

        def event(name: str) -> None:  # 5단계 출발·남은 날 지문은 밀어내지 않음
            state["name_quiet"] = NAME_QUIET_MSGS
            if NAME_EVENT_KEYS[name] in shown:  # S2-1: 판정한 답장에서 이미 나왔으면 다음 답장에 또 넣지 않음
                changes.append(f"{name}: 이번 답장에서 이미 나옴")
                return
            if state["pending_event"] not in CRISIS_EVENTS | ROUTE_SCENES | {"NIGHT_END"}:  # 위기·필수 사건·밤 마무리는 밀어내지 않음
                state["pending_event"] = name

        def lose(points: int) -> None:
            state["affection"] = max(0, min(AFFECTION_CAP[state["stage"]], state["affection"] - points))

        if ns in ("asked", "accepted") and "refuse_name" in flags:
            if state["stage"] in (3, 4):
                cls._set_stage(state, 2)
            lose(NAME_REFUSE_PENALTY)
            state["name_state"] = "refused"
            event("NAME_REFUSED")
            changes.append(f"이름 거절 → {state['stage']}단계, 호감 -{NAME_REFUSE_PENALTY} (사과 전에는 3단계로 못 오름)")
        elif ns == "asked":
            # 물은 답장을 판정하는 차례(물은 판정 바로 다음)에는 유저가 아직 답하지 않았으므로 기다림
            if state["turn"] >= state["used_events"].get("NAME_ASK", -10_000) + 2:
                state["name_state"] = "accepted"
                changes.append("이름 부르기 받아들임")
        elif ns == "refused" and "apologize" in flags:
            if state["stage"] == 2:
                cls._set_stage(state, 3)
                state["affection"] = max(state["affection"], NAME_FORGIVE_MIN_AFFECTION)
            state["name_state"] = "forgiven"
            event("NAME_FORGIVEN")
            changes.append(f"사과 받음 → {state['stage']}단계, 이름 부르기 시작")
        elif ns == "forgiven" and "refuse_name" in flags:
            if state["stage"] == 3:
                cls._set_stage(state, 2)
            lose(NAME_REFUSE_AGAIN_PENALTY)
            state["name_state"] = "closed"
            event("NAME_REFUSED_AGAIN")
            changes.append(f"이름 두 번째 거절 → {state['stage']}단계, 호감 -{NAME_REFUSE_AGAIN_PENALTY}, 다시는 이름 안 부름")

    @staticmethod
    def _scene_done(state: dict, stage: int) -> bool:
        """S4: 그 단계의 갈래 장면을 마쳤는지. 장면을 시작했고(used_events.ROUTE_x) 장면 블록이 닫혔으면(scene 이 그 장면이 아님) 마침."""
        if state.get("route") not in ROUTES:
            return False
        event = ROUTE_EVENTS[stage][state["route"]]
        return f"ROUTE_{event}" in state["used_events"] and state["scene"] != event

    @classmethod
    def _next_stage(cls, state: dict) -> int | None:
        st, aff, flags, route = state["stage"], state["affection"], set(state["stage_flags"]), state.get("route")
        if state.get("stage_user_msgs", 0) < MIN_STAGE_MSGS.get(st, 0):
            return None  # 호감·플래그가 차도 이 단계에서 이만큼은 대화해야 오름
        if state.get("night_next"):
            return None  # S4-1: 밤 넘기기가 끝나기 전에는 오르지 않음
        if st == 1 and aff >= 10 and flags & {"care", "protect"}:
            return 2
        if st == 2 and state.get("name_state") == "refused":
            return None  # 이름을 거절한 뒤 사과 전에는 3단계로 못 오름
        # S4-1: 갈래 장면을 실제로 마쳐야 오름(outside 만으로는 안 됨). 갈래가 없으면 갈래 사건 차례에 서버가 정하므로 그때까지 기다림
        if st == 2 and aff >= 25 and route and cls._scene_done(state, 2):
            return 3
        if st == 3 and aff >= 40 and "sincere" in flags and route and cls._scene_done(state, 3):
            return 4
        if st == 4 and aff >= 55 and route and cls._scene_done(state, 4):
            return 5
        return None  # 5→6 은 위기 판정(_crisis)

    @staticmethod
    def _advance(state: dict, nxt: int, changes: list[str]) -> None:
        state["stage"] = nxt
        state["stage_flags"] = []
        state["stage_bonus"], state["stage_user_msgs"] = 0, 0  # 단계마다 새로 셈
        state["stage_hints"], state["hint_msgs"], state["hint_due"] = 0, 0, None
        changes.append(f"단계 상승 → {nxt}. {STAGE_NAMES[nxt]}")
        if nxt in STAGE_UP_SCENES:
            state["pending_event"] = f"STAGE_UP_{nxt}"  # 다음 답장에 그 단계의 상징 장면 (다른 사건보다 먼저)
        if nxt == 3 and state.get("user_name") and state.get("name_state", "none") == "none":
            state["name_state"] = "asked"  # 다음 답장이 STAGE_UP_3_NAMED (이름으로 부르고 싶다고 물음)
            state["used_events"]["NAME_ASK"] = state["turn"]
            changes.append("이름 부르기 물음 (STAGE_UP_3_NAMED)")
        if nxt == 4 and not state["route"] and "비밀1" not in state["revealed_secrets"]:
            state["revealed_secrets"].append("비밀1")  # 4단계 지침대로 1년 전 이야기를 털어놓게 함
            changes.append("비밀1 공개")
        if nxt == 5:  # S4: 그 골목에 이어 같은 밤, 서아가 새벽에 떠난다는 소식을 꺼냄 (DEPARTURE 대신)
            state["pending_event"] = "CRISIS"
            state["crisis"], state["crisis_msgs"] = "on", 0
            state["used_events"]["CRISIS"] = state["turn"]  # 소식을 꺼낸 답장을 판정하는 차례는 셈하지 않음
            changes.append("위기: 새벽에 떠난다는 소식 (CRISIS)")
        if nxt == 6:
            state["departure_days_left"] = None  # 성공 엔딩 stayed 는 첫 아침·조연 마무리 뒤 _side_finale 이 정함
            state["crisis"] = "done"

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
