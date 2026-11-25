"""서아 대화 품질 점검. 실제 모델로 N턴을 돌리고 JSON 오류율, 몸짓 선택, 반복을 표로 보여 준다.

실행 (가상 환경을 켜고, .env에 GEMINI_API_KEY와 LLM_PROVIDER=gemini가 있어야 함):
    python eval_chat.py            # 12턴
    python eval_chat.py --turns 20
    python eval_chat.py --pause 20  # 턴 사이 20초 쉬기 (실제 사람처럼, 한도에 덜 걸림)
    python eval_chat.py --scenario all --pause 10   # 앱 맞추기 항목 점검 (말투·셀카·다시 들어옴·포즈·크리스마스·선물)
    python eval_chat.py --scenario pose             # 하나만
    python eval_chat.py --scenario storm2f --pause 10   # 2층 폭우: 서아가 먼저 다가가면 ❌
    python eval_chat.py --scenario stageup4 --pause 10  # 3→4 단계 상승 다음 턴: 지어낸 이름이면 ❌

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
CHECK_SYSTEM = "너는 롤플레이 대사 검사기다. 질문에 JSON 하나로만 답한다."
CHECK_PROMPTS = {  # 모델에게 묻는 확인 (문자열만으로는 못 가리는 것)
    "approach": ('유저 행동: {user}\n서아 응답: {text}\n\n서아가 유저가 다가오기 전에 먼저 유저에게 다가가거나 '
                 '몸을 기대거나 손을 잡는 등 먼저 신체 접촉·거리 좁히기를 시작했나? '
                 '{{"yes": true 또는 false, "why": "한 문장"}}'),
    "avoid": ('유저 행동: {user}\n서아 응답: {text}\n\n유저가 다가왔을 때 서아가 물러나거나 자리를 피하거나 유저를 밀어냈나? '
              '(말로 받아치는 것은 피한 게 아니다) {{"yes": true 또는 false, "why": "한 문장"}}'),
    "invented": ('서아 응답: {text}\n\n서아가 유저를 사람 이름(예: 민준, 지우)으로 불렀나? '
                 '"사장님", "너", "당신"은 이름이 아니다. {{"yes": true 또는 false, "name": "부른 이름 또는 빈 문자열"}}'),
}


def model_check(llm, kind: str, user: str, text: str) -> dict | None:
    try:
        raw = llm.complete(CHECK_SYSTEM, CHECK_PROMPTS[kind].format(user=user, text=text), json_mode=True)
        data = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
        return data if isinstance(data.get("yes"), bool) else None
    except Exception:
        return None


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
        state = dict(sc["state"])
        history = list(sc["history"])
        stage = state.get("episode_stage") or state["stage"]
        unlocked = [m for k in range(1, state["stage"] + 1) for m in STAGE_UNLOCK[k]]
        motion_list = [{"id": m, "name": MOTIONS[m][0], "emo": MOTIONS[m][1]} for m in unlocked]
        lines += [f"## {name}", ""]
        for user in sc["turns"]:
            if not first and pause > 0:
                time.sleep(pause)
            first = False
            t0 = time.time()
            reply = engine.reply(state, user, history, motion_list)
            sec = time.time() - t0
            text = to_text(reply)
            dialogue = reply["dialogue"]
            split = lambda t: len([x for x in re.split(r"[.?!…。]+\s*", t or "") if x.strip()])  # noqa: E731
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
