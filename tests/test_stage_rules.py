"""단계 상승 규칙 (키 없이). 실행: python -m unittest discover -s tests -v"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import Engine, new_state  # noqa: E402


def state(stage=1, affection=0, flags=()):
    s = new_state()
    s.update(stage=stage, affection=affection, stage_flags=list(flags))
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

    def test_stage2_rule_unchanged(self):
        self.assertIsNone(Engine._next_stage(state(stage=2, affection=39, flags=["outside"])))
        self.assertEqual(Engine._next_stage(state(stage=2, affection=40, flags=["outside"])), 3)


if __name__ == "__main__":
    unittest.main()
