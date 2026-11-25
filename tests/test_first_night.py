"""첫날 밤 사정(FIRST_NIGHT)은 처음 몇 턴만 (feat/first-night-once) 키 없는 테스트. 실행: python -m unittest discover -s tests -v"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import FIRST_NIGHT_TURNS  # noqa: E402
from test_story_routes import Stub, engine  # noqa: E402

MARK = "오늘 밤 서아에게 있었던 일"


def system_after(user_turns, day=1, episode=None):
    """유저 메시지 user_turns 개가 기록에 있을 때 대화 호출에 들어간 system."""
    history = []
    for i in range(user_turns):
        history += [{"r": "u", "t": f"말 {i}"}, {"r": "c", "t": f"*잔을 든다.* 응 {i}"}]
    llm = Stub()
    state = {"day": day}
    if episode:
        state.update(episode=episode, episode_stage=1)
    engine(llm).reply(state, "안녕", history, [])
    return llm.system


class FirstNightTest(unittest.TestCase):
    def test_first_turns_have_it(self):
        for n in range(FIRST_NIGHT_TURNS):
            self.assertIn(MARK, system_after(n), n)

    def test_later_turns_drop_it(self):
        for n in (FIRST_NIGHT_TURNS, FIRST_NIGHT_TURNS + 5, 20):
            self.assertNotIn(MARK, system_after(n), n)

    def test_not_on_other_days(self):
        self.assertNotIn(MARK, system_after(0, day=2))

    def test_not_in_episode(self):
        self.assertNotIn(MARK, system_after(0, episode="s2"))

    def test_block_says_mention_once(self):
        self.assertIn("다시 묘사하지 않는다", system_after(0))


if __name__ == "__main__":
    unittest.main()
