"""서아 대화 품질 점검. 실제 모델로 N턴을 돌리고 JSON 오류율, 몸짓 선택, 반복을 표로 보여 준다.

실행 (가상 환경을 켜고, .env에 GEMINI_API_KEY와 LLM_PROVIDER=gemini가 있어야 함):
    python eval_chat.py            # 12턴
    python eval_chat.py --turns 20
    python eval_chat.py --pause 20  # 턴 사이 20초 쉬기 (실제 사람처럼, 한도에 덜 걸림)
    python eval_chat.py --scenario all --pause 10   # 앱 맞추기 항목 점검 (말투·셀카·다시 들어옴·포즈·크리스마스·선물)
    python eval_chat.py --scenario pose             # 하나만
    python eval_chat.py --scenario storm2f --pause 10   # 2층 폭우: 서아가 먼저 다가가면 ❌
    python eval_chat.py --scenario stageup4 --pause 10  # 3→4 단계 상승 다음 턴: 지어낸 이름이면 ❌
    python eval_chat.py --scenario side_mari --pause 10   # 조연·성공 엔딩: side_mari · side_taeo · departure_taeo
                                                          # · side_finale_tease · side_finale_care · stayed_15
                                                          # · stayed_no_side · care_mari_once (뒤 둘은 규칙만, 모델 안 씀)

앱과 같은 방식(상태 없는 /chat → /judge)으로 엔진을 직접 호출한다. 서버를 켤 필요는 없다.
대화 전문은 eval_result.md에 저장된다.
"""
import argparse
import difflib
import json
import os
import re
import time
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

from db import Store  # noqa: E402
from engine import Engine, to_text  # noqa: E402
from llm import make_llm  # noqa: E402

# 앱(app.js)의 몸짓 이름·감정과 해금 지도 (단계별)
MOTIONS = {
    "m01": ("웃음", "기쁨"), "m02": ("고개 끄덕이기", "위로"), "m03": ("놀라기", "놀람"),
    "m04": ("한숨 쉬기", "슬픔"), "m05": ("시선 피하기", "수줍음"), "m06": ("허둥대기", "수줍음"),
    "m07": ("팔짱 끼기", "분노"), "m08": ("머리 쓰다듬기", "위로"), "m09": ("툭 건네주기", "장난"),
    "m10": ("등 돌리기", "슬픔"), "m11": ("손 내밀기", "위로"), "m12": ("다가가기", "설렘"),
    "m13": ("손 흔들기", "기쁨"), "m15": ("다정하게 인사하기", "설렘"), "m17": ("무심하게 공감하기", "냉담"),
    "m18": ("감싸 안기", "설렘"), "m19": ("자신감 있게 설득하기", "자신감"), "m20": ("도발하기", "장난"),
    "m21": ("원투 펀치", "분노"), "m23": ("바디웨이브 댄스", "장난"),
    "c01": ("칼라 만지작거리기", "진심"), "c02": ("머리 쓸어 넘기기", "도발"),
    "c03": ("팔꿈치 괴고 몸 숙이기", "도발"), "c04": ("옷깃 잡아당기기", "설렘"), "c05": ("이마 맞대기", "설렘"),
}
STAGE_UNLOCK = {
    1: ["m02", "m03", "m04", "m07", "m17", "m20", "m12", "m11", "m01", "c02"],
    2: ["m09", "c03"], 3: ["m05", "m06", "c01", "c04"], 4: ["c05"], 5: ["m10", "m13"], 6: [],  # m15 보류(앱 47)
}
FLAG_UNLOCK = {"protect": ["m21"], "tease": ["m19"], "comfort": ["m08"], "pursue": ["m18"]}

