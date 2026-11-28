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
from llm import BUDGET_SEC, FALLBACK_REPLY, LLMUnavailable
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
                 "refuse_name", "apologize", "withdraw", "lovers_yes", "lovers_later"}
# S2 유저 이름: 3단계에 오르며 이름으로 부르고 싶다고 묻고, 거절하면 관계가 흔들린다 (본편만)
NAME_STATES = ("none", "asked", "accepted", "refused", "forgiven", "closed")
NAME_FLAGS = {"refuse_name", "apologize"}  # 판정 flags 로만 쓰고 stage_flags 에는 넣지 않음
JUDGE_ONLY_FLAGS = NAME_FLAGS | {"withdraw", "lovers_yes", "lovers_later"}  # S4-1 withdraw: 엇갈림에서 사과·진심을 거둠(판정 flags 로만)
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
STAGE_NOTE_MIN_RECENT = 4   # S4-1: 단계 기록을 부탁하려면 그 단계 구간 대화가 이만큼(줄) 있어야 함. 모자라면 미룸
STAGE_NOTE_RECENT_MAX = 24  # S4-1: 단계 기록용으로 판정에 넣는 대화 줄 수 상한(요약과 같음)
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
# S6: 서버가 모델 대신 location 을 채우는 것은 그 답장 action 에 이동 대목이 있을 때만. 없으면 다음 답장에서 이동 대목부터
MOVE_WORDS = ("나선다", "나간다", "올라", "계단", "문을 열", "걸어", "따라", "향한다", "들어선다")
MOVE_FORCE_AT = 2  # 장면이 열린 뒤 정상 답장(scene_msgs)이 이만큼 지나 세 번째 답장이면 이동 대목이 없어도 옮김(무한히 미루지 않음)
MOVE_ASK_SCENES = {"ROOFTOP"}  # S7: 유저가 받아 줘야 옮기는 장면(거절하면 바 안에서 이어 감) → 이동 대목이 있을 때만 옮김
BAR_LOCATIONS = {"바 '라스트 오더' 1층", "바 '라스트 오더' 2층"}
STAGE_UP_SCENES = {2, 3, 4, 6}  # 단계가 오른 다음 답장에 한 번 넣는 상징 장면(STAGE_UP_n). 5는 DEPARTURE
# S6: 상징 장면이 판정한 답장(공백 뺌)에 나왔는지 보는 원고 대사 조각. 안 나왔으면 다음 답장에 다시(그 단계 유저 메시지 STAGE_UP_TRIES 개까지)
STAGE_UP_KEYS = {"STAGE_UP_2": "사장님손", "STAGE_UP_3": "한잔만더", "STAGE_UP_3_NAMED": "이름부르고싶어",
                 "STAGE_UP_4": "아직안물어봤네", "STAGE_UP_6": "술때문아니었어"}
STAGE_UP_TRIES = 3
# 대화량 보조 점수: S3 에서 없앰(언제나 0). 필드는 앱 호환을 위해 남김
TALK_BONUS_STAGE_MAX = 8   # 단계마다 보너스 합계 상한(단계가 오르면 0부터)
MIN_STAGE_MSGS = {1: 3, 2: 9, 3: 9, 4: 6}  # 그 단계에서 이만큼 유저 메시지를 보내야 다음 단계로(S4. 5→6 은 위기 판정)
# S4 속마음 힌트: 최소 수를 넘겼는데 다음 단계 조건이 안 찼으면 다음 답장에 한 번, 그래도 그대로면 HINT_AGAIN_MSGS 뒤 한 번 더
HINT_OF_STAGE = {1: "HINT_1", 2: "HINT_2", 3: "HINT_3", 4: "HINT_4"}
HINT_BLOCKS = {"HINT_1", "HINT_2", "HINT_3", "HINT_4", "HINT_5", "HINT_6"}  # 5: 위기 3턴째 · 6: 엇갈림 → 화해
HINT_MAX = 2
HINT_STAGE = {"HINT_1": 1, "HINT_2": 2, "HINT_3": 3, "HINT_4": 4, "HINT_5": 5, "HINT_6": 5}  # S6: 그 힌트가 나올 단계
HINT_AGAIN_MSGS = 10
HINT_LOOKBACK = 6          # /chat: 최근 서아 답장 이만큼에 그 줄이 이미 있으면 다시 넣지 않음
# S4-B 위기 → 연인 / 엇갈림 → 화해 (본편 5단계)
CRISIS_STATES = ("none", "on", "rift", "done")
CRISIS_EVENTS = {"CRISIS", "RIFT_START", "RECONCILE_OPEN"}
# S7 연인 확정: 마음 확인(위기 성공·화해 성공) 뒤 서아가 관계를 묻고(ask), 유저가 받아 주면 6단계. 본편만
LOVERS_STATES = ("none", "ask", "wait", "done")
LOVERS_LABEL = "고백"        # lovers 가 ask·wait 일 때 stage_label
LOVERS_EVENTS = {"LOVERS_ASK", "LOVERS_AGAIN"}
LOVERS_AGAIN_MSGS = 3        # 망설인 뒤(wait) 유저 메시지 이만큼마다 한 번 더 물음(원고 5절)
LOVERS_ASKS_MAX = 2          # 다시 묻기 최대 횟수. 그 뒤는 묻지 않고 lovers_yes 만 기다림
LOVERS_ASK_TRIES = 3         # 물음(1절)이 답장에 안 나올 때 LOVERS_ASK 를 넣는 횟수 상한(첫 답장 포함)
LOVERS_KEYS = {  # 공백 뺀 답장에 있으면 그 원고 대사가 나온 것
    "LOVERS_ASK_TEASE": "진짜로만날래", "LOVERS_ASK_CARE": "옆에있고싶어", "LOVERS_ASK_RECONCILE": "나랑만나줄래",
    "LOVERS_YES_TEASE": "취소없기다", "LOVERS_YES_CARE": "다행이다.진짜로", "LOVERS_YES_RECONCILE": "진작그렇게말하지",
    "LOVERS_PROPOSED": "내가먼저하려던말", "LOVERS_LATER": "대답은천천히", "LOVERS_AGAIN": "그래서,대답은",
}
LOVERS_YES_LINES = {"LOVERS_YES_TEASE", "LOVERS_YES_CARE", "LOVERS_YES_RECONCILE", "LOVERS_PROPOSED"}
LOVERS_JUDGE_LINE = {"ask": "서아가 관계를 물음", "wait": "대답을 기다림"}
CRISIS_LOCATIONS = {"tease": "네온 골목", "care": "바 옥상"}
CRISIS_NEWS_KEYS = {"tease": "새벽에떠나래", "care": "새벽에떠나"}  # S6: 판정한 답장(공백 뺌)에 이게 있어야 위기 소식이 나간 것
CRISIS_NEWS_TRIES = 3      # S6: 소식이 안 나오면 CRISIS 를 다시 넣는 횟수 상한(첫 답장 포함). 넘으면 나간 것으로 보고 진행(무한 미룸 방지)
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
NIGHT_GAP_LINES = {  # S9 night_gap → STATE_BLOCK 「지난번 밤에서」 (값이 없으면 줄을 뺌, 에피소드에는 없음)
    "하루": "하루가 지났다. 지난번 밤의 일은 「어제」로 말한다.",
    "며칠": "며칠이 지났다. 지난번 밤의 일은 「어제」「하루」가 아니라 「며칠 전」「그날」로 말한다.",
}
NIGHT_END_AFTER = 8         # S6(임시, 5→8): 상징 장면 답장 다음부터 그 단계 유저 메시지 수 (첫 밤이 너무 빨리 끝남)
NIGHT_WAIT_MAX = 6          # 예전 앱처럼 「[다음 밤]」이 안 오면 유저 메시지 이만큼 뒤 서버가 밤을 넘김(첫마디 없이)
NIGHT_LOCATION = "바 '라스트 오더' 1층"
WON_KEYS = {"WON_TEASE": "연기아니지", "WON_CARE": "내려가자", "WON_RECONCILE": "늦었잖아"}  # won_line 에 올 수 있는 블록 이름(S4-1-1: 「이미 나왔나」는 WON_SHOWN 으로 봄)
# S7: won_line 은 다음 답장 앞에 넣을 블록. 연인 확정 대사(2·3절)도 오고, 둘이면 「+」로 이음(예: WON_CARE+LOVERS_PROPOSED)
# S4-1: 판정한 답장에 성공 대사가 통째로 나왔으면(공백 빼고 모두 있음) 판정 flags 가 비어도 마음을 확인한 것으로 봄
WON_SHOWN = {"WON_TEASE": ("연기아니지",), "WON_CARE": ("내려가자", "같이"), "WON_RECONCILE": ("늦었잖아", "왔으니까")}
CRISIS_JUDGE_NOTE = ("[위기 판정] 서아가 새벽에 떠난다는 소식을 꺼낸 뒤다. 유저가 진심을 말하거나, 가지 말라고 붙잡거나, "
                     "오해를 풀어 주는 말이면 정확한 단어가 아니어도 sincere 또는 pursue 를 넣는다. "
                     "단순한 장난이나 짧은 맞장구만으로는 넣지 않는다.")
CRISIS_FIRST_NOTE = "첫 번째 유저 메시지는 서아가 소식을 꺼내기 전의 말이다. sincere·pursue 는 그 뒤 메시지만 보고 정한다."
RIFT_JUDGE_NOTE = ("[엇갈림 판정] 위기에서 엇갈려 서아가 거리를 두고 있다. 관계에 대한 사과는 apologize, 진심은 sincere. "
                   "앞서 한 사과·진심을 명확하게 철회하거나 관계 회복 의사를 명확하게 거부하면 withdraw. "
                   "단순한 투덜거림, 서운함, 장난, 망설임, 추가 설명 요구는 withdraw 가 아니다. "
                   "같은 메시지에 사과와 명확한 관계 회복 거부가 함께 있으면 withdraw 를 넣는다.")
