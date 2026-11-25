"""S3 대화 품질 키 없는 테스트. 실행: python -m unittest discover -s tests -v"""
import json
import sys
import unittest
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import engine as engine_mod  # noqa: E402
from db import Store  # noqa: E402
from engine import Engine, normalize_state  # noqa: E402
from llm import FakeLLM  # noqa: E402

PROMPT = str(BASE_DIR / "yoon_seoa_prompt.md")
MOTIONS = [{"id": "m20", "name": "도발하기", "emo": "장난"}]
XMAS = {"stage": 3, "affection": 45, "episode": "s2", "episode_stage": 2,
        "episode_stage_name": "티키타카", "location": "서아의 자취방"}

OLD_LINES = [
    "action은 서아의 행동·장면 묘사 1~2문장(짧게)",
    "dialogue는 서아의 대사 한두 마디",
    "유저가 바로 반응할 수 있는 지점(질문, 행동, 선택)에서 끝낸다.",
    "action은 이번 턴에 고른 몸짓(motion_id)과 같은 장면이어야 한다.",
    "감각 디테일을 하나 넣는다",
    "매 응답은 새로운 것 하나(정보, 질문, 행동, 장소나 분위기의 변화)를 더한다.",
    "설명하지 않는다. 이유·사정을 덧붙이지 않는다.",
    "대사는 많아야 두 마디.",
    "지문도 짧게. 한 문장이 기본이고 많아야 두 문장.",
    "「방금 찍었어. 옆에 있어도 보내는 거야. …기록용.」",
    "지문 한 문장, 대사 한두 마디",
    "서아의 행동·장면 묘사 1~2문장, 짧게 (별표 없이)",
    "이 목록에서 장면에 가장 맞는 몸짓 하나를 골라",
]
COMMON_SECTIONS = ["# 대화를 이끄는 법", "# 지문", "# 단계별 말투와 스킨십"]
NEW_RULES = [
    "2. 응답은 OUTPUT_FORMAT의 JSON 하나로만 한다. action은 지문(서아의 행동·표정·속마음), dialogue는 서아의 대사.",
    "4. 외모는 화면에 보이므로 길게 묘사하지 않는다.",
    "대사는 1~3문장. 짧고 툭 던지는 말투는 그대로지만, 단답 한 마디로만 끝내지 않는다.",
    "「뭐 할래?」「알아서 해」처럼 결정을 유저에게 넘기지 않는다.",
    "닫는 말만으로 답장을 끝내지 않는다.",
    "질문은 세 번에 한 번 정도.",
    "지문은 1~3문장: 행동 하나 + 표정 하나 + 속마음 한 조각.",
    "칼라 만지기는 진심을 말하는 순간에만.",
    "지문에서 유저는 언제나 「당신」이다.",
    "| 3 썸 | 놀리다가 말끝이 흐려진다.",
    "받아들이는 범위 안의 스킨십은 피하지 않는다.",
    '"action": "지문 1~3문장(행동·표정·속마음, 별표 없이)"',
    '"dialogue": "서아의 대사 1~3문장(따옴표 없이)"',
]
MOTION_LINES = [
    "- 지문의 표정·감정과 맞는 몸짓 하나를 고른다. 찌푸림·짜증·슬픔·긴장·당황 장면에 웃는 몸짓을 고르지 않는다.",
    "- 웃음 몸짓은 지문에서 실제로 웃을 때만, 「자신감 있게 설득하기」는 무언가를 자신만만하게 주장하거나 설득할 때만.",
    "- 맞는 것이 없으면 null(앱이 기본 대기 동작을 재생). 몸짓에 맞추려고 없던 행동을 지어내지 않는다.",
    "- 목록에 없는 id는 쓰지 않는다.",
]
PLACE_RULES = [
    "# 장소와 일관성",
    "서아가 유저를 그곳으로 데려가거나 안내하지 않는다.",
    "한 답장 안에서 장소가 두 번 바뀌지 않는다.",
    "지금 장면에 없던 물건(이불, 담요, 우산, 소파 등)을 갑자기 쓰지 않는다.",
    "유저의 고백, 함께 한 일을 부정하지 않는다.",
]


class SpyLLM(FakeLLM):
    """대화 호출을 남기고 정해 둔 답장을 돌려줌."""

    def __init__(self):
        super().__init__()
        self.calls = []

    def chat_json(self, system, messages, schema, budget_sec=None):
        self.calls.append({"system": system, "messages": messages})
        return json.dumps({"action": "x", "dialogue": "응.", "motion_id": None, "emotion": "평온",
                           "location": None}, ensure_ascii=False)


