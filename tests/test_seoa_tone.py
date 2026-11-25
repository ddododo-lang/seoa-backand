"""서아 말투 PR(feat/seoa-tone) 키 없는 테스트. 실행: python -m unittest discover -s tests -v"""
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
SECOND_FLOOR = "바 '라스트 오더' 2층"


class JudgeStub(FakeLLM):
    """판정 JSON을 정해 두고 돌려주는 LLM (단계 상승을 확실히 일으키려고)."""

    def __init__(self, verdict):
        super().__init__()
        self.verdict = verdict

    def complete(self, system, prompt, json_mode=False, budget_sec=None):
        return json.dumps(self.verdict, ensure_ascii=False) if json_mode else super().complete(system, prompt)


def engine(llm=None):
    return Engine(llm=llm or FakeLLM(), store=Store(":memory:"), prompt_path=PROMPT)


def system_for(state, e=None):
    st = normalize_state(state)
    return (e or engine())._build_system(st, st["pending_event"], [])


class SecondFloorTest(unittest.TestCase):  # 1
    def test_rule_in_base_system(self):
        base = engine().blocks["BASE_SYSTEM"]
        self.assertIn("2층(사장님 집)에서는 서아가 먼저 다가가지 않는다", base)
        self.assertIn("크리스마스의 자취방은 반대로 서아가 먼저 다가간다", base)
        self.assertIn("단, 바 '라스트 오더' 2층에서는 피하지 않는다", base)  # 규칙 13 「피한다」와 충돌하지 않게
        self.assertIn("물러나거나 밀어내지 않고", base)


class StormTest(unittest.TestCase):  # 2
    def test_storm_no_longer_provokes_first(self):
        storm = engine().blocks["EVENT_STORM"]
        self.assertNotIn("서아가 먼저 도발한다", storm)
        for text in ("먼저 다가가지 않는다", "소파에서 잘게. …쳐다보지 마.", "받아치되 피하지 않는다", SECOND_FLOOR):
            self.assertIn(text, storm)

    def test_storm_block_goes_in_with_pending_event(self):
        self.assertIn("소파에서 잘게", system_for({"stage": 3, "pending_event": "STORM"}))


class StormLocationTest(unittest.TestCase):  # 2 (eval: 모델이 location 을 빠뜨려 앱 배경이 1층에 남음)
    def reply(self, location, pending="STORM"):
        llm = JudgeStub({})
        llm.chat_json = lambda *a, **k: json.dumps({"action": "수건을 받는다.", "dialogue": "…소파에서 잘게.",
                                                    "motion_id": None, "emotion": "평온", "location": location},
                                                   ensure_ascii=False)
        return engine(llm).reply({"stage": 3, "pending_event": pending}, "(수건을 건넨다)", [], [])

    def test_storm_reply_moves_to_second_floor(self):
        self.assertEqual(self.reply(None)["location"], SECOND_FLOOR)

    def test_model_location_kept_and_other_events_untouched(self):
        self.assertEqual(self.reply("바 옥상")["location"], "바 옥상")
        self.assertIsNone(self.reply(None, pending="MARI")["location"])


class StageUpSceneTest(unittest.TestCase):  # 3
    LINES = {2: "…생각보다 괜찮네. 사장님 손.", 3: "알아. 문 닫은 거. …한 잔만 더.",
             4: "…너 이름, 아직 안 물어봤네.", 6: "매일 온 거, 술 때문 아니었어."}

    def test_advance_sets_scene_once(self):
        for n, line in self.LINES.items():
            st = normalize_state({"stage": n - 1})
            Engine._advance(st, n, [])
            self.assertEqual(st["pending_event"], f"STAGE_UP_{n}")
            self.assertIn(line, system_for(st))
            self.assertIn("# 단계가 막 올랐다", system_for(st))
            st["pending_event"] = None  # 앱이 답장 뒤 비움 (6단계 대사는 STAGE_6 지침에도 원래 있어서 블록 제목으로 확인)
            self.assertNotIn("# 단계가 막 올랐다", system_for(st))

    def test_4_to_5_keeps_departure(self):
        st = normalize_state({"stage": 4})
        Engine._advance(st, 5, [])
        self.assertEqual(st["pending_event"], "DEPARTURE")

    def test_judge_returns_scene_and_it_survives_normalize(self):
        e = engine(JudgeStub({"affection_delta": 3, "flags": ["sincere"], "time_advance": False,
                              "time_of_day": "", "summary": "", "reason": "진심"}))
        result = e.judge({"stage": 3, "affection": 58, "stage_flags": []}, "너한테 거짓말 안 해.", "…")
        self.assertTrue(result["stage_up"])
        self.assertEqual(result["pending_event"], "STAGE_UP_4")
        self.assertEqual(normalize_state(result["state"])["pending_event"], "STAGE_UP_4")  # 앱이 돌려보내도 남음

    def test_no_scene_without_stage_up_or_in_episode(self):
        e = engine(JudgeStub({"affection_delta": 3, "flags": ["sincere"], "time_advance": False,
                              "time_of_day": "", "summary": "", "reason": "진심"}))
        ep = e.judge({"stage": 3, "affection": 58, "episode": "s2", "episode_stage": 3,
                      "episode_stage_name": "썸", "location": "서아의 자취방"}, "…", "…")
        self.assertFalse(ep["stage_up"])
        self.assertFalse(str(ep["pending_event"] or "").startswith("STAGE_UP_"))


class UserNameTest(unittest.TestCase):  # 4
    def test_rules(self):
        e = engine()
        self.assertIn("유저의 이름은 지어내지 않는다", e.blocks["BASE_SYSTEM"])
        self.assertNotIn("처음으로 유저의 이름을 부른다 (진지한 순간에만)", e.blocks["STAGE_4"])
        self.assertIn("모르면 이름을 지어내지 않고, 이름을 묻는 쪽으로 간다", e.blocks["STAGE_4"])
        self.assertIn("「유저 이름: ○○」을 꼭 남긴다", e.blocks["JUDGE_SYSTEM"])

    def test_session_summarizer_keeps_name(self):
        from engine import SUMMARY_SYSTEM
        self.assertIn("「유저 이름: ○○」을 꼭 남긴다", SUMMARY_SYSTEM)


if __name__ == "__main__":
    unittest.main()
