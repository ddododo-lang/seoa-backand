"""S4-B 위기 → 연인 / 엇갈림 → 화해. 키 없이 돈다.
실행: python -m unittest discover -s tests -v"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import normalize_state  # noqa: E402
from test_story_routes import Stub, engine, judge, system_for  # noqa: E402

WON = {"tease": "…연기 아니지. 이번엔.", "care": "…내려가자. 같이."}
START = {"tease": "태오야. 큰 건. 도시 밖. …새벽에 떠나래.", "care": "태오가 큰 일을 물어 왔어. 도시 밖. …새벽에 떠나."}
RECONCILE = "…늦었잖아. 그래도, 왔으니까."
OPEN = "…그날, 나 기다렸어. 바보같이."
HINT5 = "가지 말라고, 한 마디면 되는데. 그 말을 먼저 할 수는 없다."
HINT6 = "미안하다는 말 하나면, 아마 다 괜찮아질 텐데."
STAGE_UP_6 = "매일 온 거, 술 때문 아니었어."


def on(route="tease", **extra):
    """위기 중(소식을 꺼낸 답장은 이미 판정함)."""
    return {"stage": 5, "affection": 60, "route": route, "crisis": "on", "turn": 10,
            "used_events": {"CRISIS": 5}, **extra}


def rift(**extra):
    return {"stage": 5, "affection": 40, "route": "care", "crisis": "rift", "turn": 20,
            "used_events": {"CRISIS": 5, "RIFT_START": 12}, **extra}


def reply_system(state, msg="…"):
    llm = Stub()
    out = engine(llm).reply(state, msg, [], [])
    return out, llm.system


class CrisisStartTest(unittest.TestCase):
    def test_4_to_5_crisis(self):
        for route in ("tease", "care", None):
            if route is None:  # S4-1: 갈래가 없으면 그 골목 차례에 서버가 care 로 정하고 그 골목부터
                r = judge({"stage": 4, "affection": 60, "stage_user_msgs": 6, "used_events": {"ROUTE_ALLEY": 1}})
                self.assertEqual((r["state"]["stage"], r["state"]["route"], r["pending_event"]), (4, "care", None))
                r = judge({"stage": 4, "affection": 60, "stage_user_msgs": 6})
                self.assertEqual((r["state"]["stage"], r["state"]["route"], r["pending_event"]), (4, "care", "ALLEY"))
                st = dict(r["state"], pending_event=None, scene_msgs=3)
                r = judge(st)  # 그 골목을 마친 판정에서 바로 위기
            else:
                r = judge({"stage": 4, "affection": 60, "stage_user_msgs": 6, "route": route,
                           "used_events": {"ROUTE_ALLEY": 1}})
            st = r["state"]
            self.assertEqual((st["stage"], r["pending_event"], st["crisis"], st["judge_soon"], st["stage_label"]),
                             (5, "CRISIS", "on", True, "위기"), route)
            self.assertIsNone(st["departure_days_left"])
            out, system = reply_system(st)
            want = {"tease": "네온 골목", "care": "바 옥상", None: "바 옥상"}[route]  # 갈래가 없으면 care
            self.assertEqual(out["location"], want)
            self.assertIn(START[route or "care"], system)
            self.assertNotIn("# 위기 (이번 유저 메시지를 보고)", system)  # 소식을 꺼내는 답장에는 WATCH 없음
            self.assertNotIn("출발은 사흘 뒤", system)
            self.assertNotIn("붙잡기 (", system)  # ROUTE_PURSUE_* 없음

    def test_stage5_block(self):
        s = system_for(on())
        self.assertIn("# 단계 지침 — 5. 위기", s)
        self.assertIn("서아는 붙잡아 달라고 먼저 말하지 못한다. 대신 기다린다.", s)
        self.assertIn("함께 한 일과 들은 고백을 부정하지 않는다.", s)
        self.assertNotIn("일부러 차갑게 군다", s)

    def test_watch_line_by_route(self):
        for route, other in (("tease", "care"), ("care", "tease")):
            s = system_for(on(route))
            self.assertIn("이번 유저 메시지가 진심을 말하거나, 가지 말라고 붙잡거나, 오해를 푸는 말이면 이번 답장을 이것으로 한다:", s)
            self.assertIn(WON[route], s)
            self.assertNotIn(WON[other], s)
            self.assertNotIn("[tease]", s)
            self.assertIn("한 응답에 한 대목씩", s)  # 위기 장면 대목을 이어 감

    def test_reveal_turn_is_not_judged(self):
        r = judge({"stage": 4, "affection": 60, "stage_user_msgs": 6, "route": "care", "used_events": {"ROUTE_ALLEY": 1}})
        st = dict(r["state"], pending_event=None)
        st = engine(Stub({"flags": ["sincere"]})).judge(st, "가지 마", "*난간에 앉는다.* " + START["care"])["state"]  # 소식을 꺼낸 답장을 판정하는 차례
        self.assertEqual((st["stage"], st["crisis"], st["crisis_msgs"]), (5, "on", 0))
        st = judge(st, "가지 마", flags=["sincere"])["state"]
        self.assertEqual((st["stage"], st["crisis"]), (6, "done"))


class CrisisWinTest(unittest.TestCase):
    def test_win_shown_in_same_reply(self):
        r = engine(Stub({"flags": ["pursue"]})).judge(on("tease"), "가지 마.", "*고개를 든다.* " + WON["tease"])
        st = r["state"]
        self.assertEqual((st["stage"], st["crisis"], r["pending_event"], st["won_line"]), (6, "done", "STAGE_UP_6", None))
        self.assertFalse(st["judge_soon"])
        s = system_for(st)
        self.assertIn(STAGE_UP_6, s)
        self.assertNotIn(WON["tease"], s)  # 같은 답장에 이미 나왔으니 다시 넣지 않음

    def test_win_not_shown_goes_next_reply_once(self):
        r = engine(Stub({"flags": ["sincere"]})).judge(on("care"), "너 없으면 안 돼.", "…")
        st = r["state"]
        self.assertEqual((st["stage"], st["won_line"], r["pending_event"]), (6, "WON_CARE", "STAGE_UP_6"))
        s = system_for(st)
        self.assertIn(WON["care"], s)
        self.assertIn(STAGE_UP_6, s)
        self.assertLess(s.index(WON["care"]), s.index("# 단계가 막 올랐다"))  # 성공 대사 → 상징 장면 차례
        st = judge(dict(st, pending_event=None))["state"]
        self.assertIsNone(st["won_line"])
        self.assertNotIn(WON["care"], system_for(st))


class CrisisHintAndRiftTest(unittest.TestCase):
    def test_hint_after_three(self):
        st = judge(on(crisis_msgs=2), "…")["state"]
        self.assertEqual((st["crisis_msgs"], st["hint_due"]), (3, "HINT_5"))
        out, system = reply_system(st)
        self.assertIn(HINT5, system)
        st = judge(st, "…")["state"]
        self.assertIsNone(st["hint_due"])  # 한 번만

    def test_rift_after_six(self):
        r = judge(on(crisis_msgs=5), "…")
        st = r["state"]
        self.assertEqual((st["stage"], st["crisis"], st["affection"], r["pending_event"], st["crisis_msgs"]),
                         (5, "rift", 40, "RIFT_START", 0))
        self.assertEqual((st["rewind_offer"], st["judge_soon"], st["stage_label"]), (True, False, "엇갈림"))
        s = system_for(st)
        self.assertIn("…그래. 그럴 줄 알았어.", s)
        self.assertIn("웃는데 눈은 웃지 않는다", s)
        st = judge(dict(st, pending_event=None))["state"]
        self.assertFalse(st["rewind_offer"])  # 다음 판정에서 false
        self.assertIn("엇갈림 동안의 서아: 와도 오래 있지 않는다.", system_for(st))

    def test_reconcile_open_after_six_then_apology(self):
        st = judge(rift(crisis_msgs=4), "미안해", flags=["apologize"])["state"]  # 문이 열리기 전 사과는 아직
        self.assertEqual((st["stage"], st["crisis_msgs"], st["pending_event"]), (5, 5, None))
        r = judge(st, "…")
        st = r["state"]
        self.assertEqual(r["pending_event"], "RECONCILE_OPEN")
        self.assertTrue(st["judge_soon"])
        self.assertIn(OPEN, system_for(st))
        st = dict(st, pending_event=None)
        self.assertIn("유저가 사과하거나 진심을 말하면 이번 답장을 이것으로: ", system_for(st))
        self.assertIn(RECONCILE, system_for(st))
        st = judge(st, "미안해", flags=["apologize"])["state"]  # 문을 연 답장을 판정하는 차례
        self.assertEqual(st["stage"], 5)
        r = engine(Stub({"flags": ["apologize"]})).judge(st, "그날 미안했어.", "*몸을 돌린다.* " + RECONCILE)
        st = r["state"]
        self.assertEqual((st["stage"], st["crisis"], r["pending_event"], st["won_line"]), (6, "done", "STAGE_UP_6", None))
        self.assertGreaterEqual(st["affection"], 55)
        self.assertEqual(st["stage_label"], "연인")

    def test_reconcile_hint(self):
        st = rift(crisis_msgs=6, hint_msgs=6, used_events={"CRISIS": 5, "RIFT_START": 12, "RECONCILE_OPEN": 15})
        st = judge(st, "…\n…")["state"]
        self.assertIsNone(st["hint_due"])
        st = judge(st, "…")["state"]  # 문 뒤 3개
        self.assertEqual(st["hint_due"], "HINT_6")
        out, system = reply_system(st)
        self.assertIn(HINT6, system)

    def test_sincere_also_reconciles(self):
        st = rift(crisis_msgs=8, used_events={"CRISIS": 5, "RECONCILE_OPEN": 15})
        st = judge(st, "보고 싶었어.", flags=["sincere"])["state"]
        self.assertEqual(st["stage"], 6)
        self.assertEqual(st["affection"], 55)
        self.assertEqual(st["won_line"], "WON_RECONCILE")
        self.assertIn(RECONCILE, system_for(st))


class NoDepartureTest(unittest.TestCase):
    def test_no_days_left_last_night_departed(self):
        st = on("care", crisis_msgs=0, time_of_day="밤 11시")
        seen = []
        for _ in range(12):
            r = judge(st, "…", time_advance=True, time_of_day="밤 11시")
            seen.append(r["pending_event"])
            st = dict(r["state"], pending_event=None)
            self.assertIsNone(st["ending"])
            self.assertIsNone(st["departure_days_left"])
        self.assertFalse({"DAYS_LEFT_2", "DAYS_LEFT_1", "LAST_NIGHT", "DEPARTURE"} & set(seen))
        for ev in ("DAYS_LEFT_2", "LAST_NIGHT", "DEPARTURE"):
            self.assertNotEqual(normalize_state({"stage": 5, "pending_event": ev})["pending_event"], ev)
        self.assertEqual(normalize_state({"stage": 5, "pending_event": "DEPARTURE"})["pending_event"], "CRISIS")

    def test_normalize(self):
        self.assertEqual(normalize_state({})["crisis"], "none")
        self.assertEqual(normalize_state({"stage": 5, "crisis": "weird"})["crisis"], "on")
        self.assertEqual(normalize_state({"stage": 5, "crisis": "rift"})["stage_label"], "엇갈림")
        self.assertEqual(normalize_state({"stage": 6})["crisis"], "done")
        self.assertEqual(normalize_state({"stage": 3, "crisis": "on"})["crisis"], "none")
        self.assertFalse(normalize_state({"rewind_offer": "yes"})["rewind_offer"])
        self.assertIsNone(normalize_state({"won_line": "x"})["won_line"])


class UnchangedTest(unittest.TestCase):
    def test_xmas(self):
        x = {"stage": 2, "episode": "s2", "episode_stage": 5, "episode_stage_name": "엇갈림", "location": "서아의 자취방"}
        s = system_for(x)
        self.assertIn("5 엇갈림: 가까워진 게 겁이 나서 다시 차가워진다.", s)
        self.assertNotIn("# 위기", s)
        self.assertNotIn("단계 지침 — 5", s)
        r = judge(x, "가지 마", flags=["pursue"])
        self.assertEqual((r["state"]["episode_stage"], r["pending_event"], r["state"]["crisis"]), (5, None, "none"))

    def test_stage6_side_and_name_flow(self):
        st = judge({"stage": 6, "route": "care", "morning_done": True, "taeo_step": 2}, "…")["state"]
        self.assertEqual(st["pending_event"], "TAEO_3")  # 조연 마무리 그대로
        st = judge({"stage": 2, "affection": 30, "stage_flags": ["outside"], "stage_user_msgs": 9, "route": "tease",
                    "used_events": {"NIGHT_END_2": 0, "ROUTE_DATE": 1}, "user_name": "도윤"})["state"]
        self.assertEqual((st["stage"], st["name_state"], st["pending_event"]), (3, "asked", "STAGE_UP_3"))


if __name__ == "__main__":
    unittest.main()
