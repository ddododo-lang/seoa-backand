"""조연(마리·태오)·성공 엔딩 (feat/side-ending) 키 없는 테스트. 실행: python -m unittest discover -s tests -v"""
import random
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import STAYED_MAX_MSGS, normalize_state  # noqa: E402
from test_story_routes import Stub, engine, judge, system_for  # noqa: E402


def fire(state, **verdict):
    """짧은 답 세 번째 → 무작위 사건이 꼭 나오게 해서 판정."""
    st = {"short_streak": 2, "turns_since_event": 0, **state}
    return judge(st, "응", **verdict)["state"]


def candidates(state):
    """무작위 사건 후보와 가중치."""
    seen = {}

    def grab(pop, weights):
        seen["c"] = dict(zip(pop, weights))
        return [pop[0]]
    with mock.patch("engine.random.choices", side_effect=grab):
        fire(state)
    return seen.get("c", {})


class StepNormalizeTest(unittest.TestCase):  # 1
    def test_default_and_range(self):
        self.assertEqual(normalize_state(None)["mari_step"], 0)
        self.assertEqual(normalize_state({"mari_step": 9, "taeo_step": -2}), normalize_state({"mari_step": 3}) | {"taeo_step": 0})
        self.assertEqual(normalize_state({"taeo_step": "x"})["taeo_step"], 0)
        self.assertEqual(normalize_state({"taeo_step": "2"})["taeo_step"], 2)

    def test_old_lovers_ending_becomes_none(self):
        self.assertIsNone(normalize_state({"stage": 6, "ending": "lovers"})["ending"])
        self.assertEqual(normalize_state({"ending": "stayed"})["ending"], "stayed")


class StepRangeTest(unittest.TestCase):  # 2 차례·단계 범위
    def test_before_route_rules(self):
        self.assertIn("TAEO_1", candidates({"stage": 1}))
        self.assertNotIn("MARI_1", candidates({"stage": 1}))
        c = candidates({"stage": 2})
        self.assertIn("MARI_1", c)
        self.assertIn("TAEO_1", c)

    def test_waits_when_out_of_range(self):  # 마리 두 번째는 3~4단계. 2단계에서는 후보에서 빠짐(건너뛰지 않음)
        self.assertFalse({"MARI_1", "MARI_2"} & set(candidates({"stage": 2, "route": "tease", "mari_step": 1})))
        self.assertIn("MARI_2", candidates({"stage": 3, "route": "tease", "mari_step": 1}))
        self.assertIn("MARI_2", candidates({"stage": 4, "route": "tease", "mari_step": 1}))
        self.assertNotIn("TAEO_2", candidates({"stage": 3, "route": "care", "taeo_step": 1}))
        self.assertIn("TAEO_2", candidates({"stage": 4, "route": "care", "taeo_step": 1}))
        self.assertNotIn("TAEO_1", candidates({"stage": 4, "route": "care", "taeo_step": 0}))

    def test_other_route(self):
        # 다정 갈래 마리: 3단계 「처음」 하나, 나오면 닫음
        self.assertNotIn("MARI_1", candidates({"stage": 2, "route": "care"}))
        self.assertIn("MARI_1", candidates({"stage": 3, "route": "care"}))
        with mock.patch("engine.random.choices", return_value=["MARI_1"]):
            st = fire({"stage": 3, "route": "care"})
        self.assertEqual((st["pending_event"], st["mari_step"]), ("MARI_1", 3))
        self.assertNotIn("MARI_2", candidates({"stage": 4, "route": "care", "mari_step": 1}))
        # 장난 갈래 태오: 1~2단계 「처음」만, 두 번째 없음
        self.assertIn("TAEO_1", candidates({"stage": 2, "route": "tease"}))
        self.assertNotIn("TAEO_2", candidates({"stage": 4, "route": "tease", "taeo_step": 1}))

    def test_step_goes_up_and_no_repeat(self):
        with mock.patch("engine.random.choices", return_value=["MARI_2"]):
            st = fire({"stage": 3, "route": "tease", "mari_step": 1})
        self.assertEqual((st["pending_event"], st["mari_step"]), ("MARI_2", 2))
        st["pending_event"] = None  # 앱이 답장 뒤 비움
        st["used_events"] = {}
        self.assertFalse({"MARI_1", "MARI_2", "MARI_3"} & set(candidates(st)))

    def test_scene_blocks(self):
        sys1 = system_for({"stage": 2, "pending_event": "MARI_1"})
        self.assertIn("사장님, 오늘 끝나고 뭐 해요?", sys1)
        self.assertIn("언니, 둘이 무슨 사이예요?", system_for({"stage": 3, "pending_event": "MARI_2"}))
        self.assertIn("큰 건 하나 들어왔다. 도시 밖.", system_for({"stage": 4, "pending_event": "TAEO_2"}))
        self.assertIn("그 의뢰, 안 가.", system_for({"stage": 6, "pending_event": "TAEO_3"}))
        # 예전 값 MARI 는 차례로
        self.assertIn("언니, 둘이 무슨 사이예요?", system_for({"stage": 3, "pending_event": "MARI", "mari_step": 2}))


class DepartureLineTest(unittest.TestCase):  # 3 (S4: 떠나는 소식은 위기 장면의 태오 줄)
    def test_line_without_taeo_second(self):
        st = judge({"stage": 4, "affection": 80, "stage_user_msgs": 20, "taeo_step": 0, "route": "care",
                    "used_events": {"ROUTE_ALLEY": 1}}, "…")["state"]  # S4-1: 그 골목을 마침
        self.assertEqual(st["pending_event"], "CRISIS")
        self.assertIn("태오가 큰 일을 물어 왔어. 도시 밖. …새벽에 떠나.", system_for(st))