GIFT_MOMENT_GAP = 10       # 같은 순간 선물은 준 뒤 유저 메시지 이만큼 지나야 다시 엶
FIRST_NIGHT_TURNS = 3     # 첫날 밤 사정(손등의 피)은 유저 메시지 이만큼까지만 넣음. 그 뒤엔 같은 묘사가 반복되지 않게 뺌
GIFT_MOMENT_QUIET = 3      # 순간 선물을 준 뒤 이만큼은 어떤 순간 선물도 열지 않음(자주 넣지 않기)
# S8: 정해진 장면 답장에서는 서버가 순간 선물을 확실히 엶(본편만). 선물 id → 그 답장(「[다음 밤]」 첫마디·사건·ALLEY_DONE)
# 앱이 보낸 gift_moments 에 그 id 가 있을 때만, 같은 선물은 한 번(used_events.MOMENT_{id}). 모델이 스스로 내는 경로는 그대로
MOMENT_SCENES = {
    "towel": ("NIGHT_START_3_TEASE", "NIGHT_START_3_CARE", "STORM"),
    "flower": ("ALLEY_DONE",),   # 그 골목(ALLEY) 장면이 「마침」이 된 판정 바로 다음 답장
    "umbrella": ("CRISIS",),     # 위기 소식 답장(소식이 확인될 때까지 다시 넣는 답장마다)
    "ring": ("LOVERS_ASK",),     # 서아가 관계를 묻는 답장(S7 원고 1절)
}
MOMENT_AFTER_ALLEY = "MOMENT_DUE_flower"  # used_events: ALLEY 마침 뒤 다음 답장에 flower 를 열 차례
RING_GIFT = "반지"  # 앱 목록에 ring 이 없을 때(이미 줌) 「[유저가 선물함: 반지]」를 알아볼 이름
# S8-1: 연인 물음 답장을 판정하는 차례, 첫 유저 메시지(묻기 전의 말)의 lovers_yes 는 이 말이 있을 때만(띄어쓰기 무시)
LOVERS_PROPOSE_WORDS = ("사귀", "만나자", "만날래", "만나 줄래", "연인", "애인", "여자친구", "여자 친구",
                        "좋아해", "좋아한다", "사랑해")
LOVERS_FIRST_NOTE = ("첫 번째 유저 메시지는 서아가 관계를 묻기 전의 말이다. "
                     "유저가 그 말에서 직접 사귀자고 하거나 좋아한다고 고백한 것이 아니면 lovers_yes 가 아니다.")
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
# S5 사진 촬영 제안: 앱이 state.photo_ask_v=1 · photo_asks(이미 보여 준 id 목록)를 보낼 때만. 값은 서버가 정함
PHOTO_ASK_V = 1
PHOTO_IDS = ("photo_3", "photo_6")
PHOTO_ASKS = {  # (id, 갈래) → (블록, 확정 대사, emotion, 응답 필드)
    ("photo_3", "tease"): ("PHOTO_3_TEASE", "사장님, 이런 모습 아무한테나 안 보여 주는데. 사진 한 장 찍어 둘래?", "도발",
                           {"pose": "p01", "face": "smirk", "camera_face": "wink"}),
    ("photo_3", "care"): ("PHOTO_3_CARE", "…오늘은 좀 기억해 두고 싶어서. 사진 한 장만 찍어 줄래?", "설렘",
                          {"pose": "p01", "face": "soft", "camera_face": "shy"}),
    ("photo_6", None): ("PHOTO_6", "우리 사진 좀 더 남겨 두자. 앞으로 같이 있을 날도 많잖아.", "기쁨",
                        {"pose": "p01", "face": "joy", "camera_face": "soft"}),
}
PHOTO_KEY_STRIP = re.compile(r"[\s「」\"“”'‘’.,!?~…·]")  # 확정 대사가 답장에 있는지 볼 때 공백·따옴표·문장부호는 빼고 비교
SUMMARY_TRIGGER = 40       # 요약 안 된 메시지가 이만큼 쌓이면 요약
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
        "stage_note_due": None,   # S3: 다음 판정에 한 문장을 부탁할 끝난 단계 번호 (S4-1: stage_note_queue 의 첫 단계, 호환용)
        "stage_note_queue": [],   # S4-1: 아직 기록 못 한 끝난 단계 [{"stage", "after"(끝난 뒤 유저 메시지 수), "msgs"(그 단계 유저 메시지 수)}]
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
        "night_gap": None,        # S9: 지난번 밤에서 지난 날 「하루」·「며칠」 (밤을 넘길 때 정함, 다음 밤까지 그대로). 첫날 밤은 None
        "scene_msgs": 0,          # S4-1: 열린 갈래 장면에서 정상으로 나간 서아 답장 수(시작 답장 포함). SCENE_REPLIES_DONE 이면 마침
        "rift_sorry": False,      # S4-1: 엇갈림이 된 뒤 유효한 사과·진심을 받아 둠(화해의 문 뒤에 반영, withdraw 면 지움)
        "photo_ask_v": 0,         # S5: 앱이 사진 제안을 지원하면 1 (앱이 보냄, 서버는 돌려보내기만)
        "photo_asks": [],         # S5: 앱이 이미 보여 준 사진 제안 id (photo_3·photo_6). 앱이 기록, 서버는 돌려보내기만
        "stage_up_due": None,     # S6: 단계가 올라 상징 장면(STAGE_UP_n)을 기다리는 단계 번호. 답장에 나온 것을 판정이 확인하면 None
        "stage_up_msgs": 0,       # S6: 상징 장면을 확인했을 때의 stage_user_msgs (밤 마무리는 그 뒤 메시지 수로 셈)
        "crisis_news": False,     # S6: 위기 소식(새벽에 떠나) 답장이 실제로 나간 것을 판정이 확인함. 그 전에는 마음 확인·셈 없음
        "lovers": "none",         # S7: LOVERS_STATES. ask 서아가 관계를 물음 · wait 유저가 망설여 기다림 · done 연인 확정(6단계)
        "lovers_msgs": 0,         # S7: wait 동안 센 유저 메시지 수(다시 묻기마다 0부터)
        "lovers_asks": 0,         # S7: 다시 묻기(LOVERS_AGAIN)를 넣은 횟수 (최대 LOVERS_ASKS_MAX)
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
    """S4: 앱 표시용 단계 이름. 엇갈림이면 「엇갈림」, S7: 연인 확정을 묻는 중이면 「고백」."""
    if state.get("lovers") in ("ask", "wait") and not state.get("episode"):
        return LOVERS_LABEL
    return RIFT_LABEL if state.get("crisis") == "rift" else STAGE_NAMES[state["stage"]]


def flower_waits(state: dict) -> bool:
    """S8-1: 골목 뒤 열쇠(flower)를 열지 않고 기다릴 때. 위기 중(crisis on·rift)·연인 물음 중(lovers ask·wait)."""
    return state.get("stage") == 5 and (state.get("crisis") in ("on", "rift") or state.get("lovers") in ("ask", "wait"))


def judge_soon(state: dict) -> bool:
    """앱이 다음 답마다 바로 판정할 때: 이름을 물은 뒤·사과를 기다릴 때(S2), 위기 중·화해의 문이 열린 뒤(S4)."""
    if state.get("name_state") in ("asked", "refused"):
        return True
    if state.get("episode"):
        return False
    used = state.get("used_events") if isinstance(state.get("used_events"), dict) else {}
    if (MOMENT_AFTER_ALLEY in used and not flower_waits(state)
            and any(isinstance(g, dict) and g.get("id") == "flower" for g in state.get("gift_moments") or [])):
        return True  # S8·S8-1: 골목 뒤 열쇠를 열 수 있는 동안은 그 답장을 바로 판정(한 번만 열리게)
    if state.get("lovers") == "ask":
        return True  # S7: 물음 답장·대답을 바로 판정
    if state.get("lovers") == "wait":
        return False  # S7: 기다리는 동안은 묶음 판정
    if state.get("stage_up_due"):
        return True  # S6: 상징 장면 답장을 바로 판정해 나왔는지 확인
    scene = state.get("scene")
    if scene in EVENT_LOCATIONS and state.get("location") != EVENT_LOCATIONS[scene] and not state.get("night_next"):
        return True  # S6: 장소를 옮기는 장면이 아직 그곳에 못 감 → 답장마다 판정해 「세 번째 답장」을 바로 셈
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
    if state["night_gap"] not in NIGHT_GAP_LINES:
        state["night_gap"] = None
    for key in ("stage_bonus", "stage_user_msgs", "stage_hints", "hint_msgs", "crisis_msgs", "night_wait", "stage_up_msgs"):
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
        if state["lovers"] not in LOVERS_STATES:
            state["lovers"] = "none"
        if "lovers" not in raw and state["stage"] == 6:
            state["lovers"] = "done"  # S7 전 저장: 이미 연인
        if state["stage"] == 5 and state["lovers"] in ("ask", "wait"):
            state["crisis"] = "done"  # S7: 마음은 확인했고 연인 확정을 기다림
        elif state["stage"] == 5 and state["crisis"] not in ("on", "rift"):
            state["crisis"] = "on"
        elif state["stage"] < 5:
            state["crisis"] = "none"
        elif state["stage"] == 6:
            state["crisis"] = "done"
        if state["stage"] == 6:
            state["lovers"] = "done"
        elif state["stage"] < 5 or state["crisis"] != "done":
            state["lovers"] = "none"
    else:
        state["lovers"] = "none"
    state["lovers_msgs"] = max(0, state["lovers_msgs"]) if state["lovers"] == "wait" else 0
    state["lovers_asks"] = max(0, min(LOVERS_ASKS_MAX, state["lovers_asks"])) if state["lovers"] in ("ask", "wait") else 0

    state["rewind_offer"] = state["rewind_offer"] is True
    due = raw.get("stage_up_due")
    state["stage_up_due"] = (due if type(due) is int and due == state["stage"] and due in STAGE_UP_SCENES
                             and not state["episode"] else None)
    if "crisis_news" in raw:
        state["crisis_news"] = raw["crisis_news"] is True
    else:  # S6 전 저장: 소식 답장을 판정한 차례가 지났으면 이미 나간 것으로
        used = state["used_events"] if isinstance(state["used_events"], dict) else {}
        try:
            state["crisis_news"] = state["crisis"] == "on" and ("CRISIS" not in used or (
                state["pending_event"] != "CRISIS" and state["turn"] >= int(used["CRISIS"]) + 2))
        except (TypeError, ValueError):
            state["crisis_news"] = False
    state["rift_sorry"] = state["rift_sorry"] is True and state["crisis"] == "rift"
    if not (isinstance(state["won_line"], str) and state["won_line"]
            and all(x in WON_KEYS or x in LOVERS_YES_LINES for x in state["won_line"].split("+"))):
        state["won_line"] = None
    ev = state["pending_event"]
    if isinstance(ev, str):
        ev = ev.removeprefix("EVENT_")
        if ev == "DEPARTURE" and not state["episode"]:
            ev = "CRISIS"  # 예전 앱이 5단계에 막 오른 상태를 보내면 위기 장면으로
        known = ({e for v in STAGE_EVENTS.values() for e in v} | ROUTE_SCENES | SIDE_EVENTS | set(SIDE_CHARS)
                 | {f"STAGE_UP_{n}" for n in STAGE_UP_SCENES} | NAME_EVENTS | CRISIS_EVENTS | {"NIGHT_END"}
                 | (set() if state["episode"] else LOVERS_EVENTS))
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
    state["stage_note_queue"] = _note_queue(state["stage_note_queue"], due, state)
    state["stage_note_due"] = state["stage_note_queue"][0]["stage"] if state["stage_note_queue"] else None
    promises = state["promises"] if isinstance(state["promises"], list) else []
    state["promises"] = [x.strip()[:MEMO_MAX_LEN] for x in promises if isinstance(x, str) and x.strip()][-PROMISES_MAX:]
    if state["hint_due"] not in HINT_BLOCKS or state["episode"]:
        state["hint_due"] = None
    state["stage_flags"] = [f for f in state["stage_flags"] if f in ALLOWED_FLAGS - JUDGE_ONLY_FLAGS]
    if not isinstance(state["used_events"], dict):
        state["used_events"] = {}
    state["photo_ask_v"] = PHOTO_ASK_V if type(raw.get("photo_ask_v")) is int and raw["photo_ask_v"] == PHOTO_ASK_V else 0
    asks = raw.get("photo_asks") if isinstance(raw.get("photo_asks"), list) else []
    state["photo_asks"] = [x for i, x in enumerate(asks) if x in PHOTO_IDS and x not in asks[:i]]
    return state


