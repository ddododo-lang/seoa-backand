"""S7: 옥상·위기 원고 정리(A) · 연인 확정(B). 키 없이 돈다."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_story_routes import Stub, engine  # noqa: E402


def reply_with(state, action):
    return engine(Stub(reply={"action": action})).reply(state, "응", [], [])


class RooftopScriptTest(unittest.TestCase):  # A1
    def test_two_beats(self):
        b = engine().blocks["EVENT_ROOFTOP"]
        first = "- (아직 바 안) *창밖을 보던 서아가 눈을 크게 뜬다. 1년에 하루, 비가 그친 날이다.* 「…비, 그쳤어. 사장님, 여기 옥상… 올라가도 돼?」"
        second = "- (유저가 받아 주면) *당신을 따라 계단을 올라 옥상 문을 연다. 젖지 않은 밤공기가 쏟아진다.* 장소를 \"바 옥상\"으로 옮긴다."
        self.assertIn(first, b)
        self.assertIn(second, b)
        self.assertLess(b.index(first), b.index(second))
        self.assertLess(b.index(second), b.index("「별, 처음 봐. …이 도시에서.」"))
        self.assertIn("「…그럼 창문으로 볼게.」", b)
        self.assertNotIn("손목을 잡고", b)

    def test_first_beat_does_not_move(self):
        st = {"stage": 3, "route": "care", "pending_event": "ROOFTOP", "scene": "ROOFTOP"}
        self.assertIsNone(reply_with(st, "창밖을 보던 서아가 눈을 크게 뜬다. 1년에 하루, 비가 그친 날이다.")["location"])
        moved = reply_with(dict(st, pending_event=None, scene_msgs=1), "당신을 따라 계단을 올라 옥상 문을 연다. 젖지 않은 밤공기가 쏟아진다.")
        self.assertEqual(moved["location"], "바 옥상")


class CrisisCareScriptTest(unittest.TestCase):  # A2
    def test_move_beat_first(self):
        b = engine().blocks["CRISIS_CARE"]
        self.assertIn("1. (시작) *골목을 나와 당신과 함께 바 옥상으로 올라온다. 서아가 먼저 난간 위로 올라앉는다. "
                      "도시를 내려다보는 서아만의 자리.", b)
        self.assertNotIn("서아가 먼저 옥상 난간 위로", b)


if __name__ == "__main__":
    unittest.main()