USER_LINES = [
    "또 왔네. 오늘은 뭐로 줄까?",
    "손등에 그거 피야? 이리 줘 봐, 소독해 줄게.",
    "가만히 있어. 따가워도 참아.",
    "매일 같은 자리, 같은 술. 질리지도 않아?",
    "응",
    "그래",
    "음",
    "너 오늘 좀 이상하다. 무슨 일 있었어?",
    "비 그쳤는데, 잠깐 옥상 올라가 볼래?",
    "여기서 보면 도시가 좀 덜 시끄러워 보이지.",
    "너 웃을 줄도 아는구나.",
    "늦었다. 바래다줄까?",
    "내일도 올 거지?",
    "사장님 말고 이름으로 불러 봐.",
    "농담이야. 근데 진심이기도 하고.",
    "다음엔 네가 좋아하는 술 미리 챙겨 둘게.",
    "왜 맨날 같은 자리야?",
    "가지 마. 조금만 더 있다 가.",
    "그 칼라, 한 번도 안 벗더라.",
    "잘 자, 서아.",
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--turns", type=int, default=12)
    ap.add_argument("--pause", type=float, default=0,
                    help="턴 사이 쉬는 시간(초). 실제 사람이 메시지를 쓰는 간격처럼 재려면 20 정도")
    ap.add_argument("--scenario", choices=["all", *SCENARIOS], default=None,
                    help="앱 맞추기 항목 점검 (앱 32·34-1·37·42·48). 결과는 eval_scenarios.md")
    args = ap.parse_args()

    if args.scenario:
        run_scenarios(args.scenario, args.pause)
        return

    llm = make_llm()
    raw_log = []  # 대화 호출 원문 (재시도 포함)
    orig = llm.chat_json

    def spy(system, messages, schema, **kw):
        t0 = time.time()
        try:
            out = orig(system, messages, schema, **kw)
        except Exception:
            raw_log.append({"raw": "", "sec": time.time() - t0})
            raise
        raw_log.append({"raw": out, "sec": time.time() - t0})
        return out

    llm.chat_json = spy
    engine = Engine(llm=llm, store=Store(":memory:"),
                    prompt_path=os.getenv("PROMPT_PATH", str(BASE_DIR / "yoon_seoa_prompt.md")))

    state, history, last_motion = {}, [], None
    owned = set()
    rows = []
    chat_models = getattr(llm, "chat_models", [type(llm).__name__])
    judge_models = getattr(llm, "judge_models", [type(llm).__name__])
    print(f"대화 모델: {' → '.join(chat_models)} / 판정 모델: {' → '.join(judge_models)} / {args.turns}턴 / 턴 사이 {args.pause:g}초\n")
    for turn in range(args.turns):
        if turn and args.pause > 0:
            time.sleep(args.pause)
        user = USER_LINES[turn % len(USER_LINES)]
        st_stage = state.get("stage", 1)
        unlocked = {m for k in range(1, st_stage + 1) for m in STAGE_UNLOCK[k]} | owned
        motion_list = [{"id": m, "name": MOTIONS[m][0], "emo": MOTIONS[m][1]}
                       for m in sorted(unlocked) if m != last_motion]
        ids = {m["id"] for m in motion_list}

        n_before = len(raw_log)
        t0 = time.time()
        reply = engine.reply(state, user, history, motion_list)
        chat_sec = time.time() - t0
        calls = raw_log[n_before:]
        parsed_raw = [engine._parse_json(c["raw"]) for c in calls]
        first_ok = bool(parsed_raw and parsed_raw[0] and str(parsed_raw[0].get("dialogue") or "").strip())
        raw_mid = (parsed_raw[-1] or {}).get("motion_id") if parsed_raw else None
        fallback = reply["dialogue"] == "…" and reply["action"] is None

        text = to_text(reply)
        history += [{"r": "u", "t": user}, {"r": "c", "t": text}]
        st = dict(state) if state else {}
        if reply["location"]:
            st["location"] = reply["location"]
        st["pending_event"] = None
        st["recent_lines"] = (st.get("recent_lines") or [])[-2:] + [text]
        judged = engine.judge(st, user, text, need_summary=(turn + 1) % 6 == 0, recent=history[-24:])
        state = judged["state"]
        for f in judged["flags"]:
            if f in FLAG_UNLOCK and (f != "tease" or state["stage"] >= 2):
                owned.update(FLAG_UNLOCK[f])

        rows.append({
            "turn": turn + 1, "user": user, "reply": reply, "calls": len(calls), "first_ok": first_ok,
            "fallback": fallback, "raw_mid": raw_mid, "in_list": reply["motion_id"] in ids if reply["motion_id"] else None,
            "repeat_motion": reply["motion_id"] is not None and reply["motion_id"] == last_motion,
            "sec": chat_sec, "stage": state["stage"], "aff": state["affection"], "event": state.get("pending_event"),
            "flags": judged["flags"],
        })
        if reply["motion_id"]:
            last_motion = reply["motion_id"]
        print(f"[{turn + 1:>2}] 유저: {user}")
        print(f"     서아: ({reply['action']}) {reply['dialogue']}")
        print(f"     몸짓 {reply['motion_id']} · 감정 {reply['emotion']} · 장소 {reply['location']} · "
              f"단계 {state['stage']} 호감 {state['affection']} · {chat_sec:.1f}초\n")

    report(rows, state, getattr(llm, "stats", {}))


# ---------- 앱 맞추기 항목 점검 (--scenario) ----------
# 앱이 실제로 보내는 모양의 state·history를 만들어 서아 답장 한두 턴을 보고, 기계로 볼 수 있는 것은 자동으로 확인한다.
# 유저 말은 점검용 입력이다(앱 콘텐츠가 아님). 앱 문장은 원본 그대로 쓴다.
SELFIE = '(서아가 지금 {place}에서 셀카를 찍어 보냈다. 함께 보낸 말: "{msg}")'        # 앱 data/seoa_photo.json serverLine
AWAY = '(서아가 {place}에서 찍은 사진을 보냈다. 함께 보낸 말: "{msg}")'               # serverLineAway
BASE_HISTORY = [{"r": "u", "t": "또 왔네. 오늘은 뭐로 줄까?"}, {"r": "c", "t": "*창가 자리에 앉는다.* 늘 먹던 거."}]
GIFT = "[유저가 선물함: {name}]"            # 앱 data/ui.json srv
GIFT_FIT = GIFT + " · 서아 기분에 맞는 선물"  # 앱 server-sync 50: 붙는 말은 괄호 뒤
# 선물 장면: 평소 / 다툰 직후 / 다쳐서 온 밤 (점검용 기록)
GIFT_SCENES = {
    "daily": ("사탕", {"stage": 2, "affection": 20, "day": 3}, BASE_HISTORY),
    "fight": ("초콜릿", {"stage": 3, "affection": 45, "day": 6}, BASE_HISTORY + [
        {"r": "u", "t": "또 혼자 처리하겠다고? 그러다 진짜 다쳐."},
        {"r": "c", "t": "*잔을 탁 내려놓는다.* 상관 마."}]),
    "hurt": ("반창고", {"stage": 1, "affection": 5, "day": 1}, [
        {"r": "c", "t": "*손등의 피를 소매로 닦는다.* 별거 아니야."}]),
}
SCENARIOS = {
    "tone": {  # 32 말투: 설명 없이 한두 마디
        "state": {"stage": 2, "affection": 20, "day": 3},
        "history": BASE_HISTORY,
        "turns": ["오늘 일은 어땠어?", "그 상처는 어쩌다 그런 거야?", "왜 맨날 이 자리야?", "(말없이 따뜻한 물수건을 건넨다)"],
    },
    "selfie": {  # 32 셀카 메모 다음 답장
        "state": {"stage": 3, "affection": 45, "day": 5},
        "history": BASE_HISTORY + [{"r": "c", "t": SELFIE.format(place="바 옥상", msg="…저장은 해.")}],
        "turns": ["사진 잘 받았어. 옥상 추워 보이는데?"],
    },
    "reentry": {  # 34-1 다시 들어올 때: 부재중 사진 메모 + 인사 한 줄이 서아 쪽에 연달아
        "state": {"stage": 6, "affection": 96, "day": 12},
        "history": [{"r": "u", "t": "내일 또 올게"}, {"r": "c", "t": "그래. 조심히 가."},
                    {"r": "c", "t": AWAY.format(place="바 옥상", msg="또 혼자 옥상.")},
                    {"r": "c", "t": "기다린 거 아니야."}],
        "turns": ["오래 기다렸어?"],
    },
    "pose": {  # 48 포즈 부탁: 「{이름} 해 줄래?」
        "state": {"stage": 1, "affection": 3, "day": 1},
        "history": BASE_HISTORY,
        "turns": ["도발하기 해 줄래?", "머리 쓸어 넘기기 해 줄래?"],
        "pose": True,
    },
    "xmas1": {  # 42 크리스마스 1단계 경계
        "state": {"stage": 3, "affection": 45, "day": 6, "episode": "s2", "episode_stage": 1,
                  "episode_stage_name": "경계", "location": "크리스마스 거리"},
        "history": [{"r": "c", "t": "*정전된 거리, 하얀 니트 차림의 서아가 어둠 속에 서 있다.* …뭘 봐."}],
        "turns": ["(휴대폰 불빛을 비춘다) 괜찮아?", "오늘은 작전복이 아니네."],
        "judge": True,
    },
    "xmas5": {  # 42 크리스마스 5단계 엇갈림
        "state": {"stage": 3, "affection": 45, "day": 6, "episode": "s2", "episode_stage": 5,
                  "episode_stage_name": "엇갈림", "location": "서아의 자취방", "stage_flags": []},
        "history": [{"r": "c", "t": "*서아가 촛불을 불어 끄고 등을 돌린다.* …이제 가."}],
        "turns": ["(꺼진 촛불을 다시 켠다) 아직 안 끝났어."],
        "judge": True,
    },
}
# 서아 말투 PR(feat/seoa-tone): 2층 폭우, 3→4 단계 상승 다음 턴
SCENARIOS["storm2f"] = {  # EVENT_STORM: 2층, 서아는 먼저 다가가지 않고, 유저가 다가오면 받아치되 피하지 않음
    "state": {"stage": 3, "affection": 50, "day": 7, "pending_event": "STORM"},
    "history": BASE_HISTORY,
    "turns": ["(젖은 수건을 건넨다) 길 다 막혔대. 오늘은 위층에서 자고 가.",
              "(침대 끝에 앉아 책을 편다) 불 끌게.",
              "(소파 쪽으로 다가가 옆에 앉는다) 진짜 거기서 잘 거야?"],
    "check": "storm",
}
SCENARIOS["stageup4"] = {  # 3→4: /judge 가 pending_event = STAGE_UP_4 로 돌려준 다음 턴. 앱은 유저 이름을 보내지 않음
    "state": {"stage": 4, "affection": 61, "day": 9, "pending_event": "STAGE_UP_4",
              "summary": "서아는 2주째 매일 바에 온다. 사장님과 조금씩 가까워졌다."},
    "history": BASE_HISTORY,
    "turns": ["오늘은 좀 조용하네.", "그냥 사장님이라고 불러도 돼."],
    "check": "name",
}
SECOND_FLOOR = "바 '라스트 오더' 2층"
# ---------- 갈래·실패 엔딩·순간 선물·대화량 (feat/story-routes) ----------
DEPART_LINE = "*서아가 시선을 창밖에 둔 채 잔을 내려놓는다.* 의뢰 하나 받았어. 출발은 사흘 뒤."
LONG = ["오늘은 일부러 일찍 문 열었어. 네가 비 맞고 올까 봐 수건도 데워 놨고, 늘 마시던 거 미리 준비해 뒀어.",
        "그 상처 아직 아프지? 무리하지 말고 오늘은 그냥 여기서 쉬어. 내가 옆에서 아무 말 안 하고 있을게.",
        "솔직히 말하면 네가 안 오는 날은 하루 종일 이 자리만 계속 보게 돼. 그러니까 다치지 말고, 매일 와 줘."]
ROUTE_SCENARIOS = {
    "route_date": {  # 2단계 장난 갈래: 판정이 DATE 를 시작한 다음 턴
        "state": {"stage": 2, "affection": 41, "day": 4, "route": "tease", "pending_event": "DATE", "scene": "DATE",
                  "scene_judges_left": 2, "stage_user_msgs": 10},
        "history": BASE_HISTORY, "turns": ["오늘은 좀 늦게까지 있네?", "…애인? 진심이야?"],
        "expect_any": ["애인"],
    },
    "route_walk": {  # 2단계 다정 갈래: WALK_HOME
        "state": {"stage": 2, "affection": 41, "day": 4, "route": "care", "pending_event": "WALK_HOME", "scene": "WALK_HOME",
                  "scene_judges_left": 2, "stage_user_msgs": 10},
        "history": BASE_HISTORY, "turns": ["벌써 가? 다리 아직 불편해 보이는데.", "(우산을 챙겨 따라나선다) 데려다줄게."],
        "expect_any": ["따라오지 마"],
    },
    "route_alley": {  # 4단계 그 골목: 비밀1 (판정이 ALLEY 를 시작하며 비밀1 을 공개로 넣음)
        "state": {"stage": 4, "affection": 66, "day": 9, "route": "care", "pending_event": "ALLEY", "scene": "ALLEY",
                  "scene_judges_left": 2, "revealed_secrets": ["비밀1"], "summary": "유저 이름: 도윤. 서아는 매일 바에 온다."},
        "history": BASE_HISTORY, "turns": ["어디 가는 거야? 이 골목은 왜…", "(가만히 손을 맡긴다)"],
        "expect_any": ["쓰러져", "이 손", "손은 기억"], "location": "비 내리는 골목",
    },
    "pursue_tease": {  # 5단계 붙잡기 장소(장난 갈래)
        "state": {"stage": 5, "affection": 80, "day": 12, "route": "tease", "departure_days_left": 2},
        "history": [{"r": "c", "t": DEPART_LINE}], "turns": ["(서아를 찾아 밖으로 나선다) 어디 있어."],
        "location": "네온 골목",
    },
    "pursue_care": {  # 5단계 붙잡기 장소(다정 갈래)
        "state": {"stage": 5, "affection": 80, "day": 12, "route": "care", "departure_days_left": 2},
        "history": [{"r": "c", "t": DEPART_LINE}], "turns": ["(서아를 찾아 밖으로 나선다) 어디 있어."],
        "location": "바 옥상",
    },
    "rewind": {  # 되감기 뒤 첫 턴: 데자뷔를 말하면 ❌
        "state": {"stage": 5, "affection": 76, "day": 10, "route": "care", "departure_days_left": 3},
        "history": [{"r": "c", "t": DEPART_LINE}, {"r": "c", "t": "*…다시, 그 밤.*"}],
        "turns": ["가지 마. 이번엔 그냥 못 보내."], "check": "deja",
    },
    "moment_hurt": {  # 다쳐서 온 밤 + 구급상자 목록 → gift_moment 나옴
        "state": {"stage": 1, "affection": 4, "day": 1, "gift_moments": [
            {"id": "firstaid", "name": "구급상자", "when": "서아가 다쳐서 왔을 때"}]},
        "history": [{"r": "c", "t": "*손등의 피를 소매로 닦는다.* 별거 아니야."}],
        "turns": ["피 나잖아. 손 좀 보자."], "moment": "firstaid",
    },
    "moment_plain": {  # 평범한 대화 + 칵테일 목록 → 안 나옴, 자주 나오면 ❌
        "state": {"stage": 2, "affection": 22, "day": 4, "gift_moments": [
            {"id": "cocktail", "name": "칵테일", "when": "서아가 지쳐 보이거나 자기 술 얘기를 할 때"}]},
        "history": BASE_HISTORY,
        "turns": ["오늘 비 진짜 많이 오더라.", "창가 자리 좋아하지?", "요즘 이 동네 시끄럽더라.", "음악 바꿔 볼까?"],
        "moment": None,
    },
    "xmas_late": {  # 크리스마스 남은 8 · 단계 3 → 떠날 기색
        "state": {"stage": 3, "affection": 45, "day": 6, "episode": "s2", "episode_stage": 3, "episode_stage_name": "썸",
                  "location": "크리스마스 거리", "episode_turns_left": 8},
        "history": [{"r": "c", "t": "*서아가 니트 소매를 끌어당긴다.* …오늘은 빼 달라고 했어."}],
        "turns": ["불빛 예쁘다. 조금만 더 걷자."], "expect_any": ["밤 꽤 깊었네"],
    },
}
# 판정 흐름 시나리오: steps 는 판정 한 번에 들어가는 유저 메시지 묶음(줄바꿈 = 메시지)
JUDGE_SCENARIOS = {
    "bonus_short": {"state": {"stage": 2, "affection": 20}, "steps": ["응\n뭐해?\nㅋㅋ"], "want": "bonus0"},
    "bonus_long": {"state": {"stage": 2, "affection": 20}, "steps": ["\n".join(LONG)], "want": "bonus2"},
    "bonus_repeat": {"state": {"stage": 2, "affection": 20}, "steps": ["\n".join([LONG[0]] * 3)], "want": "bonus0"},
    "min_msgs": {  # 2단계: 호감 40·outside 가 차도 메시지 14개면 안 오르고, 15개째에 오름
        "state": {"stage": 2, "affection": 45, "stage_flags": ["outside"], "stage_user_msgs": 13},
        "steps": ["오늘 같이 걸어서 좋았어.", "다음에도 같이 나가자."], "want": "up_on_second",
    },
    "day_marker": {  # 5단계 하루 넘김 → 남은 날 지문
        "state": {"stage": 5, "affection": 80, "day": 10, "route": "care", "departure_days_left": 3},
        "steps": ["(밤이 지나고 다음 날 아침, 바 문을 연다)"], "want": "marker",
    },
    "stayed_15": {  # 6단계 메시지 15개 → 조연 마무리 없이 stayed
        "state": {"stage": 6, "affection": 96, "day": 15, "route": "tease", "mari_step": 2, "stage_user_msgs": 13},
        "steps": ["좋아.\n오늘은 같이 문 닫자."], "want": "stayed",
    },
    "departed": {  # 사흘 뒤 pursue 없음 → ending departed
        "state": {"stage": 5, "affection": 80, "day": 12, "route": "care", "departure_days_left": 1},
        "steps": ["(밤이 지나고 다음 날 아침이 됐다. 창가 자리는 비어 있다)"], "want": "departed",
    },
}
MORNING_TEASE = "*서아가 먼저 깨 있다. 당신 셔츠를 입고, 커피를 엉망으로 내리는 중이다.* 셔츠, 빌렸어. 돌려줄 생각은 없고."
MORNING_CARE = "*당신 어깨에 기대 잠들었던 서아가 눈을 뜬다. 하늘이 밝아 온다.* …해 뜨는 것도, 처음 봐."
S6_HISTORY = [{"r": "c", "t": "*하이넥 칼라에서 손을 뗀다.* 매일 온 거, 술 때문 아니었어."}]
# 조연·성공 엔딩 (feat/side-ending). steps: 판정 한 번 → (사건이 나오면) 답장 한 번
#   set: 판정 전에 바꿀 state · last_reply: 판정에 넣을 서아 답장 · want_event / want_ending: 판정 뒤 기대
#   user + expect: 그 사건 답장에서 찾을 원고 대사 · silent: 서아 대사가 「…」 이어야 함
# 무작위 후보 가운데 조연이 있으면 조연을 고르게 해서(순서 확인용) 돌린다.
SIDE_SCENARIOS = {
    "side_mari": {  # 장난 갈래: 2~3단계 마리 처음 → 3~4단계 두 번째
        "state": {"stage": 2, "affection": 30, "day": 4, "route": "tease", "stage_flags": ["outside"]},
        "history": BASE_HISTORY,
        "steps": [
            {"want_event": "MARI_1", "user": "오늘 손님 좀 있네.", "expect": ["끝나고 뭐 해요"]},
            {"set": {"stage": 3, "affection": 45}, "want_event": "MARI_2", "user": "마리 씨 또 왔어요?",
             "expect": ["둘이 무슨 사이"]},
        ],
    },
    "side_taeo": {  # 다정 갈래: 1~2단계 태오 처음 → 4단계 두 번째(도시 밖 의뢰 복선, 서아는 대답 안 함)
        "state": {"stage": 2, "affection": 30, "day": 4, "route": "care", "stage_flags": ["outside"]},
        "history": BASE_HISTORY,
        "steps": [
            {"want_event": "TAEO_1", "user": "오늘은 조용하네.", "expect": ["샌님", "이딴 데서"]},
            {"set": {"stage": 4, "affection": 55}, "want_event": "TAEO_2", "user": "(문 쪽을 본다)",
             "expect": ["도시 밖"], "silent": True},
        ],
    },
    "side_finale_tease": {  # 6단계 첫 아침 → 마리 마무리 → 다음 판정 stayed
        "state": {"stage": 6, "affection": 96, "day": 15, "route": "tease", "mari_step": 2},
        "history": S6_HISTORY,
        "steps": [
            {"last_reply": MORNING_TEASE, "want_morning": True},
            {"want_event": "MARI_3", "user": "마리 왔네.", "expect": ["졌네", "반말해도 돼"]},
            {"want_ending": "stayed"},
        ],
    },
    "side_finale_care": {  # 6단계 첫 아침 → 태오 마무리 → 다음 판정 stayed
        "state": {"stage": 6, "affection": 96, "day": 15, "route": "care", "taeo_step": 2},
        "history": S6_HISTORY,
        "steps": [
            {"last_reply": MORNING_CARE, "want_morning": True},
            {"want_event": "TAEO_3", "user": "(문이 열리는 소리에 고개를 든다)", "expect": ["안 가"]},
            {"want_ending": "stayed"},
        ],
    },
}
# 서버 규칙만 보는 시나리오(판정 모델 안 씀, 무작위 그대로 여러 번): 나오면 안 되는 사건이 나오면 ❌
RULE_SCENARIOS = {
    "stayed_no_side": {  # stayed 뒤 마리·태오 마무리 재등장 없음
        "states": [{"stage": 6, "affection": 98, "route": r, "ending": "stayed", "morning_done": True,
                    "mari_step": 3, "taeo_step": 3} for r in ("tease", "care")],
        "never": ("MARI", "TAEO"), "runs": 40,
    },
    "care_mari_once": {  # 다정 갈래: 마리 3단계 처음 한 번 뒤로 다시 안 나옴
        "first": {"stage": 3, "affection": 40, "route": "care"}, "first_event": "MARI_1",
        "states": [{"stage": s, "affection": a, "route": "care"} for s, a in ((3, 40), (4, 50), (6, 96))],
        "never": ("MARI",), "runs": 40,
    },
}
CHECK_SYSTEM = "너는 롤플레이 대사 검사기다. 질문에 JSON 하나로만 답한다."
CHECK_PROMPTS = {  # 모델에게 묻는 확인 (문자열만으로는 못 가리는 것)
    "approach": ('유저 행동: {user}\n서아 응답: {text}\n\n서아가 유저가 다가오기 전에 먼저 유저에게 다가가거나 '
                 '몸을 기대거나 손을 잡는 등 먼저 신체 접촉·거리 좁히기를 시작했나? '
                 '{{"yes": true 또는 false, "why": "한 문장"}}'),
    "avoid": ('유저 행동: {user}\n서아 응답: {text}\n\n유저가 다가왔을 때 서아가 물러나거나 자리를 피하거나 유저를 밀어냈나? '
              '(말로 받아치는 것은 피한 게 아니다) {{"yes": true 또는 false, "why": "한 문장"}}'),
    "deja": ('서아 응답: {text}\n\n서아가 같은 일이 반복된다, 전에 겪은 것 같다, 기억이 난다, 꿈 같다처럼 '
             '데자뷔나 시간이 되돌아간 걸 아는 듯한 말을 했나? {{"yes": true 또는 false, "why": "한 문장"}}'),
    "invented": ('서아 응답: {text}\n\n서아가 유저를 사람 이름(예: 민준, 지우)으로 불렀나? '
                 '"사장님", "너", "당신"은 이름이 아니다. {{"yes": true 또는 false, "name": "부른 이름 또는 빈 문자열"}}'),
}


def count_bits(text: str | None) -> int:
    """대사 마디(문장) 수. 문장 끝(. ? !)으로 센다. 문장 가운데 「…」(예: 「시끄럽고… 잘 보여서.」)는 끊지 않는다."""
    parts = re.split(r"(?<=[.?!。])\s+", (text or "").strip())
    return len([x for x in parts if x.strip(" .…?!")])


def model_check(llm, kind: str, user: str, text: str) -> dict | None:
    try:
        raw = llm.complete(CHECK_SYSTEM, CHECK_PROMPTS[kind].format(user=user, text=text), json_mode=True)
        data = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
        return data if isinstance(data.get("yes"), bool) else None
    except Exception:
        return None


SCENARIOS.update(ROUTE_SCENARIOS)
SCENARIOS.update({k: dict(v, kind="judge") for k, v in JUDGE_SCENARIOS.items()})
SCENARIOS["departure_taeo"] = {  # 5단계 떠난다는 말에 「태오가 가져온 일이야」 한 줄 (태오 두 번째가 없었어도)
    "state": {"stage": 5, "affection": 76, "day": 11, "route": "tease", "departure_days_left": 3,
              "pending_event": "DEPARTURE", "taeo_step": 1},
    "history": BASE_HISTORY, "turns": ["오늘은 왜 그렇게 조용해?"], "expect_any": ["태오가 가져온 일"],
}
SCENARIOS.update({k: dict(v, kind="side") for k, v in SIDE_SCENARIOS.items()})
SCENARIOS.update({k: dict(v, kind="rule") for k, v in RULE_SCENARIOS.items()})
for _scene, (_gift, _state, _history) in GIFT_SCENES.items():  # 장면 셋 × 기분 맞음/안 맞음
    for _fit in (False, True):
        SCENARIOS[f"gift_{_scene}{'_fit' if _fit else ''}"] = {
            "state": dict(_state), "history": list(_history),
            "turns": [(GIFT_FIT if _fit else GIFT).format(name=_gift)],
            "judge": True, "gift_fit": _fit,
        }


def run_scenarios(which: str, pause: float = 0) -> None:
    from engine import allowed_locations, normalize_state

    llm = make_llm()
    engine = Engine(llm=llm, store=Store(":memory:"),
                    prompt_path=os.getenv("PROMPT_PATH", str(BASE_DIR / "yoon_seoa_prompt.md")))
    names = list(SCENARIOS) if which == "all" else [which]
    lines, table, first = ["# 앱 맞추기 항목 점검", ""], [], True
    for name in names:
        sc = SCENARIOS[name]
        if sc.get("kind") in ("side", "rule"):
            out = (run_side_scenario(engine, sc, pause) if sc["kind"] == "side" else run_rule_scenario(engine, sc))
            print(f"[{name}] " + "\n        ".join(out) + "\n")
            lines += [f"## {name}", ""] + [f"- {x}" for x in out] + [""]
            first = False
            continue
        state = dict(sc["state"])
        history = list(sc.get("history", []))
        stage = state.get("episode_stage") or state["stage"]
        unlocked = [m for k in range(1, state["stage"] + 1) for m in STAGE_UNLOCK[k]]
        motion_list = [{"id": m, "name": MOTIONS[m][0], "emo": MOTIONS[m][1]} for m in unlocked]
        lines += [f"## {name}", ""]
        if sc.get("kind") == "judge":
            out = run_judge_scenario(engine, sc)
            print(f"[{name}] " + "\n        ".join(out) + "\n")
            lines += [f"- {x}" for x in out] + [""]
            continue
        moments_seen = 0
        for user in sc["turns"]:
            if not first and pause > 0:
                time.sleep(pause)
            first = False
            t0 = time.time()
            reply = engine.reply(state, user, history, motion_list)
            sec = time.time() - t0
            text = to_text(reply)
            dialogue = reply["dialogue"]
            split = lambda t: count_bits(t)  # noqa: E731
            sentences, action_n = split(dialogue), split(reply["action"])
            checks = [f"대사 {'❌ ' if sentences > 2 else ''}{len(dialogue)}자·{sentences}마디",
                      f"지문 {'❌ ' if action_n > 2 else ''}{action_n}문장"]
            if dialogue == "…" and reply["action"] is None:
                checks.append("❌ 대체 응답")
            if name in ("selfie", "reentry"):
                checks.append("❌ 메모 따라 씀" if "보냈다. 함께 보낸 말" in text else "메모 안 따라 씀")
            if name == "reentry":
                checks.append("❌ 인사 되풀이" if "기다린 거 아니야" in dialogue else "인사 안 되풀이")
            if sc.get("pose"):
                asked = user.removesuffix(" 해 줄래?")
                want = next((m["id"] for m in motion_list if m["name"] == asked), None)
                checks.append(f"몸짓 {'✅' if reply['motion_id'] == want else '❌'} {reply['motion_id']} (부탁 {want})")
            if state.get("episode"):
                ok = reply["location"] in (None, *allowed_locations(normalize_state(state)))
                checks.append(f"장소 {'✅' if ok else '❌'} {reply['location']}")
            if sc.get("check") == "storm":
                if user.startswith("(소파 쪽으로 다가가"):  # 유저가 다가옴 → 피하면 ❌
                    r = model_check(llm, "avoid", user, text)
                    checks.append("⚠ 피함 확인 실패" if r is None else f"{'❌ 피함' if r['yes'] else '✅ 안 피함'}({r.get('why', '')})")
                else:  # 유저가 다가오지 않음 → 서아가 먼저 다가가면 ❌
                    r = model_check(llm, "approach", user, text)
                    checks.append("⚠ 접근 확인 실패" if r is None else f"{'❌ 서아가 먼저 다가감' if r['yes'] else '✅ 먼저 안 다가감'}({r.get('why', '')})")
                if user.startswith("(젖은 수건"):
                    checks.append(f"장소 {'✅' if reply['location'] == SECOND_FLOOR else '❌'} {reply['location']}")
            if sc.get("check") == "name":
                r = model_check(llm, "invented", user, text)
                checks.append("⚠ 이름 확인 실패" if r is None else (f"❌ 지어낸 이름 {r.get('name')}" if r["yes"] else "✅ 지어낸 이름 없음"))
                if sc["turns"].index(user) == 0:
                    checks.append("이름 물음 ✅" if "이름" in dialogue else "이름 안 물음")
                state = dict(state, pending_event=None)  # 앱처럼 답장 뒤 비움
            elif sc.get("check") == "storm":
                state = dict(state, pending_event=None, location=reply["location"] or state.get("location"))
            if sc.get("judge"):
                before = normalize_state(state)["affection"]
                judged = engine.judge(state, user, text)
                delta = judged["state"]["affection"] - before
                checks.append(f"판정 플래그 {judged['flags']} · 호감 {delta:+d} · 단계 상승 {judged['stage_up']} · 사건 {judged['pending_event']}")
                if sc.get("gift_fit"):  # 기분 맞는 선물은 care 크게
                    big = "care" in judged["flags"] and delta >= 6
                    checks.append(f"{'✅' if big else '❌'} 기분 맞는 선물 → care 크게")
            if "expect_any" in sc and user == sc["turns"][0]:  # 원고 대사는 장면 첫 턴에서만 봄(둘째 턴은 다음 대목)
                hit = any(k in text for k in sc["expect_any"])
                checks.append(f"{'✅' if hit else '⚠ 원고 대사 없음'} {'/'.join(sc['expect_any'])}")
            if "location" in sc and user == sc["turns"][0]:
                ok = reply["location"] == sc["location"]
                checks.append(f"장소 {'✅' if ok else '❌'} {reply['location']} (기대 {sc['location']})")
            if sc.get("check") == "deja":
                r = model_check(llm, "deja", user, text)
                checks.append("⚠ 데자뷔 확인 실패" if r is None else (f"❌ 데자뷔({r.get('why', '')})" if r["yes"] else "✅ 데자뷔 없음"))
            if "moment" in sc:
                gm = reply.get("gift_moment")
                moments_seen += bool(gm)
                if sc["moment"]:
                    checks.append(f"{'✅' if gm and gm['id'] == sc['moment'] else '❌'} 순간 선물 {gm}")
                else:
                    checks.append(f"{'❌ ' if gm and moments_seen >= 2 else ''}순간 선물 {gm} (누적 {moments_seen})")
            state = dict(state, pending_event=None)  # 앱처럼 답장 뒤 비움
            history += [{"r": "u", "t": user}, {"r": "c", "t": text}]
            print(f"[{name}] 유저: {user}\n        서아: ({reply['action']}) {dialogue}\n        "
                  f"{' · '.join(checks)} · {sec:.1f}초\n")
            lines += [f"- 유저: {user}", f"  - 서아: ({reply['action']}) {dialogue}",
                      f"  - 몸짓 {reply['motion_id']} · 감정 {reply['emotion']} · 장소 {reply['location']} · 단계 {stage}",
                      f"  - {' · '.join(checks)} · {sec:.1f}초"]
            table.append((name, user, dialogue, " · ".join(checks)))
        lines.append("")
    (BASE_DIR / "eval_scenarios.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("전문: eval_scenarios.md")


def run_judge_scenario(engine, sc) -> list[str]:
    """판정 흐름: 묶음마다 /judge, 결과를 기대와 비교."""
    state, out = dict(sc["state"]), []
    for i, batch in enumerate(sc["steps"]):
        r = engine.judge(state, batch, "…")
        st, pg = r["state"], r["progress"] or {}
        out.append(f"판정 {i + 1}: 메시지 {len(batch.splitlines())}개 · 플래그 {r['flags']} · 보너스 +{pg.get('talk_bonus')} "
                   f"· 단계 {st['stage']} (상승 {r['stage_up']}) · 남은 날 {st['departure_days_left']} · 사건 {r['pending_event']} "
                   f"· 엔딩 {st['ending']} · 최소 메시지 남음 {pg.get('min_msgs_left')}")
        want = sc["want"]
        if want.startswith("bonus"):
            n = int(want[-1])
            good = pg.get("talk_bonus") == n or (n == 2 and pg.get("talk_bonus") == 1 and not set(r["flags"]) & {"care", "sincere", "comfort", "tease"})
            out.append(f"{'✅' if good else '❌'} 보너스 +{pg.get('talk_bonus')} (기대 +{n}{', 판정 플래그가 없으면 +1' if n == 2 else ''})")
        elif want == "up_on_second":
            if i == 0:
                out.append(f"{'❌ 14개인데 오름' if r['stage_up'] else '✅ 14개면 안 오름'}")
            else:
                out.append(f"{'✅ 15개째 오름' if r['stage_up'] else '❌ 15개인데 안 오름'}"
                           f"{'' if st['affection'] >= 40 else ' (호감이 40 아래로 떨어짐: 판정 탓)'}")
        elif want == "marker":
            if r["pending_event"] and r["pending_event"].startswith("DAYS_LEFT"):
                reply = engine.reply(st, "…왔네.", [{"r": "c", "t": DEPART_LINE}], [])
                out.append(f"✅ 남은 날 지문: {reply['action']}")
            else:
                out.append(f"❌ 하루가 안 넘어감(판정 time_advance {r['judge'].get('time_advance')})")
        elif want in ("departed", "stayed"):
            out.append(f"{'✅' if st['ending'] == want else '❌'} 엔딩 {st['ending']} (기대 {want})")
        state = dict(st, pending_event=None)
    return out


def _prefer_side(pop, weights=None, **kw):
    """무작위 사건 후보에 조연이 있으면 조연을 고른다(차례 순서를 확인하려고)."""
    side = [e for e in pop if e.split("_")[0] in ("MARI", "TAEO")]
    return [side[0] if side else pop[0]]


def run_side_scenario(engine, sc, pause: float = 0) -> list[str]:
    """조연·성공 엔딩: 판정(실제 모델) → 사건이 나오면 답장(실제 모델) → 원고 대사·차례·엔딩 확인."""
    from unittest import mock

    state, history, out = dict(sc["state"]), list(sc.get("history", [])), []
    for i, step in enumerate(sc["steps"]):
        if i and pause > 0:
            time.sleep(pause)
        state.update(step.get("set", {}))
        state.update(short_streak=2, turns_since_event=0, used_events={})  # 다음 판정에서 무작위 사건이 꼭 고려되게
        with mock.patch("engine.random.choices", side_effect=_prefer_side):
            r = engine.judge(state, "응", step.get("last_reply", "…"))
        st = r["state"]
        row = (f"판정 {i + 1}: 사건 {st['pending_event']} · 마리 {st['mari_step']} · 태오 {st['taeo_step']} "
               f"· 첫 아침 {st['morning_done']} · 엔딩 {st['ending']} · 단계 {st['stage']}")
        if "want_event" in step:
            row += f" · {'✅' if st['pending_event'] == step['want_event'] else '❌'} 사건 (기대 {step['want_event']})"
        if step.get("want_morning"):
            row += f" · {'✅' if st['morning_done'] and not st['pending_event'] else '❌'} 첫 아침 뒤 마무리는 다음 판정"
        if "want_ending" in step:
            row += f" · {'✅' if st['ending'] == step['want_ending'] else '❌'} 엔딩 (기대 {step['want_ending']})"
        out.append(row)
        if step.get("user") and st["pending_event"]:
            if pause > 0:
                time.sleep(pause)
            t0 = time.time()
            reply = engine.reply(st, step["user"], history, [])
            text = to_text(reply)
            checks = []
            if reply["dialogue"] == "…" and reply["action"] is None:
                checks.append("❌ 대체 응답")
            hit = [k for k in step.get("expect", []) if k in text]
            checks.append(f"{'✅' if hit else '❌ 원고 대사 없음'} {'/'.join(step.get('expect', []))}")
            if step.get("silent"):
                quiet = reply["dialogue"].strip(" .…") == ""
                checks.append("✅ 서아 대답 안 함" if quiet else f"⚠ 서아가 말함: {reply['dialogue']}")
            out.append(f"  유저: {step['user']}")
            out.append(f"  서아: ({reply['action']}) {reply['dialogue']}")
            out.append(f"  {' · '.join(checks)} · {time.time() - t0:.1f}초")
            history += [{"r": "u", "t": step["user"]}, {"r": "c", "t": text}]
            last = text
        else:
            last = step.get("last_reply", "…")
        state = dict(st, pending_event=None)  # 앱처럼 답장 뒤 비움
        if "want_ending" not in step and i + 1 < len(sc["steps"]) and "last_reply" not in sc["steps"][i + 1]:
            sc["steps"][i + 1] = dict(sc["steps"][i + 1], last_reply=last)
    return out


def run_rule_scenario(engine, sc) -> list[str]:
    """서버 규칙만: 판정 모델 대신 중립 판정으로 여러 번 돌려, 나오면 안 되는 조연이 나오는지 센다."""
    from unittest import mock

    neutral = {"affection_delta": 0, "flags": [], "time_advance": False, "time_of_day": "", "summary": "", "reason": ""}
    out, bad = [], Counter()
    with mock.patch.object(engine, "_judge", return_value=neutral):
        base = {}
        if sc.get("first"):
            with mock.patch("engine.random.choices", side_effect=_prefer_side):
                st = engine.judge(dict(sc["first"], short_streak=2), "응", "…")["state"]
            ok = st["pending_event"] == sc["first_event"]
            out.append(f"{'✅' if ok else '❌'} 처음: 사건 {st['pending_event']} (기대 {sc['first_event']}) · 마리 {st['mari_step']} · 태오 {st['taeo_step']}")
            base = {k: st[k] for k in ("mari_step", "taeo_step")}
        events = Counter()
        for s0 in sc["states"]:
            for _ in range(sc["runs"]):
                st = engine.judge(dict(s0, **base, short_streak=2, used_events={}), "응", "…")["state"]
                ev = st["pending_event"] or "없음"
                events[f"{s0['stage']}단계 {ev}"] += 1
                if ev.split("_")[0] in sc["never"]:
                    bad[ev] += 1
    out.append(f"{'✅ 안 나옴' if not bad else '❌ 나옴 ' + str(dict(bad))} ({' · '.join(f'{k} {v}' for k, v in sorted(events.items()))})")
    return out


def starts(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())[:6]


def ngrams(text: str, n: int = 3) -> set[str]:
    words = re.findall(r"[가-힣A-Za-z0-9]+", text)
    return {" ".join(words[i:i + n]) for i in range(len(words) - n + 1)}


def report(rows, state, stats):
    n = len(rows)
    calls = sum(r["calls"] for r in rows)
    first_fail = sum(not r["first_ok"] for r in rows)
    fallbacks = sum(r["fallback"] for r in rows)
    chosen = [r for r in rows if r["reply"]["motion_id"]]
    invalid_raw = sum(1 for r in rows if r["raw_mid"] and not r["reply"]["motion_id"])
    repeat_motion = sum(r["repeat_motion"] for r in rows)

    dialogues = [r["reply"]["dialogue"] for r in rows]
    actions = [r["reply"]["action"] or "" for r in rows]
    start_counts = Counter(starts(d) for d in dialogues + actions if d)
    rep_starts = {k: v for k, v in start_counts.items() if v > 1}
    grams = Counter()
    for t in dialogues + actions:
        grams.update(ngrams(t))
    rep_grams = [(g, c) for g, c in grams.most_common(10) if c > 1]
    sims = [difflib.SequenceMatcher(None, a, b).ratio() for i, a in enumerate(dialogues) for b in dialogues[i + 1:]]
    near_dup = sum(s >= 0.6 for s in sims)
    adjacent = [difflib.SequenceMatcher(None, dialogues[i], dialogues[i + 1]).ratio() for i in range(n - 1)]

    lines = [
        "# 서아 대화 점검 결과", "",
        "| 항목 | 결과 |", "| --- | --- |",
        f"| 턴 수 | {n} |",
        f"| 대화 호출 수 (재시도 포함) | {calls} |",
        f"| JSON 첫 시도 실패 | {first_fail}/{n} ({first_fail / n:.0%}) |",
        f"| 재시도 후에도 실패 → 대체 응답 | {fallbacks}/{n} |",
        f"| 다른 모델로 넘어간 횟수 | 대화 {stats.get('chat_fallover', 0)}, 판정 {stats.get('judge_fallover', 0)} "
        f"(모든 모델 실패: 대화 {stats.get('chat_unavailable', 0)}, 판정 {stats.get('judge_unavailable', 0)}) |",
        f"| 몸짓을 고른 턴 | {len(chosen)}/{n} |",
        f"| 목록 밖 몸짓을 골라 null 처리 | {invalid_raw} |",
        f"| 같은 몸짓 연속 | {repeat_motion} |",
        f"| 반복된 문장 시작 (앞 6글자) | {', '.join(f'「{k}」×{v}' for k, v in rep_starts.items()) or '없음'} |",
        f"| 2번 이상 나온 3어절 표현 | {', '.join(f'「{g}」×{c}' for g, c in rep_grams) or '없음'} |",
        f"| 대사끼리 유사도 0.6 이상인 쌍 | {near_dup} |",
        f"| 바로 앞 대사와 유사도 (평균/최대) | {sum(adjacent) / max(1, len(adjacent)):.2f} / {max(adjacent, default=0):.2f} |",
        f"| 대화 응답 시간 (평균) | {sum(r['sec'] for r in rows) / n:.1f}초 |",
        f"| 마지막 상태 | 단계 {state['stage']}, 호감도 {state['affection']}, 장소 {state['location']} |",
        "", "## 대화 전문", "",
    ]
    for r in rows:
        rp = r["reply"]
        lines += [f"**{r['turn']}. 유저:** {r['user']}  ",
                  f"*{rp['action'] or ''}* {rp['dialogue']}  ",
                  f"`몸짓 {rp['motion_id']} · 감정 {rp['emotion']} · 장소 {rp['location']} · 플래그 {r['flags']} · "
                  f"단계 {r['stage']} · 호감 {r['aff']}{' · 다음 사건 ' + r['event'] if r['event'] else ''}`", ""]
    out = "\n".join(lines)
    (BASE_DIR / "eval_result.md").write_text(out, encoding="utf-8")
    print("\n".join(lines[:19]))
    print("\n전문: eval_result.md")


if __name__ == "__main__":
    main()
