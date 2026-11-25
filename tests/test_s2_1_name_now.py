"""S2-1 이름 장면 반응을 같은 답장에서 바로 · 이름 장면 중 무작위 사건 막기. 키 없이 돈다.
실행: python -m unittest discover -s tests -v"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import normalize_state  # noqa: E402
from test_s2_user_name import Stub, at_stage2_ready, engine, system_for  # noqa: E402

REFUSE = "…싫어? 그럼 말구."
FORGIVE = "…이번 한 번만 넘어가 줄게. 도윤."
AGAIN = "…두 번은 안 물어봐, 사장님."
HEAD = "# 이름 장면 (이번 유저 메시지를 보고)"


def judge(state, flags=(), last_user="…", last_reply="…"):
    return engine(Stub(flags)).judge(state, last_user, last_reply)["state"]


def named(ns, **extra):
    return {"stage": 3, "affection": 55, "user_name": "도윤", "name_state": ns, **extra}


class NameWatchTest(unittest.TestCase):
    def test_line_per_state(self):
        for ns, line, others in [("asked", REFUSE, (FORGIVE, AGAIN)), ("accepted", REFUSE, (FORGIVE, AGAIN)),
                                 ("refused", FORGIVE, (REFUSE, AGAIN)), ("forgiven", AGAIN, (REFUSE, FORGIVE))]:
            s = system_for(named(ns))
            self.assertIn(HEAD, s, ns)
            self.assertIn(line, s, ns)
            for o in others:
                self.assertNotIn(o, s, ns)
            self.assertNotIn("[asked]", s)
        self.assertIn("사과가 아니면 서운한 채로 짧게 받고, 이름은 부르지 않는다.", system_for(named("refused")))

    def test_not_present(self):
        cases = {
            "none": named("none"), "closed": named("closed"), "no name": {"stage": 3, "name_state": "accepted"},
            "xmas": named("accepted", episode="s2", episode_stage=3, episode_stage_name="썸", location="서아의 자취방"),
            "ask turn": named("asked", pending_event="STAGE_UP_3"),
            "event turn": named("refused", stage=2, pending_event="NAME_REFUSED"),
        }
        for why, st in cases.items():
            self.assertNotIn(HEAD, system_for(st), why)


class ShownInReplyTest(unittest.TestCase):
    def test_refused_shown_no_pending(self):
        st = judge(named("accepted"), ["refuse_name"], "싫은데? 그냥 사장님이라고 불러", "*잔을 내려놓는다.* …싫어? 그럼 말구.")
        self.assertEqual((st["stage"], st["name_state"]), (2, "refused"))
        self.assertEqual(st["affection"], 45)  # 상태·단계·호감 변화는 그대로
        self.assertIsNone(st["pending_event"])

    def test_refused_not_shown_pending(self):
        st = judge(named("accepted"), ["refuse_name"], "싫은데", "…장난이야?")
        self.assertEqual(st["pending_event"], "NAME_REFUSED")

    def test_forgiven_and_again(self):
        st = judge(named("refused", stage=2, affection=30), ["apologize"], "미안해", "…이번 한 번만 넘어가 줄게. 도윤.")
        self.assertEqual((st["stage"], st["name_state"], st["pending_event"]), (3, "forgiven", None))
        st = judge(named("refused", stage=2, affection=30), ["apologize"], "미안해", "…흥.")
        self.assertEqual(st["pending_event"], "NAME_FORGIVEN")
        st = judge(named("forgiven"), ["refuse_name"], "부르지 마", "…두 번은 안 물어봐, 사장님.")
        self.assertEqual((st["name_state"], st["pending_event"]), ("closed", None))
        st = judge(named("forgiven"), ["refuse_name"], "부르지 마", "…")
        self.assertEqual(st["pending_event"], "NAME_REFUSED_AGAIN")


class QuietTest(unittest.TestCase):
    SHORT = "응"  # 짧은 답: 이것만으로 무작위 사건 조건(짧은 답 3번)이 참

    def ripe(self, **extra):  # 무작위 사건이 바로 나올 상태 (15턴 조용함 + 짧은 답 연속)
        return {"turns_since_event": 30, "short_streak": 5, "stage_user_msgs": 0, **extra}

    def test_no_random_while_asked_or_refused(self):
        for ns, stage in (("asked", 3), ("refused", 2)):
            st = judge(named(ns, stage=stage, affection=20, used_events={"NAME_ASK": 0}, **self.ripe()),
                       last_user=self.SHORT)  # NAME_ASK: 방금 물음(아직 asked)
            self.assertEqual(st["name_state"], ns)
            self.assertIsNone(st["pending_event"], ns)

    def test_quiet_three_messages_after_name_event(self):
        st = judge(named("refused", stage=2, affection=30, **self.ripe()), ["apologize"], "미안해", "…넘어가 줄게. 도윤.")
        self.assertEqual((st["name_state"], st["name_quiet"], st["pending_event"]), ("forgiven", 3, None))
        st.update(turns_since_event=30, short_streak=5)
        st = judge(st, last_user="응\n그래")  # 메시지 2개 → 1 남음
        self.assertEqual((st["name_quiet"], st["pending_event"]), (1, None))
        st.update(turns_since_event=30, short_streak=5)
        st = judge(st, last_user="응")  # 3개째 → 쉬기 끝, 이번 판정부터 사건이 나올 수 있음
        self.assertEqual(st["name_quiet"], 0)
        self.assertIsNotNone(st["pending_event"])

    def test_without_name_random_still_happens(self):
        st = judge({"stage": 3, "affection": 55, **self.ripe()}, last_user=self.SHORT)
        self.assertIsNotNone(st["pending_event"])

    def test_normalize_quiet(self):
        self.assertEqual(normalize_state({"name_quiet": 9})["name_quiet"], 3)
        self.assertEqual(normalize_state({"name_quiet": "x"})["name_quiet"], 0)


if __name__ == "__main__":
    unittest.main()