class FrequencyTest(unittest.TestCase):  # 4
    def test_route_side_twice(self):
        c = candidates({"stage": 3, "route": "tease", "mari_step": 1})
        self.assertEqual(c["MARI_2"], 2)
        self.assertTrue(all(w == 1 for e, w in c.items() if e != "MARI_2"))
        c = candidates({"stage": 4, "route": "care", "taeo_step": 1})
        self.assertEqual(c["TAEO_2"], 2)
        c = candidates({"stage": 3, "route": "care", "taeo_step": 1})  # 다정 갈래 3단계: 마리 처음은 1배
        self.assertEqual(c["MARI_1"], 1)

    def test_no_side_while_route_scene(self):
        c = candidates({"stage": 3, "route": "tease", "mari_step": 1, "scene": "STORM", "scene_judges_left": 2})
        self.assertFalse(any(e.startswith(("MARI", "TAEO")) for e in c))

    def test_ratio_roughly_two_to_one(self):  # 실제 무작위: 장난 갈래 2단계 마리 처음 : 태오 처음 ≈ 2 : 1
        random.seed(5)
        got = {"MARI_1": 0, "TAEO_1": 0}
        for _ in range(600):
            ev = fire({"stage": 2, "route": "tease", "stage_flags": ["outside"]})["pending_event"]
            if ev in got:
                got[ev] += 1
        self.assertGreater(got["MARI_1"], got["TAEO_1"] * 1.5)


class StayedTest(unittest.TestCase):  # 5
    S6 = {"stage": 6, "affection": 96, "route": "tease", "mari_step": 2}

    def test_morning_then_finale_then_stayed(self):
        eng = engine(Stub())
        st = eng.judge(self.S6, "응", "*서아가 먼저 깨 있다.* 셔츠, 빌렸어. 돌려줄 생각은 없고.")["state"]
        self.assertTrue(st["morning_done"])
        self.assertIsNone(st["pending_event"])  # 첫 아침이 닫힌 다음 판정에서 마무리
        st = eng.judge(st, "응", "…")["state"]
        self.assertEqual((st["pending_event"], st["mari_step"]), ("MARI_3", 3))
        self.assertIn("아, 졌네.", system_for(st))
        self.assertIsNone(st["ending"])
        st["pending_event"] = None  # 앱이 마무리 답장 뒤 비움
        st = eng.judge(st, "응", "…이제 반말해도 돼, 마리.")["state"]
        self.assertEqual(st["ending"], "stayed")
        self.assertEqual(st["route"], "tease")

    def test_care_morning_line(self):
        st = judge({**self.S6, "route": "care", "taeo_step": 2}, "응")["state"]
        self.assertFalse(st["morning_done"])
        st = engine().judge({**self.S6, "route": "care"}, "응", "…해 뜨는 것도, 처음 봐.")["state"]
        self.assertTrue(st["morning_done"])
        st = judge(st, "응")["state"]
        self.assertEqual(st["pending_event"], "TAEO_3")

    def test_no_random_event_before_stayed(self):  # 첫 아침·마무리 사이에 무작위 사건이 끼지 않음
        st = engine(Stub()).judge({**self.S6, "short_streak": 2}, "응", "셔츠, 빌렸어. 돌려줄 생각은 없고.")["state"]
        self.assertIsNone(st["pending_event"])
        st = judge({**self.S6, "short_streak": 2, "stage_user_msgs": STAYED_MAX_MSGS}, "응")["state"]
        self.assertEqual(st["ending"], "stayed")
        self.assertIsNone(st["pending_event"])  # stayed 를 넣은 판정에도 사건 없음

    def test_time_advance_counts_as_morning(self):
        st = judge(self.S6, "응", time_advance=True)["state"]
        self.assertTrue(st["morning_done"])

    def test_morning_block_only_until_morning(self):
        self.assertIn("셔츠, 빌렸어", system_for(self.S6))
        self.assertNotIn("셔츠, 빌렸어", system_for({**self.S6, "morning_done": True}))

    def test_stayed_after_15_msgs_without_finale(self):
        st = judge({**self.S6, "stage_user_msgs": STAYED_MAX_MSGS - 1}, "응")["state"]
        self.assertEqual(st["ending"], "stayed")
        self.assertEqual((st["mari_step"], st["taeo_step"]), (3, 3))
        st = judge({**self.S6, "stage_user_msgs": STAYED_MAX_MSGS - 2}, "응")["state"]
        self.assertIsNone(st["ending"])

    def test_stage_up_to_6_does_not_stay_yet(self):
        st = judge({"stage": 5, "affection": 95, "departure_days_left": 2, "route": "care",
                    "stage_user_msgs": 40}, "가지 마", flags=["pursue"], time_advance=True)["state"]
        self.assertEqual((st["stage"], st["pending_event"]), (6, "STAGE_UP_6"))
        self.assertIsNone(st["ending"])
        self.assertFalse(st["morning_done"])

    def test_after_stayed_no_finale_and_events_go_on(self):
        st = {**self.S6, "ending": "stayed", "morning_done": True, "mari_step": 3, "taeo_step": 3}
        c = candidates(st)
        self.assertTrue(c)
        self.assertFalse(any(e.startswith(("MARI", "TAEO")) for e in c))
        self.assertEqual(judge(st, "응")["state"]["ending"], "stayed")


class XmasTest(unittest.TestCase):  # 6
    def test_no_side_in_episode(self):
        st = fire({"stage": 2, "episode": "s2", "episode_stage": 2, "mari_step": 0, "taeo_step": 0})
        self.assertIsNone(st["pending_event"])
        self.assertEqual((st["mari_step"], st["taeo_step"]), (0, 0))


if __name__ == "__main__":
    unittest.main()
