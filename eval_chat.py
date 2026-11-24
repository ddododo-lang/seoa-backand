"""서아 대화 품질 점검. 실제 모델로 N턴을 돌리고 JSON 오류율, 몸짓 선택, 반복을 표로 보여 준다.

실행 (가상 환경을 켜고, .env에 GEMINI_API_KEY와 LLM_PROVIDER=gemini가 있어야 함):
    python eval_chat.py            # 12턴
    python eval_chat.py --turns 20
    python eval_chat.py --pause 20  # 턴 사이 20초 쉬기 (실제 사람처럼, 한도에 덜 걸림)

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
    2: ["m09", "c03"], 3: ["m05", "m06", "c01", "c04"], 4: ["c05"], 5: ["m10", "m13"], 6: ["m15"],
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
    args = ap.parse_args()

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
