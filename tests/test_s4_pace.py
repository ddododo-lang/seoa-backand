"""S4-A 진행 속도 · 속마음 힌트 · 위장 연인 · 바 열쇠. 키 없이 돈다.
실행: python -m unittest discover -s tests -v"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import MIN_STAGE_MSGS, ROUTE_EVENT_AFFECTION, Engine, normalize_state  # noqa: E402
from test_story_routes import Stub, engine, judge, system_for  # noqa: E402

HINT1 = "…이 사람, 이런 상처 같은 것도 신경 써 주려나."
HINT2 = "창밖 네온에 시선이 오래 머문다. 이 바 밖에서도, 이 사람이랑 이렇게 얘기할 수 있을까."
HINT3 = "장난 말고, 진짜 말 한마디. 그걸 기다리는 자신이 우스워서 괜히 잔만 돌린다."
HINT4 = "보여 주고 싶은 곳이 있다. 아무한테도 보여 준 적 없는 곳."


def nxt(**st):
    return Engine._next_stage(normalize_state({"stage_user_msgs": 99, **st}))


class PaceTest(unittest.TestCase):  # A1·A3
    def test_numbers(self):
        self.assertEqual(MIN_STAGE_MSGS, {1: 3, 2: 9, 3: 9, 4: 6})
        self.assertEqual(ROUTE_EVENT_AFFECTION, {2: 15, 3: 28, 4: 42})

    def test_route_event_before_stage_condition(self):
        for stage, aff in ((2, 25), (3, 40), (4, 55)):
            self.assertLess(ROUTE_EVENT_AFFECTION[stage], aff)


class StageRuleTest(unittest.TestCase):  # A2
    def test_1_to_2_unchanged(self):
        self.assertEqual(nxt(stage=1, affection=10, stage_flags=["care"]), 2)
        self.assertIsNone(nxt(stage=1, affection=10, stage_flags=["care"], stage_user_msgs=2))

    def test_2_to_3_with_route_needs_scene_done(self):
        base = {"stage": 2, "affection": 25, "route": "tease"}
        self.assertIsNone(nxt(**base))  # 장면 전
        self.assertIsNone(nxt(**base, used_events={"ROUTE_DATE": 3}, scene="DATE", scene_judges_left=1))  # 진행 중
        self.assertEqual(nxt(**base, used_events={"ROUTE_DATE": 3}), 3)  # 마침
        self.assertIsNone(nxt(**dict(base, affection=24), used_events={"ROUTE_DATE": 3}))
        self.assertEqual(nxt(stage=2, affection=25, route="care", used_events={"ROUTE_WALK_HOME": 1}), 3)
        self.assertIsNone(nxt(**base, stage_flags=["outside"]))  # S4-1: outside 만으로는 안 오름(장면을 마쳐야)

    def test_2_to_3_without_route_outside(self):  # S4-1: 갈래가 없으면 갈래 사건 차례에 서버가 정할 때까지 기다림
        self.assertIsNone(nxt(stage=2, affection=25, stage_flags=["outside"]))
        self.assertIsNone(nxt(stage=2, affection=25))

    def test_3_to_4(self):
        done = {"used_events": {"ROUTE_STORM": 1}}
        self.assertEqual(nxt(stage=3, affection=40, route="tease", stage_flags=["sincere"], **done), 4)
        self.assertIsNone(nxt(stage=3, affection=40, route="tease", stage_flags=["sincere"]))  # 장면 전
        self.assertIsNone(nxt(stage=3, affection=40, route="tease", **done))  # sincere 없음
        self.assertIsNone(nxt(stage=3, affection=39, route="tease", stage_flags=["sincere"], **done))
        self.assertEqual(nxt(stage=3, affection=40, route="care", stage_flags=["sincere"],
                             used_events={"ROUTE_ROOFTOP": 1}), 4)
        self.assertIsNone(nxt(stage=3, affection=40, stage_flags=["sincere"]))  # 갈래 없음: S4-1 갈래 사건 때 서버가 정함

    def test_4_to_5(self):
        self.assertEqual(nxt(stage=4, affection=55, route="care", used_events={"ROUTE_ALLEY": 1}), 5)
        self.assertIsNone(nxt(stage=4, affection=55, route="care", used_events={"ROUTE_ALLEY": 1},
                              scene="ALLEY", scene_judges_left=2))
        self.assertIsNone(nxt(stage=4, affection=54, route="care", used_events={"ROUTE_ALLEY": 1}))
        self.assertIsNone(nxt(stage=4, affection=55))  # 갈래 없음: S4-1 갈래 사건 때 서버가 정함
        self.assertEqual(MIN_STAGE_MSGS[4], 6)
        self.assertIsNone(nxt(stage=4, affection=55, stage_user_msgs=5))

    def test_scene_closes_then_stage_up_same_judge(self):
        st = {"stage": 2, "affection": 30, "route": "tease", "stage_user_msgs": 9,
              "used_events": {"ROUTE_DATE": 1}, "scene": "DATE", "scene_msgs": 3}  # S4-1: 정상 답장 3개 지남
        r = judge(st)
        self.assertTrue(r["stage_up"])
        self.assertEqual(r["state"]["stage"], 3)


class HintTest(unittest.TestCase):  # A4
    def test_blocks(self):
        e = engine()
        for n, line in ((1, HINT1), (2, HINT2), (3, HINT3), (4, HINT4)):
            self.assertIn("이 줄을 지문 끝에 그대로: " + line, e.blocks[f"HINT_{n}"])

    def test_after_min_without_condition(self):
        st = judge({"stage": 1, "affection": 5, "stage_user_msgs": 1}, "응\n그래")["state"]  # 3개째
        self.assertEqual((st["hint_due"], st["stage_hints"]), ("HINT_1", 1))
        st = judge({"stage": 1, "affection": 5, "stage_user_msgs": 0}, "응")["state"]
        self.assertIsNone(st["hint_due"])  # 최소 전

    def test_in_next_reply_once(self):
        st = judge({"stage": 3, "affection": 30, "stage_user_msgs": 8, "route": "care",
                    "used_events": {"NIGHT_END_3": 0, "ROUTE_ROOFTOP": 1}}, "응")["state"]  # S4-1: 장면은 마침
        self.assertEqual(st["hint_due"], "HINT_3")
        llm = Stub()
        engine(llm).reply(st, "응", [], [])
        self.assertIn(HINT3, llm.system)
        self.assertIn("이 줄을 지문 끝에 그대로", llm.system)
        llm = Stub()  # 판정 전 다음 답장: 이미 나왔으면 다시 넣지 않음
        engine(llm).reply(st, "응", [{"r": "c", "t": "*잔을 본다. " + HINT3 + "* …뭐."}], [])
        self.assertNotIn(HINT3, llm.system)
        st = judge(st, "응")["state"]  # 다음 판정이 비움, 10개 전엔 다시 안 정함
        self.assertIsNone(st["hint_due"])

    def test_again_after_10_and_max_two(self):
        st = judge({"stage": 2, "affection": 20, "stage_user_msgs": 8, "route": "care",
                    "used_events": {"NIGHT_END_2": 0, "ROUTE_WALK_HOME": 1}}, "응")["state"]  # S4-1: 장면은 마쳤고 호감이 모자람
        self.assertEqual((st["hint_due"], st["stage_hints"], st["hint_msgs"]), ("HINT_2", 1, 9))
        st = judge(dict(st, stage_user_msgs=17, short_streak=0), "응")["state"]  # 9 + 9
        self.assertIsNone(st["hint_due"])
        st = judge(dict(st, stage_user_msgs=18, short_streak=0), "응")["state"]  # 9 + 10 (short_streak 0: 짧은 답 무작위 사건이 끼지 않게)
        self.assertEqual((st["hint_due"], st["stage_hints"]), ("HINT_2", 2))
        st = judge(dict(st, stage_user_msgs=40, short_streak=0), "응")["state"]
        self.assertIsNone(st["hint_due"])  # 최대 두 번

    def test_not_with_event_or_name_scene_or_scene(self):
        r = judge({"stage": 2, "affection": 20, "stage_user_msgs": 8, "route": "care",
                   "used_events": {"NIGHT_END_2": 0}})  # 갈래 장면이 정해지는 판정
        self.assertEqual(r["pending_event"], "WALK_HOME")
        self.assertIsNone(r["state"]["hint_due"])
        st = judge({"stage": 3, "affection": 30, "stage_user_msgs": 8, "user_name": "도윤", "name_state": "asked",
                    "used_events": {"NAME_ASK": 0}})["state"]
        self.assertIsNone(st["hint_due"])
        st = judge({"stage": 3, "affection": 30, "stage_user_msgs": 8, "route": "tease",
                    "used_events": {"ROUTE_STORM": 0}, "scene": "STORM", "scene_judges_left": 2})["state"]
        self.assertIsNone(st["hint_due"])
        llm = Stub()  # 사건이 있는 답장에는 넣지 않음
        engine(llm).reply({"stage": 1, "hint_due": "HINT_1", "pending_event": "INJURY"}, "응", [], [])
        self.assertNotIn(HINT1, llm.system)

    def test_reset_on_stage_up_and_not_in_xmas(self):
        st = normalize_state({"stage": 1, "stage_hints": 2, "hint_msgs": 7, "hint_due": "HINT_1"})
        Engine._advance(st, 2, [])
        self.assertEqual((st["stage_hints"], st["hint_msgs"], st["hint_due"]), (0, 0, None))
        xmas = {"stage": 1, "episode": "s2", "episode_stage": 1, "location": "크리스마스 거리", "stage_user_msgs": 5}
        self.assertIsNone(judge(xmas, "응")["state"]["hint_due"])
        self.assertIsNone(normalize_state(dict(xmas, hint_due="HINT_1"))["hint_due"])
        self.assertIsNone(normalize_state({"hint_due": "EVENT_X"})["hint_due"])


class DateAndKeyTest(unittest.TestCase):  # A5·A6
    def test_fake_lover(self):
        block = engine().blocks["EVENT_DATE"]
        for line in ("「사장님, 오늘 밤만 내 애인 해. …일이야. 네온 골목에 쫓는 놈이 있는데, 혼자 다니면 눈에 띄어.」",
                     "「따라와. 팔짱은 내가 낄게. 연기니까.」", "「지금. 웃어. …진짜 애인처럼.」",
                     "「끝. …연기 꽤 하네, 사장님.」", "다음 대목으로 이어 간다", "한 응답에 한 대목씩"):
            self.assertIn(line, block)
        self.assertNotIn("하룻밤 애인", block)
        st = judge({"stage": 2, "affection": 20, "route": "tease", "used_events": {"NIGHT_END_2": 0}})["state"]
        self.assertIn("오늘 밤만 내 애인 해", system_for(st))

    def test_key_instead_of_flowers(self):
        gift = engine().blocks["GIFT_MOMENT"]
        self.assertIn("4단계 그 골목·처음 이름을 부를 때(바 여분 열쇠)", gift)
        self.assertNotIn("꽃다발", gift)


if __name__ == "__main__":
    unittest.main()