def engine(llm=None):
    return Engine(llm=llm or FakeLLM(), store=Store(":memory:"), prompt_path=PROMPT)


def system(state):
    st = normalize_state(state)
    return engine()._build_system(st, st["pending_event"], MOTIONS)


class DialogueRulesTest(unittest.TestCase):  # A9
    def test_old_lines_gone(self):
        for st in ({"stage": 2}, XMAS):
            s = system(st)
            for line in OLD_LINES:
                self.assertNotIn(line, s, line)

    def test_main_has_all_new_sections(self):
        for stage in (1, 2, 3, 4, 5, 6):
            s = system({"stage": stage})
            for text in COMMON_SECTIONS + NEW_RULES + MOTION_LINES + PLACE_RULES:
                self.assertIn(text, s, f"{stage}: {text}")

    def test_xmas_has_common_but_not_place(self):
        s = system(XMAS)
        for text in COMMON_SECTIONS + NEW_RULES + MOTION_LINES:
            self.assertIn(text, s, text)
        for text in PLACE_RULES:
            self.assertNotIn(text, s, text)

    def test_sections_live_in_right_blocks(self):
        e = engine()
        for name in COMMON_SECTIONS:
            self.assertIn(name, e.blocks["BASE_SYSTEM"])
        self.assertIn("# 장소와 일관성", e.blocks["MAIN_WORLD"])
        self.assertNotIn("# 장소와 일관성", e.blocks["BASE_SYSTEM"])
        self.assertTrue(e.blocks["MOTION_BLOCK"].rstrip().endswith("\n".join(MOTION_LINES)))

    def test_kept_lines(self):
        s = system({"stage": 4})
        self.assertIn("아래 [말투]의 대표 대사와 [예시]의 문장은 말투 참고용이며 그대로 쓰지 않는다.", s)
        self.assertIn("키스는 하지 않는다.", s)  # STAGE_4 그대로
        self.assertIn("기본 반말. 짧고 나른한 문장.", s)


class JudgeStub(FakeLLM):
    """판정 JSON을 차례로 돌려주고, 받은 판정 요청을 남기는 LLM."""

    def __init__(self, *verdicts):
        super().__init__()
        self.verdicts, self.prompts = list(verdicts), []

    def complete(self, system, prompt, json_mode=False, budget_sec=None):
        if not json_mode:
            return super().complete(system, prompt)
        self.prompts.append(prompt)
        v = self.verdicts.pop(0) if len(self.verdicts) > 1 else self.verdicts[0]
        return json.dumps({"affection_delta": 0, "flags": [], **v}, ensure_ascii=False)


