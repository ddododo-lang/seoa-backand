"""단계 상승 규칙 (키 없이). 실행: python -m unittest discover -s tests -v"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import Engine, new_state, normalize_state  # noqa: E402


def state(stage=1, affection=0, flags=(), msgs=99):
    s = new_state()
    s.update(stage=stage, affection=affection, stage_flags=list(flags), stage_user_msgs=msgs)
    return s


class Stage1To2Test(unittest.TestCase):
    """1→2단계: 호감도 10 이상 + care 또는 protect (앱과 같은 기준)."""

    def test_affection_10_with_care_goes_up(self):
        self.assertEqual(Engine._next_stage(state(affection=10, flags=["care"])), 2)

    def test_affection_10_with_protect_goes_up(self):
        self.assertEqual(Engine._next_stage(state(affection=10, flags=["protect"])), 2)

    def test_affection_9_stays(self):
        self.assertIsNone(Engine._next_stage(state(affection=9, flags=["care"])))

    def test_flag_still_required(self):
        self.assertIsNone(Engine._next_stage(state(affection=25, flags=["tease"])))

    def test_min_messages_gate(self):  # S4: 1→2 3개 · 2→3 9개 (S4-1: 2→3 은 갈래 장면을 마쳐야)
        done = {"route": "care", "used_events": {"ROUTE_WALK_HOME": 1}}
        self.assertIsNone(Engine._next_stage(normalize_state(dict(state(stage=2, affection=25, msgs=8), **done))))
        self.assertEqual(Engine._next_stage(normalize_state(dict(state(stage=2, affection=25, msgs=9), **done))), 3)
        self.assertIsNone(Engine._next_stage(state(stage=1, affection=10, flags=["care"], msgs=2)))
        self.assertEqual(Engine._next_stage(state(stage=1, affection=10, flags=["care"], msgs=3)), 2)

    def test_stage2_rule_without_route(self):  # S4-1: 갈래가 없으면 outside 로도 안 오름(갈래 사건 때 서버가 갈래를 정함)
        self.assertIsNone(Engine._next_stage(state(stage=2, affection=24, flags=["outside"])))
        self.assertIsNone(Engine._next_stage(state(stage=2, affection=25, flags=["outside"])))


if __name__ == "__main__":
    unittest.main()


class StageNamesTest(unittest.TestCase):
    """서버 단계 이름 = 앱 단계 이름 (앱 13단계부터)."""

    def test_names_match_app(self):
        from engine import STAGE_NAMES
        self.assertEqual(list(STAGE_NAMES.values()), ["경계", "티키타카", "썸", "고백 직전", "위기", "연인"])  # S4

    def test_prompt_headings_match(self):
        from engine import STAGE_NAMES
        text = (Path(__file__).resolve().parent.parent / "yoon_seoa_prompt.md").read_text(encoding="utf-8")
        for n, name in STAGE_NAMES.items():
            self.assertIn(f"# 단계 지침 — {n}. {name}", text)
