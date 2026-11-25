"""앱 맞추기(feat/app-sync) 키 없이 도는 테스트. 실행: python -m unittest discover -s tests -v"""
import json
import sys
import unittest
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from db import Store  # noqa: E402
from engine import Engine, normalize_state  # noqa: E402
from llm import FakeLLM  # noqa: E402

PROMPT = str(BASE_DIR / "yoon_seoa_prompt.md")
MOTIONS = [{"id": "m20", "name": "도발하기", "emo": "장난"}, {"id": "c02", "name": "머리 쓸어 넘기기", "emo": "도발"}]
XMAS = {"stage": 3, "affection": 45, "episode": "s2", "episode_stage": 2,
        "episode_stage_name": "티키타카", "location": "서아의 자취방"}


class SpyLLM(FakeLLM):
    """서버가 모델에 넘긴 system·messages를 기록하고, 정해 둔 JSON 답을 돌려준다."""

    def __init__(self, answer=None):
        super().__init__()
        self.answer, self.calls = answer, []

    def chat_json(self, system, messages, schema, budget_sec=None):
        self.calls.append({"system": system, "messages": messages})
        return json.dumps(self.answer, ensure_ascii=False) if self.answer else super().chat_json(system, messages, schema)


def engine(llm=None):
    return Engine(llm=llm or SpyLLM(), store=Store(":memory:"), prompt_path=PROMPT)


class ToneTest(unittest.TestCase):  # 6
    def test_rules_in_prompt(self):
        system = engine()._build_system(normalize_state({}), None, MOTIONS)
        self.assertIn("설명하지 않는다. 이유·사정을 덧붙이지 않는다.", system)
        self.assertIn("dialogue는 서아의 대사 한두 마디", system)
        self.assertNotIn("대사 1~3문장", system)


class HistoryLinesTest(unittest.TestCase):  # 7
    def test_consecutive_seoa_lines_are_merged(self):
        llm = SpyLLM()
        history = [{"r": "u", "t": "내일 또 올게"}, {"r": "c", "t": "그래. 조심히 가."},
                   {"r": "c", "t": '(서아가 바 옥상에서 찍은 사진을 보냈다. 함께 보낸 말: "또 혼자 옥상.")'},
                   {"r": "c", "t": "기다린 거 아니야."}]
        engine(llm).reply({"stage": 6, "day": 9}, "오래 기다렸어?", history, MOTIONS)
        msgs = llm.calls[0]["messages"]
        roles = [m["role"] for m in msgs]
        self.assertTrue(all(a != b for a, b in zip(roles, roles[1:])), roles)
        self.assertTrue(msgs[-2]["content"].endswith("기다린 거 아니야."))
        self.assertIn("또 혼자 옥상.", msgs[-2]["content"])

    def test_pose_request_forces_that_motion(self):
        llm = SpyLLM({"action": "머리를 넘긴다.", "dialogue": "…한 번만이야.", "motion_id": "m20",
                      "emotion": "도발", "location": None})
        out = engine(llm).reply({}, "머리 쓸어 넘기기 해 줄래?", [], MOTIONS)
        self.assertEqual(out["motion_id"], "c02")

    def test_unknown_pose_name_keeps_model_choice(self):
        llm = SpyLLM({"action": "웃는다.", "dialogue": "뭐?", "motion_id": "m20", "emotion": "장난", "location": None})
        out = engine(llm).reply({}, "물구나무 해 줄래?", [], MOTIONS)
        self.assertEqual(out["motion_id"], "m20")

    def test_rules_in_prompt(self):
        system = engine()._build_system(normalize_state({}), None, MOTIONS)
        for text in ("사진과 말을 보낸 것이다", "가장 마지막 서아 말에 이어서", "해 줄래?"):
            self.assertIn(text, system)


class FirstNightTest(unittest.TestCase):  # 8
    LINE = "오늘 밤 골목에서 괴한에게 쫓기다 싸워서 손등에 피가 묻은 채 바에 왔다"

    def test_only_on_day_1(self):
        e = engine()
        self.assertIn(self.LINE, e._build_system(normalize_state({"day": 1}), None, MOTIONS))
        self.assertNotIn(self.LINE, e._build_system(normalize_state({"day": 2}), None, MOTIONS))


