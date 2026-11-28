"""S9: 지난번 밤에서 지난 날(night_gap). 키 없이 돈다.
실행: python -m unittest discover -s tests -v"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import normalize_state  # noqa: E402
from test_story_routes import judge, system_for  # noqa: E402

ONE = "- 지난번 밤에서: 하루가 지났다. 지난번 밤의 일은 「어제」로 말한다."
FEW = "- 지난번 밤에서: 며칠이 지났다. 지난번 밤의 일은 「어제」「하루」가 아니라 「며칠 전」「그날」로 말한다."
DAY_LINE = "- 일차: "


def next_night(**state):
    return judge(normalize_state(state), "[다음 밤]")["state"]


class NightGapTest(unittest.TestCase):
    def test_first_night_has_no_line(self):
        st = normalize_state({})
        self.assertIsNone(st["night_gap"])
        system = system_for(st)
        self.assertIn(DAY_LINE, system)
        self.assertNotIn("지난번 밤에서", system)
        self.assertNotIn("{{night_gap}}", system)

    def test_day2_is_one_day(self):
        st = next_night(stage=2, affection=20, night_next="NIGHT_START_2")
        self.assertEqual((st["day"], st["night_gap"]), (2, "하루"))
        system = system_for(st)
        self.assertIn(ONE, system)
        self.assertNotIn(FEW, system)
        lines = system.split("\n")
        i = next(k for k, x in enumerate(lines) if x.startswith(DAY_LINE))
        self.assertEqual(lines[i + 1], ONE)  # 「일차」 줄 바로 아래

    def test_few_days(self):
        for route in ("tease", "care"):
            st = next_night(stage=3, affection=35, route=route, day=2, night_gap="하루",
                            night_next=f"NIGHT_START_3_{route.upper()}")
            self.assertEqual((st["day"], st["night_gap"]), (5, "며칠"), route)
            self.assertIn(FEW, system_for(st))
            self.assertNotIn(ONE, system_for(st))
        st = next_night(stage=5, affection=60, route="care", day=5, night_gap="며칠", crisis="rift",
                        night_next="RIFT_NIGHT")
        self.assertEqual((st["day"], st["night_gap"]), (8, "며칠"))
        self.assertIn(FEW, system_for(st))

    def test_kept_until_next_night(self):
        st = next_night(stage=2, affection=20, night_next="NIGHT_START_2")
        for msg in ("응", "그래서?", "오늘은 뭐 마실래?"):
            st = judge(st, msg)["state"]
            self.assertEqual(st["night_gap"], "하루", msg)
        st = normalize_state(st)  # 앱이 받은 state 를 그대로 돌려보냄
        self.assertEqual(st["night_gap"], "하루")
        self.assertIn(ONE, system_for(st))

    def test_normalize(self):
        self.assertIsNone(normalize_state({"night_gap": "이틀"})["night_gap"])
        self.assertIsNone(normalize_state({"night_gap": 3})["night_gap"])
        self.assertEqual(normalize_state({"night_gap": "며칠"})["night_gap"], "며칠")
        self.assertIsNone(normalize_state({"night_gap": None})["night_gap"])  # 되감기로 첫날 state 가 와도 줄 없음
        self.assertNotIn("지난번 밤에서", system_for({"night_gap": None}))

    def test_christmas_has_no_line(self):
        for gap in ("하루", "며칠"):
            system = system_for({"episode": "s2", "episode_stage": 2, "night_gap": gap, "day": 5})
            self.assertNotIn("지난번 밤에서", system)


if __name__ == "__main__":
    unittest.main()