def photo_supported(raw) -> bool:
    """S5: 앱이 사진 제안을 지원한다고 알렸는지. photo_ask_v 가 정확히 1이고 photo_asks 가 목록일 때만(모양이 틀리면 예전 응답)."""
    return (isinstance(raw, dict) and type(raw.get("photo_ask_v")) is int and raw["photo_ask_v"] == PHOTO_ASK_V
            and isinstance(raw.get("photo_asks"), list))


def _note_queue(raw, due: int | None, state: dict) -> list[dict]:
    """S4-1 단계 기록 요청 목록. 단계마다 한 번, 이미 기록이 있는 단계는 뺌(덮어쓰지 않음).
    예전 모양(stage_note_due 하나만)이면 그 단계를 「지금 단계에 들어온 뒤 메시지 수」로 채워 넣음."""
    out: list[dict] = []
    for e in raw if isinstance(raw, list) else []:
        if not isinstance(e, dict):
            continue
        try:
            stage, after, msgs = int(e.get("stage")), int(e.get("after") or 0), int(e.get("msgs") or 0)
        except (TypeError, ValueError):
            continue
        if 1 <= stage <= 5 and str(stage) not in state["stage_notes"] and all(x["stage"] != stage for x in out):
            out.append({"stage": stage, "after": max(0, after), "msgs": max(0, msgs)})
    if due in STAGE_NAMES and str(due) not in state["stage_notes"] and all(x["stage"] != due for x in out):
        out.insert(0, {"stage": due, "after": state["stage_user_msgs"], "msgs": 0})
    return out[:5]


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
        photo_on = photo_supported(state)  # S5: 지원 신호가 없으면 photo_ask 필드도 넣지 않음(예전 응답 그대로)
        state = normalize_state(state)
        state["_scene_gifts"] = self._open_moments(state["gift_moments"], history, user_message, quiet=False)  # S8-1
        state["gift_moments"] = self._open_moments(state["gift_moments"], history, user_message)
        state["_xmas_late"] = self._xmas_late(state, history)
        state["_first_night"] = sum(1 for h in history if h.get("r") == "u") < FIRST_NIGHT_TURNS
        event = state["pending_event"]
        # S4-C: 앱의 「[다음 밤]」 → 다음 밤 첫마디 (night_next 가 있을 때만, 유저의 말로 다루지 않음)
        night = state["night_next"] if user_message.strip() == NEXT_NIGHT and not state["episode"] else None
        state["_night"] = night
        if night:
            event = None
        state["_hint"] = (None if event or night or self._hint_stale(state, state["hint_due"])
                          else self._hint_now(state["hint_due"], history))  # S6: 넣기 직전에 단계·조건을 다시 봄
        pose_id = self._pose_request(user_message, motion_list)
        photo = self._photo_due(state, event, night, user_message, history, pose_id) if photo_on else None
        system = self._build_system(state, event, motion_list)
        if photo:  # S5: 사진 제안 지시는 맨 끝에 (다른 사건이 없는 답장만)
            system += "\n\n" + self.blocks[PHOTO_ASKS[photo][0]]
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
                scene_gift = self._scene_moment(state, event, night)  # S8: 정해진 장면이면 그 선물이 우선
                if scene_gift and not (out["gift_moment"] and out["gift_moment"]["id"] == scene_gift):
                    out["gift_moment"] = self._gift_moment({"id": scene_gift}, state["_scene_gifts"])
                if event in DAY_MARKERS:  # 남은 날 지문은 모델에 맡기지 않고 그대로 앞에 붙임
                    out["action"] = f"{DAY_MARKERS[event]} {out['action'] or ''}".strip()
                moved = any(w in (out["action"] or "") for w in MOVE_WORDS)  # S6: 이동 대목이 있을 때만 서버가 채움
                if not out["location"] and event in EVENT_LOCATIONS and moved:  # 장소를 옮기는 사건인데 모델이 빠뜨리면
                    out["location"] = EVENT_LOCATIONS[event]
                scene = state["scene"]  # S4-1·S6: 장면이 아직 그 장소로 옮기지 못했으면 이어지는 답장이 옮김(세 번째는 그대로)
                if (not out["location"] and not event and not night and scene in EVENT_LOCATIONS
                        and state["scene_msgs"] <= MOVE_FORCE_AT and state["location"] != EVENT_LOCATIONS[scene]
                        and (moved or (state["scene_msgs"] >= MOVE_FORCE_AT and scene not in MOVE_ASK_SCENES))):
                    out["location"] = EVENT_LOCATIONS[scene]
                if event == "CRISIS" and not state["episode"]:  # S4: 위기 장소는 갈래로 정함
                    out["location"] = CRISIS_LOCATIONS[state["route"] or "care"]
                if night:  # S4-C: 다음 밤은 바 1층에서
                    out["location"] = NIGHT_LOCATION
                if pose_id:  # 포즈 부탁: 앱이 그 몸짓을 고정 재생하므로 답장도 같은 몸짓으로
                    out["motion_id"] = pose_id
                if photo_on:
                    out["photo_ask"] = self._photo_ask(photo, out, state)
                return out
        out = dict(FALLBACK)
        if photo_on:  # 예비 지문에는 사진 제안 없음(소비하지 않음 → 다음 정상 대화에서 다시)
            out["photo_ask"] = None
        return out

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
    def _hint_stale(state: dict, name: str | None) -> bool:
        """S6: 예약된 속마음 힌트가 이제 맞지 않으면 True. 단계가 바뀌었거나, 그 힌트가 가리키는 조건이 이미 찼을 때.
        HINT_1 care·protect · HINT_2 outside 또는 2단계 갈래 장면 시작 · HINT_3 sincere · HINT_4 그 골목 시작 ·
        HINT_5 위기가 끝남 · HINT_6 엇갈림이 끝났거나 사과·진심을 이미 받음."""
        if not name:
            return False
        if HINT_STAGE.get(name) != state["stage"]:
            return True
        flags, used = set(state["stage_flags"]), state["used_events"]
        if name == "HINT_1":
            return bool(flags & {"care", "protect"})
        if name == "HINT_2":
            return "outside" in flags or any(f"ROUTE_{e}" in used for e in ROUTE_EVENTS[2].values())
        if name == "HINT_3":
            return "sincere" in flags
        if name == "HINT_4":
            return "ROUTE_ALLEY" in used
        if name == "HINT_5":
            return state["crisis"] != "on"
        if name == "HINT_6":
            return state["crisis"] != "rift" or bool(state["rift_sorry"])
        return False

    @staticmethod
    def _open_moments(moments: list[dict], history: list[dict], user_message: str = "", quiet: bool = True) -> list[dict]:
        """앱이 보낸 순간 선물 목록에서, 최근에 준 것을 뺀다(대화 기록 + 이번 메시지의 「[유저가 선물함: 이름] · 순간 선물」 줄로 셈).
        quiet=False(S8-1, 장면 선물): 「방금 줘서 쉼」(GIFT_MOMENT_QUIET)은 보지 않고 같은 선물 간격(GIFT_MOMENT_GAP)만 봄."""
        if not moments:
            return moments
        users = [str(h.get("t") or "") for h in history if h.get("r") == "u"] + [user_message or ""]
        given = [i for i, t in enumerate(users) if "· 순간 선물" in t or "순간 선물]" in t]
        if quiet and given and len(users) - 1 - given[-1] < GIFT_MOMENT_QUIET:
            return []  # 방금 순간 선물을 줬으면 잠깐 쉼
        def recent(name: str) -> bool:
            for i in reversed(given):
                if f"[유저가 선물함: {name}" in users[i]:
                    return len(users) - 1 - i < GIFT_MOMENT_GAP
            return False
        return [g for g in moments if not recent(g["name"])]

    @staticmethod
    def _scene_keys(state: dict, event: str | None, night: str | None) -> list[str]:
        """S8: 이 답장의 장면(「[다음 밤]」 첫마디 또는 사건), 그다음 골목 뒤 차례(ALLEY_DONE). 앞이 우선."""
        keys = [night or event or ""]
        if MOMENT_AFTER_ALLEY in state["used_events"] and not flower_waits(state):  # S8-1: 위기·연인 물음 중에는 기다림
            keys.append("ALLEY_DONE")
        return keys

    @classmethod
    def _scene_moment(cls, state: dict, event: str | None, night: str | None) -> str | None:
        """S8: 이 답장이 MOMENT_SCENES 의 장면이고 그 선물이 앱 목록(이번에 열 수 있는 것)에 있고 아직 연 적이 없으면 그 id.
        S8-1: 목록은 「방금 줘서 쉼」을 보지 않은 것(_scene_gifts, reply() 가 정함)."""
        if state["episode"]:
            return None
        ids, used = {g["id"] for g in state.get("_scene_gifts", state["gift_moments"])}, state["used_events"]
        for key in cls._scene_keys(state, event, night):
            for gid, scenes in MOMENT_SCENES.items():
                if key in scenes and gid in ids and f"MOMENT_{gid}" not in used:
                    return gid
        return None

    @staticmethod
    def _moment_opened(state: dict, gid: str, changes: list[str]) -> None:
        """S8: 판정이 장면 답장이 나간 것을 보면, 그 선물이 앱 목록에 있었을 때(열렸을 때) MOMENT_{id} 로 기록 → 다시 열지 않음."""
        used = state["used_events"]
        if (state["episode"] or state.get("_gifts_known") is False  # S8-1: 판정에 목록 키가 없으면 「모름」
                or f"MOMENT_{gid}" in used or gid not in {g["id"] for g in state["gift_moments"]}):
            return
        used[f"MOMENT_{gid}"] = state["turn"]
        changes.append(f"순간 선물 {gid}: 장면 답장에서 엶 (다시 열지 않음)")

    @classmethod
    def _moments_seen(cls, state: dict, next_night: bool, n_msgs: int, fallback: bool, changes: list[str]) -> None:
        """S8: 판정 첫머리(상태를 바꾸기 전). 이번 묶음에 「[다음 밤]」 첫마디·STORM 시작 답장이 있었으면 towel 을 기록.
        S8-1 골목 뒤 차례(flower)는 열쇠가 실제로 열릴 때까지 남김: 판정 목록에 flower 가 있고, 그 답장이 위기·연인 물음 중이
        아니고, 묶음이 다른 장면 선물 답장 하나뿐이 아닐 때 기록하고 지움. 목록 키가 없으면(모름) 아무것도 안 함.
        umbrella·ring 은 위기 소식·연인 물음이 답장에 나온 것을 확인할 때 기록(_crisis_news·_lovers)."""
        if state["episode"]:
            return
        used, turn = state["used_events"], state["turn"]
        keys = []
        if next_night and state["night_next"]:
            keys.append(state["night_next"])
        storm_at = used.get("STORM")
        if (n_msgs and not (fallback and n_msgs == 1) and state["pending_event"] != "STORM" and storm_at is not None
                and storm_at == turn - (1 if used.get("ROUTE_STORM") == storm_at else 0)):
            keys.append("STORM")  # 갈래 STORM 은 정한 판정의 turn(그 뒤 +1), 무작위 STORM(6단계)은 +1 한 뒤의 turn 으로 기록됨
        for gid, scenes in MOMENT_SCENES.items():
            if any(k in scenes for k in keys):
                cls._moment_opened(state, gid, changes)
        if MOMENT_AFTER_ALLEY not in used or not n_msgs or state.get("_gifts_known") is False or flower_waits(state):
            return
        ids = {g["id"] for g in state["gift_moments"]}
        if "flower" not in ids:
            return  # 그 답장 때 목록에 없었음 → 열리지 않았으니 차례를 남김
        if cls._crisis_reply_judged(state):
            keys.append("CRISIS")
        if cls._ask_reply_judged(state):
            keys.append("LOVERS_ASK")
        taken = [gid for gid, scenes in MOMENT_SCENES.items()
                 if gid != "flower" and gid in ids and any(k in scenes for k in keys)
                 and used.get(f"MOMENT_{gid}", turn) == turn]  # 이번 판정에 막 기록했거나 아직 안 연 것
        if n_msgs == 1 and taken:
            return  # 그 한 답장은 다른 장면 선물이 차지함 → 골목 뒤 선물은 다음 답장
        used.pop(MOMENT_AFTER_ALLEY, None)
        cls._moment_opened(state, "flower", changes)

    @staticmethod
    def _crisis_reply_judged(state: dict) -> bool:
        """S8: 이번 판정이 위기 소식 답장(CRISIS)을 판정하는 차례인지(소식 확인 전, 예약이 아님)."""
        return (state["stage"] == 5 and state["crisis"] == "on" and not state["crisis_news"]
                and state["pending_event"] != "CRISIS" and "CRISIS" in state["used_events"])

    @staticmethod
    def _ask_reply_judged(state: dict) -> bool:
        """S8: 이번 판정이 연인 물음 답장(LOVERS_ASK)을 판정하는 차례인지."""
        used = state["used_events"]
        return (state["lovers"] == "ask" and state["pending_event"] != "LOVERS_ASK" and "LOVERS_ASK" in used
                and state["turn"] < used["LOVERS_ASK"] + 2)

    @staticmethod
    def _ring_given(state: dict, msgs: list[str]) -> bool:
        """S8: 유저가 반지를 순간 선물로 건넴(「[유저가 선물함: 반지]」 · 순간 선물)."""
        names = {g["name"] for g in state["gift_moments"] if g["id"] == "ring" and g["name"]} | {RING_GIFT}
        return any("순간 선물" in m and any(f"[유저가 선물함: {n}" in m for n in names) for m in msgs)

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

    @classmethod
    def _photo_due(cls, state: dict, event: str | None, night: str | None, user_message: str,
                   history: list[dict], pose_id: str | None) -> tuple[str, str | None] | None:
        """S5: 이번 답장에 사진 제안을 넣을 차례면 PHOTO_ASKS 키 (id, 갈래), 아니면 None. state 는 바꾸지 않음.
        다른 사건·밤 넘기기·힌트·이름 장면·포즈 부탁·선물이 있는 답장에는 넣지 않고 다음 정상 대화에서 다시 봄."""
        msg = (user_message or "").strip()
        if (state["episode"] or event or night or state["night_next"] or msg == NEXT_NIGHT or pose_id
                or "[유저가 선물함" in msg or state.get("_hint") or cls._name_scene_quiet(state)
                or state["ending"] == "departed" or state["crisis"] in ("on", "rift")):
            return None
        asks, used = state["photo_asks"], state["used_events"]
        if state["stage"] == 3 and "photo_3" not in asks and state["route"] in ROUTES:
            scene = ROUTE_EVENTS[3][state["route"]]
            if f"ROUTE_{scene}" not in used or state["pending_event"] == scene:
                return None  # 갈래 장면이 아직 시작 답장을 내지 않음
            if state["location"] != EVENT_LOCATIONS[scene]:
                return None  # 장면의 첫 정상 답장이 장소를 옮긴 뒤부터 (예비 지문이면 장소가 그대로)
            if state["scene"] == scene and not state["scene_msgs"] and cls._last_reply_fallback(history):
                return None  # 판정이 늦어 셈이 0인데 직전 서아 답장이 예비 지문이면 아직 첫 정상 답장 전
            return "photo_3", state["route"]
        if state["stage"] == 6 and "photo_6" not in asks and state["ending"] == "stayed":
            name = SIDE_OF_ROUTE.get(state["route"] or "")
            if name and state[f"{name.lower()}_step"] < 3:
                return None
            return "photo_6", None
        return None

    @staticmethod
    def _last_reply_fallback(history: list[dict]) -> bool:
        """기록의 마지막 서아 답장이 없거나 예비 지문(「…」·서버 대체 응답)인지."""
        last = next((str(h.get("t") or "").strip() for h in reversed(history) if h.get("r") == "c"), "")
        spoken = re.sub(r"^\*[^*]*\*\s*", "", last).strip()
        return not spoken or spoken in FALLBACK_TEXTS or last == FALLBACK_REPLY.strip()

    @staticmethod
    def _photo_ask(photo: tuple[str, str | None] | None, out: dict, state: dict) -> dict | None:
        """S5: 확정 대사가 정상 답장에 그대로 들어갔고 장소가 그대로일 때만 photo_ask (값은 서버가 정함). emotion 도 원고대로."""
        if not photo or out.get("gift_moment"):
            return None
        if out.get("location") and out["location"] != state["location"]:
            return None
        _, line, emotion, fields = PHOTO_ASKS[photo]
        if PHOTO_KEY_STRIP.sub("", line) not in PHOTO_KEY_STRIP.sub("", out.get("dialogue") or ""):
            return None  # 모델이 빠뜨림: 소비하지 않음, 다음 정상 대화에서 다시
        out["emotion"] = emotion
        return {"id": photo[0], "route": photo[1] or state["route"] or "care", **fields}

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
              need_summary: bool = False, recent: list[dict] | None = None, reply_ok=None) -> dict:
        """판정 호출 → 규칙 적용 → 다음 턴 사건 결정. 갱신된 state를 돌려준다.
        reply_ok (S4-1-1, 선택): last_user 메시지마다 서아 답장이 정상으로 나갔는지 [true, false, …]. 없거나 길이가 다르면 예전 방식."""
        gifts_known = isinstance(state, dict) and isinstance(state.get("gift_moments"), list)  # S8-1: 키 없음 = 모름
        state = normalize_state(state)
        state["_gifts_known"] = gifts_known
        stage_before = state["stage"]
        lines = [m.strip() for m in (last_user or "").split("\n") if m.strip()]
        msgs = [m for m in lines if m != NEXT_NIGHT]  # S4-C: 「[다음 밤]」은 유저의 말이 아님
        next_night = len(msgs) < len(lines) and not state["episode"]
        real_user = "\n".join(msgs)
        note_req = None  # S4-1: 이번 판정에서 부탁하는 단계 기록 (단계, 대화)
        if next_night and not msgs and not need_summary:  # 판정할 말이 없음: 호출하지 않음
            judge = {"affection_delta": 0, "flags": [], "time_advance": False, "time_of_day": "", "summary": "",
                     "stage_note": "", "promise": "", "reason": "다음 밤"}
        else:
            notes = [] if state["episode"] else self._judge_notes(state, len(msgs))
            if not state["episode"]:
                for e in state["stage_note_queue"]:  # 이번 묶음은 그 단계가 끝난 뒤의 메시지
                    e["after"] += len(msgs)
                note_req = self._note_request(state, recent or [])
            judge = self._judge(state, real_user if next_night else last_user, last_reply, need_summary, recent or [],
                                notes=notes, note=note_req)
        if next_night:  # 날짜는 밤 넘기기가 정함. 「[다음 밤]」만 왔으면 호감·플래그 0
            judge.update(time_advance=False, time_of_day="")
            if not msgs:
                judge.update(affection_delta=0, flags=[], promise="")
        progress = None
        fallback = (last_reply or "").strip() in FALLBACK_TEXTS  # S4-1: 판정한 답장이 예비 지문
        changes: list[str] = []
        self._moments_seen(state, next_night, len(msgs), fallback, changes)  # S8: 상태를 바꾸기 전에 이번 묶음의 장면을 봄
        if not state["episode"]:  # 대화량 점수·메시지 수는 본 이야기만
            state["name_quiet"] = max(0, min(NAME_QUIET_MSGS, state["name_quiet"]) - len(msgs))
            judge["talk_bonus"] = max(0, min(self._talk_bonus(msgs, judge), TALK_BONUS_STAGE_MAX - state["stage_bonus"]))
            state["stage_user_msgs"] += len(msgs)
            state["hint_due"] = None  # S4: 앞 판정이 정한 힌트는 그 사이 답장에 나갔음
            state["won_line"], state["rewind_offer"] = None, False  # S4: 한 번만
            ok = self._ok_replies(lines, msgs, reply_ok)
            if ok is None:  # 예전 앱: 묶음 수에서 판정한 마지막 답장이 예비 지문이면 하나 뺌
                ok = max(0, len(msgs) - (1 if fallback else 0))
            self._tick_scene(state, ok, changes)  # 단계 조건이 갈래 장면 「마침」을 보므로 단계 판단보다 먼저
            changes += self._remember(state, judge, note_req[0] if note_req else None)
            self._stage_up_seen(state, last_reply, len(msgs), fallback, changes)  # S6: 이름 흐름·단계 판단보다 먼저
        # S4-1: 이름 거절에 대한 사과(사과를 기다리던 중의 apologize)는 관계 위기 사과로 세지 않음
        name_sorry = bool(state["user_name"]) and state["name_state"] == "refused" and "apologize" in judge["flags"]
        changes += self._apply(state, judge, None, last_reply)
        if not state["episode"] and state["night_next"]:  # S4-C 밤 넘기기
            if next_night:
                self._next_night(state, changes)
            else:
                state["night_wait"] += len(msgs)
                if state["night_wait"] >= NIGHT_WAIT_MAX:
                    self._next_night(state, changes, f"「{NEXT_NIGHT}」 없이 메시지 {state['night_wait']}개")
        ring = (not state["episode"] and stage_before == 5 and state["lovers"] in ("ask", "wait")
                and self._ring_given(state, msgs))
        if ring and "lovers_yes" not in judge["flags"]:
            judge["flags"] = list(judge["flags"]) + ["lovers_yes"]  # S8: 관계를 물은 뒤 반지를 건넴 = 받아 줌
            changes.append("연인 확정: 반지를 건넴 → lovers_yes")
        if not state["episode"] and stage_before == 5 and state["lovers"] in ("ask", "wait"):
            self._lovers(state, judge, last_reply, len(msgs), changes, fallback,
                         first_msg=msgs[0] if msgs else "", ring=bool(ring))  # S7 연인 확정
        elif not state["episode"] and stage_before == 5:
            self._crisis(state, judge, last_reply, len(msgs), changes, fallback, name_sorry)
        if not state["episode"]:  # S3·S4-1: 끝난 단계(정상으로 오른 때만, _advance 가 목록에 넣음)
            state["stage_note_due"] = state["stage_note_queue"][0]["stage"] if state["stage_note_queue"] else None
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
        if state["ending"]:
            state["used_events"].pop(MOMENT_AFTER_ALLEY, None)  # S8-1: 엔딩이 나면 열쇠 차례를 지움
        state.pop("_gifts_known", None)
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
        main_state = self.blocks["STATE_BLOCK"]
        if state.get("night_gap") in NIGHT_GAP_LINES:  # S9: 지난번 밤에서 지난 날
            values["night_gap"] = NIGHT_GAP_LINES[state["night_gap"]]
        else:  # 첫날 밤 등: 줄을 통째로 뺀다
            main_state = "\n".join(x for x in main_state.split("\n") if "{{night_gap}}" not in x)
        parts += [self.blocks["MAIN_WORLD"], render(main_state, values)]
        if state["gift_moments"]:  # 앱 50: 열 수 있는 순간 선물이 있을 때만
            values["gift_moments"] = "\n".join(f"- {g['id']} {g['name']}: {g['when']}" for g in state["gift_moments"])
            parts.append(render(self.blocks["GIFT_MOMENT"], values))
        if state["day"] == 1 and state.get("_first_night", True):  # 앱 37 골목 프롤로그: 첫날 밤, 처음 몇 턴만(계속 넣으면 매 답장에 손등의 피가 나옴)
            parts.append(self.blocks["FIRST_NIGHT"])
        if state.get("lovers") not in ("ask", "wait"):  # S7: 연인 확정 중에는 「새벽에 떠나야 한다」 위기 지침을 넣지 않음
            parts.append(render(self.blocks[f"STAGE_{state['stage']}"], values))
        parts += [
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
        elif not event and state.get("night_next") in NIGHT_NEXT and NIGHT_NEXT[state["night_next"]][2]:
            parts.append(self._after_night_end_line(state["night_next"]))  # S6: 마무리 다음 답장은 짧게 한 번만 받음
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

    def _after_night_end_line(self, nxt: str) -> str:
        """S6: 밤 마무리 블록의 「그다음 유저 답장에는 …」 줄(원문 그대로). 마무리 다음 답장에만 따로 넣음."""
        block = self.blocks[NIGHT_NEXT[nxt][2]]
        return next(x for x in block.split("\n") if x.startswith("그다음 유저 답장에는"))

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
            return [self.blocks[x] for x in state["won_line"].split("+")]
        if state.get("lovers") in ("ask", "wait") and state["stage"] == 5:
            return self._lovers_parts(state, event)
        if crisis == "on" and state["stage"] == 5:
            parts = [self.blocks[f"CRISIS_{route}"]]
            if event != "CRISIS" and state.get("crisis_news"):  # S6: 소식이 나간 것을 확인한 뒤에만(그 전의 진심은 성공이 아님)
                lines = self.blocks["CRISIS_WATCH"].split("\n")
                parts.append("\n".join([x for x in lines if not x.startswith("[")]
                                       + [x.split("] ", 1)[1] for x in lines if x.startswith(f"[{route.lower()}] ")]))
            return parts
        if crisis == "rift" and state["stage"] == 5:
            parts = [] if event == "RIFT_START" else [self.blocks["RIFT"]]
            if "RECONCILE_OPEN" in used and event != "RECONCILE_OPEN":
                parts.append(self.blocks["RECONCILE_WATCH"])
                if state.get("rift_sorry"):  # S4-1: 앞서 받은 사과·진심. 같은 말을 다시 요구하지 않음
                    parts.append(self.blocks["RECONCILE_REMEMBERED"])
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
        if event == "STAGE_UP_3" and named and state.get("name_state") in ("none", "asked"):  # S6: 이름이 뒤늦게 와도 물음
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
    def _ok_replies(lines: list[str], msgs: list[str], reply_ok) -> int | None:
        """S4-1-1: reply_ok 가 메시지 수(「[다음 밤]」 뺀 것 또는 포함한 것)와 길이가 같은 bool 배열이면 정상 답장 수.
        「[다음 밤]」 자리의 값은 세지 않음. 아니면 None(예전 계산)."""
        if not isinstance(reply_ok, list) or not all(isinstance(x, bool) for x in reply_ok):
            return None
        if len(reply_ok) == len(lines) and len(lines) != len(msgs):
            reply_ok = [ok for line, ok in zip(lines, reply_ok) if line != NEXT_NIGHT]
        if len(reply_ok) != len(msgs):
            return None
        return sum(reply_ok)

    @staticmethod
    def _tick_scene(state: dict, ok_replies: int, changes: list[str]) -> None:
        """S4-1: 열린 갈래 장면에서 정상으로 나간 서아 답장을 센다(S4-1-1: reply_ok 의 true 수, 없으면 예전 계산).
        시작 답장 + 그 뒤 정상 답장 3개(SCENE_REPLIES_DONE)면 마침. 예약만 된 장면(앱이 아직 사건 답장을 내지 않음)은 세지 않음."""
        scene = state["scene"]
        if not scene or state["pending_event"] == scene:
            return
        state["scene_msgs"] += max(0, ok_replies)
        if state["scene_msgs"] >= SCENE_REPLIES_DONE:
            state["scene"], state["scene_judges_left"], state["scene_msgs"] = None, 0, 0
            changes.append(f"갈래 장면 마침: {scene}")
            if scene == "ALLEY" and not state["episode"] and "MOMENT_flower" not in state["used_events"]:
                state["used_events"][MOMENT_AFTER_ALLEY] = state["turn"]  # S8: 다음 답장에 골목 뒤 순간 선물(flower)

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
        if (state["ending"] or state["pending_event"] or state["scene"] or stage not in ROUTE_EVENTS
                or state.get("lovers") in ("ask", "wait")):
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

    @staticmethod
    def _event_after(state: dict, key: str, n_msgs: int, fallback: bool) -> int:
        """S4-1: 이번 묶음에서 그 사건 답장(CRISIS·RIFT_START·RECONCILE_OPEN) 뒤에 온 유저 메시지 수.
        사건을 정한 바로 다음 판정만 따로 본다: 메시지 하나면 사건 답장을 받기 전의 말(0), 여럿이면 첫째만 그 전(n-1).
        -1: 그 하나의 답장이 예비 지문이라 사건이 나가지 않음(다시 넣어야 함). 앱이 아직 사건 답장을 내지 않았으면 0."""
        used = state["used_events"]
        if key not in used:
            return n_msgs
        if state["pending_event"] == key:
            used[key] = state["turn"]  # 예약만 됨: 사건 답장이 나간 다음 판정부터 다시 셈
            return 0
        if state["turn"] >= used[key] + 2:
            return n_msgs
        if n_msgs <= 1:
            return -1 if n_msgs == 1 and fallback else 0
        return n_msgs - 1

    @staticmethod
    def _won_shown(won: str, shown: str) -> bool:
        return all(k in shown for k in WON_SHOWN[won])

    def _judge_notes(self, state: dict, n_msgs: int) -> list[str]:
        """S4-1: 위기·엇갈림 판정 지시(맥락으로 인정, 위기 소식 전 메시지는 빼고)."""
        if state["stage"] != 5:
            return []
        if self._ask_reply_judged(state):
            return [LOVERS_FIRST_NOTE]  # S8-1: 물음 답장을 판정하는 차례
        if state["crisis"] == "on":
            used = state["used_events"]
            first = "CRISIS" in used and state["turn"] < used["CRISIS"] + 2 and n_msgs > 1
            return [CRISIS_JUDGE_NOTE + (" " + CRISIS_FIRST_NOTE if first else "")]
        if state["crisis"] == "rift":
            return [RIFT_JUDGE_NOTE]
        return []

    def _crisis(self, state: dict, judge: dict, last_reply: str, n_msgs: int, changes: list[str],
                fallback: bool = False, name_sorry: bool = False) -> None:
        """S4-B 5단계: 위기(on) 중 마음 확인(sincere·pursue)이면 6단계, 메시지 6개가 지나면 엇갈림(rift).
        엇갈림은 다음 밤부터 메시지를 세어 RECONCILE_OPEN, 그 뒤 사과·진심(apologize·sincere)이면 6단계.
        S4-1: 위기 소식 답장 뒤의 메시지부터 셈(묶음이면 첫째만 뺌), 성공 대사가 이미 나왔으면 성공으로 봄,
        엇갈린 뒤의 사과·진심은 rift_sorry 로 기억해 화해의 문 뒤에 반영(withdraw 면 지움), 이름 사과는 빼고 봄,
        사건 답장이 예비 지문이었으면 사건을 다시 넣음."""
        flags, used, turn = set(judge["flags"]), state["used_events"], state["turn"]
        if name_sorry:
            flags.discard("apologize")  # 이름 거절에 대한 사과 ≠ 관계 위기 사과
        shown = re.sub(r"\s+", "", last_reply or "")
        route = state["route"] or "care"
        if state["crisis"] == "on":
            post = self._crisis_news(state, last_reply, n_msgs, changes)
            if not post:
                return  # 소식이 아직 안 나갔거나(다시 넣음), 소식을 꺼낸 답장을 판정하는 차례
            state["crisis_msgs"] += post
            won = f"WON_{route.upper()}"
            if flags & {"sincere", "pursue"} or self._won_shown(won, shown):
                self._won(state, won, shown, changes, "위기에서 마음 확인", flags)
            elif state["crisis_msgs"] >= CRISIS_MAX_MSGS:
                state["crisis"], state["crisis_msgs"] = "rift", 0
                state["affection"] = RIFT_AFFECTION
                state["rift_sorry"] = False
                state["pending_event"], state["rewind_offer"] = "RIFT_START", True
                state["stage_hints"], state["hint_msgs"], state["hint_due"] = 0, 0, None
                state["used_events"]["RIFT_START"] = turn
                self._set_night(state, "RIFT_NIGHT")  # RIFT_START 가 그 밤의 마무리: 한 번 답한 뒤 「며칠 뒤 밤」
                changes.append(f"엇갈림: 위기 메시지 {CRISIS_MAX_MSGS}개 동안 마음 확인 없음 → 호감 {RIFT_AFFECTION}")
            elif state["crisis_msgs"] >= CRISIS_HINT_MSGS and not state["stage_hints"]:
                self._set_hint(state, "HINT_5", state["crisis_msgs"], changes)
            return
        if state["crisis"] != "rift":
            return
        post = max(0, self._event_after(state, "RIFT_START", n_msgs, fallback))  # 엇갈림이 된 뒤의 메시지만
        if post and "withdraw" in flags:
            if state["rift_sorry"]:
                changes.append("엇갈림: 앞의 사과·진심을 거둠")
            state["rift_sorry"] = False
        elif post and flags & {"apologize", "sincere"} and not state["rift_sorry"]:
            state["rift_sorry"] = True
            changes.append("엇갈림: 사과·진심을 기억해 둠 (화해의 문 뒤에 반영)")
        if not self._rift_night_started(state):
            return
        state["crisis_msgs"] += n_msgs
        opened = used.get("RECONCILE_OPEN")
        if opened is None:
            if state["crisis_msgs"] >= RIFT_OPEN_MSGS and not state["pending_event"]:
                state["pending_event"] = "RECONCILE_OPEN"
                used["RECONCILE_OPEN"] = turn
                state["hint_msgs"] = state["crisis_msgs"]
                changes.append("화해의 문: RECONCILE_OPEN" + (" (사과·진심을 기억하고 있음)" if state["rift_sorry"] else ""))
            return
        after = self._event_after(state, "RECONCILE_OPEN", n_msgs, fallback)
        if after < 0:
            state["pending_event"], used["RECONCILE_OPEN"] = "RECONCILE_OPEN", turn
            changes.append("화해의 문 답장이 예비 지문 → 다음 답장에 다시")
            return
        if after and "withdraw" in flags:  # S4-1-1: 명확한 거부가 성공 대사·사과·진심·기억한 사과보다 우선
            changes.append("엇갈림: 회복 거부(withdraw) → 화해 안 함, 엇갈림 유지"
                           + (" (성공 대사가 나왔지만 거부 우선)" if self._won_shown("WON_RECONCILE", shown) else ""))
            return
        sorry = bool(flags & {"apologize", "sincere"}) or state["rift_sorry"]
        if after and (sorry or self._won_shown("WON_RECONCILE", shown)):
            state["affection"] = max(state["affection"], RECONCILE_MIN_AFFECTION)
            why = "엇갈림에서 화해" + ("" if flags & {"apologize", "sincere"} else " (앞서 받은 사과·진심)")
            state["rift_sorry"] = False
            self._won(state, "WON_RECONCILE", shown, changes, why, flags)
        elif (not state["stage_hints"] and not state["rift_sorry"]
              and state["crisis_msgs"] - state["hint_msgs"] >= RECONCILE_HINT_MSGS):
            self._set_hint(state, "HINT_6", state["crisis_msgs"], changes)

    @classmethod
    def _stage_up_seen(cls, state: dict, last_reply: str, n_msgs: int, fallback: bool, changes: list[str]) -> None:
        """S6: 단계가 오른 뒤 상징 장면(STAGE_UP_n, 이름이 있으면 3단계는 이름 장면)이 판정한 답장에 나왔는지 확인.
        나왔으면 그때부터 밤 마무리를 셈. 안 나왔으면 다음 답장에 다시(그 단계 유저 메시지 STAGE_UP_TRIES 개가 되면 나간 것으로 봄).
        앱은 묶음의 서아 답장을 모두 이어 last_reply 로 보내므로 묶음이어도 그 글에서 찾음."""
        n = state["stage_up_due"]
        if not n:
            return
        ev = f"STAGE_UP_{n}"
        if n != state["stage"]:
            state["stage_up_due"] = None
            return
        if state["pending_event"] == ev or not n_msgs:
            return  # 앱이 아직 상징 장면 답장을 내지 않음
        block = cls._stage_up_block(state, ev) or ev
        shown = re.sub(r"\s+", "", last_reply or "")
        named = block == "STAGE_UP_3_NAMED"
        seen = STAGE_UP_KEYS.get(block, "") in shown
        if seen or state["stage_user_msgs"] >= STAGE_UP_TRIES:
            state["stage_up_due"], state["stage_up_msgs"] = None, state["stage_user_msgs"]
            if named:
                if state["name_state"] == "none":
                    state["name_state"] = "asked"
                state["used_events"]["NAME_ASK"] = state["turn"] - 1  # 다음 판정에서 유저의 대답을 봄
            why = "답장에 나옴" if seen else f"메시지 {STAGE_UP_TRIES}개 동안 안 나옴 → 나간 것으로 봄"
            changes.append(f"상징 장면 {block}: {why}")
            return
        if named and state["name_state"] == "asked":
            state["used_events"]["NAME_ASK"] = state["turn"]  # 아직 묻지 않았으니 받아들임으로 넘기지 않음
        if not state["pending_event"]:
            state["pending_event"] = ev
        changes.append(f"상징 장면 {block} 이 답장에 없음 → 다음 답장에 다시")

    @staticmethod
    def _crisis_news(state: dict, last_reply: str, n_msgs: int, changes: list[str]) -> int:
        """S6: 위기 소식 답장이 실제로 나갔는지 판정한 답장(last_reply)으로 확인. 소식 뒤에 온 유저 메시지 수를 돌려줌.
        아직 안 나갔으면 CRISIS 를 다시 예약하고 0(셈·성공 판정 없음). 앱은 묶음의 답장을 줄마다 이어 보내므로,
        줄 수가 메시지 수와 같으면 소식이 나온 줄 뒤의 메시지만 셈(아니면 0)."""
        used, turn = state["used_events"], state["turn"]
        if state["pending_event"] == "CRISIS":
            used["CRISIS"] = turn  # 예약만 됨: 앱이 아직 소식 답장을 내지 않음
            return 0
        if state["crisis_news"]:
            return n_msgs
        if not n_msgs:
            return 0
        key = CRISIS_NEWS_KEYS[state["route"] or "care"]
        if key in re.sub(r"\s+", "", last_reply or ""):
            state["crisis_news"] = True
            Engine._moment_opened(state, "umbrella", changes)  # S8: 소식 답장과 함께 열린 우산
            rows = [x for x in (last_reply or "").split("\n") if x.strip()]
            at = next((i for i, x in enumerate(rows) if key in re.sub(r"\s+", "", x)), len(rows))
            post = len(rows) - 1 - at if len(rows) == n_msgs else 0
            changes.append("위기 소식이 나감" + (f" (그 뒤 메시지 {post}개)" if post else ""))
            return post
        tries = int(used.get("CRISIS_TRY") or 1)
        if tries >= CRISIS_NEWS_TRIES:
            state["crisis_news"] = True
            Engine._moment_opened(state, "umbrella", changes)
            changes.append(f"위기 소식이 {tries}번 안 나옴 → 나간 것으로 보고 진행")
            return 0
        used["CRISIS_TRY"] = tries + 1
        state["pending_event"], used["CRISIS"] = "CRISIS", turn
        changes.append("위기 소식이 답장에 없음 → 다음 답장에 다시 (셈·성공 판정 없음)")
        return 0

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
        """1→2, 2→3 로 오른 뒤 상징 장면 답장 다음부터 유저 메시지 NIGHT_END_AFTER 개가 지나면 다음 답장에 그 밤의 마무리(NIGHT_END) 한 번."""
        st = state["stage"]
        if (st not in NIGHT_END_STAGES or state["night_next"] or state["pending_event"] or state["ending"] or state["scene"]
                or state["stage_up_due"]):
            return  # S6: 상징 장면이 나가기 전에는 예약하지 않음
        if f"NIGHT_END_{st}" in state["used_events"] or state["stage_user_msgs"] - state["stage_up_msgs"] < NIGHT_END_AFTER:
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
        state["night_gap"] = "하루" if NIGHT_NEXT[nxt][1] == 1 else "며칠"  # S9
        state["time_of_day"], state["location"] = "밤", NIGHT_LOCATION
        state["night_next"], state["night_card"], state["night_wait"] = None, None, 0
        Engine._drop_scene(state, changes, "밤 넘기기")
        if state["stage_up_due"]:  # S6: 상징 장면은 다음 밤 첫머리로 넘기지 않음
            if state["pending_event"] == f"STAGE_UP_{state['stage_up_due']}":
                state["pending_event"] = None
            state["stage_up_due"], state["stage_up_msgs"] = None, state["stage_user_msgs"]
        if state["crisis"] == "rift":
            state["crisis_msgs"] = 0
        changes.append(f"다음 밤 ({why}): {state['day']}일째 밤, {nxt}")

    def _won(self, state: dict, won: str, shown: str, changes: list[str], why: str, flags: set | None = None) -> None:
        """마음 확인. S7: 바로 6단계로 올리지 않고 서아가 관계를 묻는다(LOVERS_ASK, 5단계 그대로).
        성공 대사가 판정한 답장에 없었으면 다음 답장 앞에 넣음. 같은 묶음에서 유저가 이미 사귀자고 했으면(lovers_yes)
        물음을 건너뛰고 6단계(다음 답장: 원고 3절 → STAGE_UP_6)."""
        state["crisis"] = "done"
        lines = [] if self._won_shown(won, shown) else [won]  # S4-1-1: 성공 판단과 같은 기준(WON_SHOWN)
        said = "" if lines else " (성공 대사가 이번 답장에 이미 나옴)"
        if "lovers_yes" in (flags or set()):
            if LOVERS_KEYS["LOVERS_PROPOSED"] not in shown:
                lines.append("LOVERS_PROPOSED")
            state["won_line"] = "+".join(lines) or None
            state["lovers"] = "done"
            changes.append(f"{why} · 유저가 먼저 사귀자고 함 → 연인 확정, 6단계{said}")
            self._advance(state, 6, changes)
            return
        state["won_line"] = "+".join(lines) or None
        state["lovers"], state["lovers_msgs"], state["lovers_asks"] = "ask", 0, 0
        state["pending_event"] = "LOVERS_ASK"
        state["used_events"]["LOVERS_ASK"] = state["turn"]  # 물음 답장을 판정하는 차례는 그 전의 말
        state["used_events"].pop("LOVERS_ASK_TRY", None)
        state["stage_hints"], state["hint_msgs"], state["hint_due"] = 0, 0, None
        changes.append(f"{why} → 연인 확정 묻기 (LOVERS_ASK){said}")

    def _lovers(self, state: dict, judge: dict, last_reply: str, n_msgs: int, changes: list[str],
                fallback: bool = False, first_msg: str = "", ring: bool = False) -> None:
        """S7 연인 확정(5단계, lovers ask·wait). 받아 주면(lovers_yes) 6단계, 망설이면(lovers_later) 기다리고
        유저 메시지 LOVERS_AGAIN_MSGS 개마다 다시 묻기(최대 LOVERS_ASKS_MAX). 물음이 답장에 안 나왔으면 다시 넣음."""
        flags, used = set(judge["flags"]), state["used_events"]
        shown = re.sub(r"\s+", "", last_reply or "")
        kind = self._lovers_kind(state)
        yes, later = "lovers_yes" in flags, "lovers_later" in flags
        if state["lovers"] == "ask":
            if state["pending_event"] == "LOVERS_ASK":
                used["LOVERS_ASK"] = state["turn"]
                return  # 앱이 아직 물음 답장을 내지 않음
            if not n_msgs:
                return
            post = self._event_after(state, "LOVERS_ASK", n_msgs, fallback)
            first = state["turn"] < used["LOVERS_ASK"] + 2  # 물음 답장을 판정하는 차례
            if yes and first and post <= 0 and not ring and not self._proposes(first_msg):
                yes = False  # S8-1: 묻기 전의 말(「응, 같이 내려가자」)은 대답이 아님. 직접 사귀자는 말이 있을 때만
                judge["flags"] = [f for f in judge["flags"] if f != "lovers_yes"]
                changes.append("연인 확정: 묻기 전의 말에 사귀자는 말이 없음 → lovers_yes 뺌")
            if first and (post < 0 or (LOVERS_KEYS[f"LOVERS_ASK_{kind}"] not in shown and not yes)):
                tries = int(used.get("LOVERS_ASK_TRY") or 1)
                if tries < LOVERS_ASK_TRIES:
                    used["LOVERS_ASK_TRY"] = tries + 1
                    state["pending_event"], used["LOVERS_ASK"] = "LOVERS_ASK", state["turn"]
                    changes.append("연인 확정 물음이 답장에 없음 → 다음 답장에 다시")
                    return
                changes.append(f"연인 확정 물음이 {tries}번 안 나옴 → 나간 것으로 보고 진행")
            if first:
                self._moment_opened(state, "ring", changes)  # S8: 물음 답장과 함께 열린 반지
            if yes:  # 물음 답장을 판정하는 차례의 한 메시지는 묻기 전의 말 → 유저가 먼저 사귀자고 함(3절)
                self._lovers_done(state, "LOVERS_PROPOSED" if first and post <= 0 else f"LOVERS_YES_{kind}",
                                  shown, changes)
            elif later and post > 0:
                state["lovers"], state["lovers_msgs"] = "wait", 0
                changes.append("연인 확정: 유저가 망설임 → 기다림 (원고 4절)")
            return
        if yes:  # wait
            self._lovers_done(state, "LOVERS_PROPOSED" if state["lovers_asks"] >= LOVERS_ASKS_MAX
                              else f"LOVERS_YES_{kind}", shown, changes)
            return
        if state["pending_event"] == "LOVERS_AGAIN":
            return  # 앱이 아직 다시 묻기 답장을 내지 않음
        state["lovers_msgs"] += n_msgs
        if (state["lovers_msgs"] >= LOVERS_AGAIN_MSGS and state["lovers_asks"] < LOVERS_ASKS_MAX
                and not state["pending_event"]):
            state["pending_event"], used["LOVERS_AGAIN"] = "LOVERS_AGAIN", state["turn"]
            state["lovers_asks"] += 1
            state["lovers_msgs"] = 0
            changes.append(f"연인 확정: 다시 묻기 LOVERS_AGAIN ({state['lovers_asks']}/{LOVERS_ASKS_MAX})")

    def _lovers_done(self, state: dict, line: str, shown: str, changes: list[str]) -> None:
        """S7: 연인 확정 → 6단계. 2·3절 대사가 판정한 답장에 없었으면 다음 답장(STAGE_UP_6) 앞에 넣음."""
        said = any(LOVERS_KEYS[k] in shown for k in LOVERS_YES_LINES)
        state["won_line"] = None if said else line
        state["lovers"], state["lovers_msgs"] = "done", 0
        changes.append("연인 확정 → 6단계" + (" (수락 대사가 이번 답장에 이미 나옴)" if said else f" ({line} 를 STAGE_UP_6 앞에)"))
        self._advance(state, 6, changes)

    @staticmethod
    def _proposes(msg: str) -> bool:
        """S8-1: 메시지에 직접 사귀자·좋아한다는 말이 있는지(LOVERS_PROPOSE_WORDS, 띄어쓰기 무시)."""
        text = re.sub(r"\s+", "", msg or "")
        return any(re.sub(r"\s+", "", w) in text for w in LOVERS_PROPOSE_WORDS)

    @staticmethod
    def _lovers_kind(state: dict) -> str:
        """S7: 연인 확정 원고 갈래. 엇갈림을 지나 화해했으면 RECONCILE, 아니면 갈래(없으면 CARE)."""
        return "RECONCILE" if "RIFT_START" in state["used_events"] else (state.get("route") or "care").upper()

    @staticmethod
    def lovers_call(state: dict) -> str:
        """S7 원고 {호칭}: 이름을 부를 수 있으면(name_state accepted·forgiven) 유저 이름만, 아니면 「사장님」."""
        name = state.get("user_name") or ""
        return name if name and state.get("name_state") in ("accepted", "forgiven") else "사장님"

    def _lovers_parts(self, state: dict, event: str | None) -> list[str]:
        """S7: 물음(LOVERS_ASK)·다시 묻기(LOVERS_AGAIN) 답장에는 그 블록, 그 밖의 답장에는 LOVERS_WATCH(갈래 줄만)."""
        kind = self._lovers_kind(state)
        if event == "LOVERS_ASK":
            lines = state["won_line"].split("+") if state.get("won_line") else []
            parts = [self.blocks[x] for x in lines if x in WON_KEYS]
            return parts + [self.blocks[f"LOVERS_ASK_{kind}"].replace("{호칭}", self.lovers_call(state))]
        if event == "LOVERS_AGAIN":
            return [self.blocks["LOVERS_AGAIN"]]
        tag = f"[{kind.lower()}] "
        rows = self.blocks["LOVERS_WATCH"].split("\n")
        return ["\n".join(x[len(tag):] if x.startswith(tag) else x for x in rows
                          if not x.startswith("[") or x.startswith(tag))]

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
                or cls._name_scene_quiet(state) or state.get("lovers") in ("ask", "wait")):
            return
        if msgs < MIN_STAGE_MSGS.get(st, 0) or state["stage_hints"] >= HINT_MAX:
            return
        if cls._hint_stale(state, name):
            return  # S6: 그 힌트가 가리키는 것을 이미 함(예: 치료한 뒤 「신경 써 주려나」)
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
        if state["lovers"] in ("ask", "wait"):
            return None  # S7: 연인 확정 중에는 무작위 사건·조연 없음
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

    @staticmethod
    def _note_request(state: dict, recent: list[dict]) -> tuple[int, str] | None:
        """S4-1: 기록 못 한 끝난 단계 중, 받은 recent 에 그 단계 구간이 충분히 남은 첫 단계와 그 구간 대화.
        구간 = 그 단계가 끝난 뒤의 유저 메시지(after)를 뒤에서 잘라 내고, 그 단계 유저 메시지 수(msgs)만큼 앞에서 자름.
        모자라면 None(지어내지 않고 미룸). need_summary 와 상관없음."""
        rows = [m for m in recent if m.get("r") in ("u", "c") and str(m.get("t") or "").strip()
                and str(m.get("t")).strip() != NEXT_NIGHT]
        for e in state.get("stage_note_queue") or []:
            users = [i for i, m in enumerate(rows) if m["r"] == "u"]
            part = rows
            if e["after"]:
                if len(users) < e["after"]:
                    continue
                part = rows[:users[-e["after"]]]
            if e["msgs"]:
                pu = [i for i, m in enumerate(part) if m["r"] == "u"]
                if len(pu) > e["msgs"]:
                    part = part[pu[-e["msgs"]]:]
            part = part[-STAGE_NOTE_RECENT_MAX:]
            if len(part) < STAGE_NOTE_MIN_RECENT:
                continue
            convo = "\n".join(f"{'유저' if m['r'] == 'u' else '서아'}: {m['t']}" for m in part)
            return e["stage"], convo
        return None

    def _judge(self, state: dict, user_message: str, reply: str,
               need_summary: bool = False, recent: list[dict] | None = None, notes: list[str] | None = None,
               note: tuple[int, str] | None = None) -> dict:
        prompt = (
            f"[현재 상태]\n단계: {state['stage']} ({state['episode_stage_name'] or STAGE_NAMES[state['stage']]}), "
            f"호감도: {state['affection']}, {state['day']}일째 {state['time_of_day']}, "
            f"장소: {state['location']}"
            + ("" if state["episode"] else f", 이름 부르기: {self._judge_name_call(state)}")
            + (f", 연인 확정: {LOVERS_JUDGE_LINE[state['lovers']]}"
               if not state["episode"] and state["lovers"] in LOVERS_JUDGE_LINE else "")
            + f"\n\n[지금까지의 요약]\n{state['summary'] or '없음'}"
        )
        if need_summary:
            convo = "\n".join(
                f"{'유저' if m.get('r') == 'u' else '서아'}: {m.get('t')}"
                for m in (recent or [])[-24:] if m.get("r") in ("u", "c")
            )
            prompt += f"\n\n[요약 요청]\n기존 요약과 아래 최근 대화를 합쳐 summary를 채운다.\n[최근 대화]\n{convo or '없음'}"
        due = None if state["episode"] or not note else note[0]
        if due:  # S3: 끝난 단계를 한 문장으로 (S4-1: 받은 recent 의 그 단계 구간만 보고)
            prompt += (f"\n\n[단계 기록 요청] {due}단계({STAGE_NAMES[due]})를 아래 [단계 기록용 대화]만 보고 한 문장으로. "
                       f"그 단계가 끝난 뒤에 일어난 일은 넣지 않는다.\n[단계 기록용 대화]\n{note[1]}")
        for note in notes or []:  # S4-1 위기·엇갈림 판정 지시
            prompt += f"\n\n{note}"
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
    def _remember(state: dict, judge: dict, due: int | None = None) -> list[str]:
        """S3 기억(본편만): 부탁한 단계 기록을 stage_notes 에, 서아의 약속·제안을 promises 에.
        S4-1: 이번 판정에서 부탁한 단계(due)만 저장하고 목록에서 뺌. 비거나 실패하면 목록에 남아 다음에 다시."""
        changes = []
        note = (judge.get("stage_note") or "")[:MEMO_MAX_LEN]
        if due and note:
            state["stage_notes"][str(due)] = note
            state["stage_note_queue"] = [e for e in state["stage_note_queue"] if e["stage"] != due]
            state["stage_note_due"] = state["stage_note_queue"][0]["stage"] if state["stage_note_queue"] else None
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
            if flag not in state["stage_flags"] and flag not in JUDGE_ONLY_FLAGS:
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
        state["stage_up_due"], state["stage_up_msgs"] = None, 0
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
            if state["pending_event"] == "NIGHT_END":  # S6: 이름 장면 > 밤 마무리. 밤 마무리는 이름 장면 뒤로 미룸
                state["used_events"].pop("NIGHT_END_2" if state["night_next"] == "NIGHT_START_2" else "NIGHT_END_3", None)
                state["night_next"], state["night_card"], state["night_wait"] = None, None, 0
                state["pending_event"] = None
                changes.append("밤 마무리를 이름 장면 뒤로 미룸")
            if state["pending_event"] not in CRISIS_EVENTS | ROUTE_SCENES | LOVERS_EVENTS:  # 위기·필수·연인 확정 사건은 밀어내지 않음
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
            if state["stage_up_due"] != 3 and state["turn"] >= state["used_events"].get("NAME_ASK", -10_000) + 2:
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
        old, queue = state["stage"], state.setdefault("stage_note_queue", [])
        if (not state.get("episode") and str(old) not in state.get("stage_notes", {})
                and all(e["stage"] != old for e in queue)):  # S4-1: 정상으로 마친 단계만 기록 요청(하락·이름 흐름은 아님)
            queue.append({"stage": old, "after": 0, "msgs": state.get("stage_user_msgs", 0)})
            state["stage_note_due"] = queue[0]["stage"]
        state["stage"] = nxt
        state["stage_flags"] = []
        state["stage_bonus"], state["stage_user_msgs"] = 0, 0  # 단계마다 새로 셈
        state["stage_hints"], state["hint_msgs"], state["hint_due"] = 0, 0, None
        changes.append(f"단계 상승 → {nxt}. {STAGE_NAMES[nxt]}")
        state["stage_up_due"], state["stage_up_msgs"] = None, 0
        if nxt in STAGE_UP_SCENES:
            state["pending_event"] = f"STAGE_UP_{nxt}"  # 다음 답장에 그 단계의 상징 장면 (다른 사건보다 먼저)
        if nxt == 3 and state.get("user_name") and state.get("name_state", "none") == "none":
            state["name_state"] = "asked"  # 다음 답장이 STAGE_UP_3_NAMED (이름으로 부르고 싶다고 물음)
            state["used_events"]["NAME_ASK"] = state["turn"]
            changes.append("이름 부르기 물음 (STAGE_UP_3_NAMED)")
        if nxt in STAGE_UP_SCENES and not state.get("episode") and Engine._stage_up_block(state, f"STAGE_UP_{nxt}"):
            state["stage_up_due"] = nxt  # S6: 상징 장면이 답장에 나온 것을 확인할 때까지 밤 마무리·이름 받아들임을 미룸
        if nxt == 4 and not state["route"] and "비밀1" not in state["revealed_secrets"]:
            state["revealed_secrets"].append("비밀1")  # 4단계 지침대로 1년 전 이야기를 털어놓게 함
            changes.append("비밀1 공개")
        if nxt == 5:  # S4: 그 골목에 이어 같은 밤, 서아가 새벽에 떠난다는 소식을 꺼냄 (DEPARTURE 대신)
            state["pending_event"] = "CRISIS"
            state["crisis"], state["crisis_msgs"], state["rift_sorry"] = "on", 0, False
            state["crisis_news"] = False
            state["used_events"].pop("CRISIS_TRY", None)
            state["used_events"]["CRISIS"] = state["turn"]  # 소식을 꺼낸 답장을 판정하는 차례는 셈하지 않음
            changes.append("위기: 새벽에 떠난다는 소식 (CRISIS)")
        if nxt == 6:
            state["departure_days_left"] = None  # 성공 엔딩 stayed 는 첫 아침·조연 마무리 뒤 _side_finale 이 정함
            state["crisis"], state["rift_sorry"] = "done", False

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