class MemoryTest(unittest.TestCase):  # B5
    def test_history_80(self):
        self.assertEqual(engine_mod.HISTORY_MESSAGES, 80)
        llm = SpyLLM()
        history = [{"r": "u" if i % 2 == 0 else "c", "t": f"줄{i}"} for i in range(100)]
        engine(llm).reply({"stage": 2, "day": 3}, "지금", history, MOTIONS)
        sent = "\n".join(m["content"] for m in llm.calls[0]["messages"])
        self.assertIn("줄20\n", sent + "\n")
        self.assertNotIn("줄19\n", sent + "\n")

    def test_stage_note_requested_after_stage_up_and_saved(self):
        llm = JudgeStub({"affection_delta": 5, "flags": ["care"]},
                        {"stage_note": "서아가 손등 상처를 맡겼다."})
        e = engine(llm)
        r = e.judge({"stage": 1, "affection": 9}, "치료해 줄게", "…알아서 해.")
        st = r["state"]
        self.assertEqual((st["stage"], st["stage_note_due"]), (2, 1))
        self.assertNotIn("[단계 기록 요청]", llm.prompts[0])
        r = e.judge(st, "또 와", "…봐서.")
        self.assertIn("[단계 기록 요청] 1단계(경계)를 한 문장으로", llm.prompts[1])
        st = r["state"]
        self.assertEqual(st["stage_notes"], {"1": "서아가 손등 상처를 맡겼다."})
        self.assertIsNone(st["stage_note_due"])
        self.assertIn("- 지난 단계 기록:\n  - 1단계(경계): 서아가 손등 상처를 맡겼다.", system(st))
        st["summary"] = "새 요약"  # 요약이 바뀌어도 지우지 않음
        self.assertEqual(normalize_state(st)["stage_notes"]["1"], "서아가 손등 상처를 맡겼다.")

    def test_note_without_request_is_ignored_and_overwrite(self):
        llm = JudgeStub({"stage_note": "요청 없는 기록"})
        r = engine(llm).judge({"stage": 2, "affection": 20}, "안녕", "…응.")
        self.assertEqual(r["state"]["stage_notes"], {})
        self.assertEqual(r["judge"]["stage_note"], "")
        llm = JudgeStub({"stage_note": "새 기록"})
        r = engine(llm).judge({"stage": 3, "affection": 45, "stage_note_due": 2,
                               "stage_notes": {"2": "옛 기록"}}, "응", "…그래.")
        self.assertEqual(r["state"]["stage_notes"], {"2": "새 기록"})

    def test_empty_blocks_say_none(self):
        s = system({"stage": 2})
        self.assertIn("- 지난 단계 기록:\n  - 없음", s)
        self.assertIn("- 서아가 한 말·약속 (부정하지 말 것):\n  - 없음", s)

    def test_promises_keep_last_five(self):
        st = {"stage": 2, "affection": 20}
        for i in range(7):
            st = engine(JudgeStub({"promise": f"약속{i}"})).judge(st, "응", "…그래.")["state"]
        self.assertEqual(st["promises"], [f"약속{i}" for i in range(2, 7)])
        s = system(st)
        self.assertIn("- 서아가 한 말·약속 (부정하지 말 것):\n  - 약속2\n", s)
        self.assertIn("  - 약속6", s)
        self.assertNotIn("약속1", s)

    def test_xmas_has_no_memory_lines(self):
        st = dict(XMAS, stage_notes={"1": "기록"}, promises=["약속"], stage_note_due=2)
        s = system(st)
        self.assertNotIn("지난 단계 기록", s)
        self.assertNotIn("서아가 한 말·약속", s)
        llm = JudgeStub({"promise": "새 약속", "stage_note": "x"})
        r = engine(llm).judge(st, "응", "…그래.")
        self.assertNotIn("[단계 기록 요청]", llm.prompts[0])
        self.assertEqual(r["state"]["promises"], ["약속"])

    def test_judge_system_fields(self):
        judge_system = engine().blocks["JUDGE_SYSTEM"]
        self.assertIn("서아가 직접 한 제안·약속·발언과 유저의 고백·약속을 가장 먼저 남긴다.", judge_system)
        self.assertIn('"stage_note": "", "promise": ""', judge_system)



class JudgeBalanceTest(unittest.TestCase):  # C4
    def test_new_criteria(self):
        j = engine().blocks["JUDGE_SYSTEM"]
        for text in (
            "- +1 ~ +4: 가벼운 다정함, 재치 있는 받아치기, 농담, 서아의 말에 성의 있게 반응함. 짧은 메시지도 상황에 맞으면 같게 본다.",
            "- 0: 인사, 성의 없는 맞장구, 스토리와 무관한 대화",
            '- "care": 실제로 챙기는 행동이나 말(치료, 물·담요·음식 챙기기, 취향 기억, 몸 상태 걱정). 그냥 친절한 말투는 care 가 아니다.',
            '- "tease": 서아의 도발을 맞받아치기, 거꾸로 놀리기, 능청, 농담, 밀당하는 말. 다정함과 장난이 같이 있으면 둘 다 넣는다.',
        ):
            self.assertIn(text, j)
        for old in ("대화를 이어감", "중립적이거나 스토리와 무관한 대화", '"care": 상처 치료, 챙김, 취향 기억',
                    "재치 있게 맞받아치거나 거꾸로 서아를 놀림"):
            self.assertNotIn(old, j)

    def test_talk_bonus_always_zero(self):
        long_msgs = ["오늘은 일부러 일찍 문 열었어. 네가 비 맞고 올까 봐 수건도 데워 놨고, 늘 마시던 거 미리 준비해 뒀어."] * 3
        self.assertEqual(Engine._talk_bonus(long_msgs, {"affection_delta": 5, "flags": ["care", "tease"]}), 0)
        r = engine(JudgeStub({"affection_delta": 3, "flags": ["care"]})).judge(
            {"stage": 2, "affection": 20}, "\n".join(long_msgs), "…그래.")
        self.assertEqual((r["judge"]["talk_bonus"], r["progress"]["talk_bonus"], r["state"]["stage_bonus"]), (0, 0, 0))
        self.assertEqual(r["state"]["affection"], 23)
        self.assertIn("stage_bonus_max", r["progress"])  # 앱 호환: 필드는 그대로


if __name__ == "__main__":
    unittest.main()