class XmasEpisodeTest(unittest.TestCase):  # 9
    def test_state_keeps_episode_values_and_locations(self):
        st = normalize_state(XMAS)
        self.assertEqual((st["episode"], st["episode_stage"], st["location"]), ("s2", 2, "서아의 자취방"))
        self.assertEqual(normalize_state({"location": "서아의 자취방"})["location"], "바 '라스트 오더' 1층")
        self.assertIsNone(normalize_state({"episode": "s9"})["episode"])

    def test_block_replaces_stage_and_events(self):
        st = normalize_state(dict(XMAS, pending_event="ROOFTOP"))
        system = engine()._build_system(st, st["pending_event"], MOTIONS)
        self.assertIn("크리스마스 이브 정전", system)
        self.assertIn("이 밤의 단계(2 티키타카)", system)
        self.assertNotIn("단계 지침 — 3", system)
        self.assertNotIn("사건 지시", system)
        self.assertNotIn("괴한에게 쫓기다", system)

    def test_no_bar_opening_and_episode_location_allowed(self):
        llm = SpyLLM({"action": "촛불이 흔들린다.", "dialogue": "…들어와.", "motion_id": None,
                      "emotion": "설렘", "location": "서아의 자취방"})
        out = engine(llm).reply(XMAS, "(문을 두드린다)", [{"r": "c", "t": "*서아가 문을 연다.*"}], MOTIONS)
        self.assertEqual(out["location"], "서아의 자취방")
        joined = " ".join(m["content"] for m in llm.calls[0]["messages"])
        self.assertNotIn("문 닫을 시간인데", joined)  # 바 오프닝 없음

    def test_judge_skips_stage_up_and_events(self):
        st = dict(XMAS, stage=1, affection=9, stage_flags=["care"], short_streak=5, turns_since_event=30)
        result = engine().judge(st, "(손을 잡는다)", "…뭐 해.")
        self.assertFalse(result["stage_up"])
        self.assertIsNone(result["pending_event"])
        self.assertEqual(result["state"]["stage"], 1)

    def test_main_story_unchanged(self):
        result = engine().judge({"stage": 1, "affection": 9, "stage_flags": ["care"]}, "괜찮아? 이리 줘 봐", "…")
        self.assertIn(result["state"]["stage"], (1, 2))  # 본 이야기는 예전 규칙대로 계산


class GiftTest(unittest.TestCase):  # 13 (앱 49·50)
    def test_rule_in_chat_and_episode_prompts(self):
        e = engine()
        for st in ({}, XMAS):
            system = e._build_system(normalize_state(st), None, MOTIONS)
            self.assertIn("[유저가 선물함: {이름}]", system)
            self.assertIn("미리 정한 반응 없이 지금 장면에 맞게 받는다", system)

    def test_judge_guidance(self):
        judge = e = engine().blocks["JUDGE_SYSTEM"]
        self.assertIn("「서아 기분에 맞는 선물」 또는 「순간 선물」이 붙으면: flags에 \"care\", affection_delta +6 ~ +10", judge)
        self.assertIn("「또」가 붙으면", e)
        self.assertIn("affection_delta +1 ~ +4, care는 넣지 않는다", e)

    def test_examples_follow_length_rule(self):
        import re
        base = engine().blocks["MAIN_WORLD"]  # S1: [예시]는 본편 블록으로 옮김
        for d in re.findall(r'"dialogue": "([^"]+)"', base):
            self.assertLessEqual(len([x for x in re.split(r"[.?!…]+\s*", d) if x.strip()]), 2, d)


class HoldTest(unittest.TestCase):  # 10·11
    def test_rooftop_dance_and_m15_removed(self):
        text = Path(PROMPT).read_text(encoding="utf-8")
        self.assertNotIn("춤을 춘다", text)
        self.assertNotIn("| EVENT_ROOFTOP | m23", text)
        self.assertNotIn("| 6단계 | m15", text)


if __name__ == "__main__":
    unittest.main()
