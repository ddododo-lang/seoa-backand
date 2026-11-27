"""S4-C 밤 넘기기. 키 없이 돈다.
실행: python -m unittest discover -s tests -v"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import NIGHT_END_AFTER, NIGHT_WAIT_MAX, Engine, normalize_state  # noqa: E402
from test_story_routes import Stub, engine, judge, system_for  # noqa: E402

END_1 = "…내일도 열지, 이 바?"
END = {"tease": "연기 수당은… 다음에 술로 받아 갈게.", "care": "…바래다준 거, 고마웠어. 다음엔 내가 먼저 올게."}
START_2 = "…또 왔어. 손은 멀쩡해. 덕분에."
START_3 = {"tease": "우리 그날 커플이었다고 소문났더라. …책임져, 사장님.", "care": "오늘은 안 다쳤어. …먼저 확인하려고 했지?"}
RIFT_FIRST = "…지나가다 들렀어. 오래는 안 있어."
RULE = "유저 메시지 「[다음 밤]」은 유저의 말로 다루지 않는다."
FIVE = "응\n그래\n그렇구나\n알았어\n음"


def msgs(n):
    return "\n".join(["응"] * n)


class NoCallStub(Stub):
    """판정 모델을 부르면 실패 (「[다음 밤]」만 온 판정은 부르지 않아야 함)."""

    def complete(self, system, prompt, json_mode=False, budget_sec=None):
        raise AssertionError("판정 모델을 불렀음")


def after_1_to_2(**extra):
    st = normalize_state({"stage": 1, "affection": 12, "stage_flags": ["care"], "stage_user_msgs": 3})
    Engine._advance(st, 2, [])
    st["pending_event"] = None  # 앱이 STAGE_UP_2 답장 뒤 비움
    st["stage_up_due"] = None  # S6: 상징 장면이 나간 것을 확인함(그 뒤부터 밤 마무리를 셈)
    st.update(extra)
    return st


def chat(state, msg="[다음 밤]", history=()):
    llm = Stub()
    out = engine(llm).reply(state, msg, list(history), [])
    return out, llm.system


class NightEndTest(unittest.TestCase):
    def test_after_1_to_2(self):
        r = judge(after_1_to_2(), msgs(NIGHT_END_AFTER - 1))
        self.assertIsNone(r["pending_event"])
        r = judge(after_1_to_2(), msgs(NIGHT_END_AFTER))  # S6: 상징 장면 답장 다음부터 NIGHT_END_AFTER 개
        st = r["state"]
        self.assertEqual((r["pending_event"], st["night_next"], st["night_card"]), ("NIGHT_END", "NIGHT_START_2", "2일째 밤"))
        out, system = chat(st, "잘 가")
        self.assertIn(END_1, system)
        self.assertIn("# 밤이 끝난다", system)
        st = judge(dict(st, pending_event=None), FIVE)["state"]  # 한 번 더 판정해도 다시 안 정함
        self.assertEqual(st["night_next"], "NIGHT_START_2")
        self.assertIsNone(st["pending_event"])

    def test_no_event_on_the_answer(self):
        st = dict(after_1_to_2(stage_user_msgs=6, night_next="NIGHT_START_2", used_events={"NIGHT_END_2": 1}),
                  short_streak=5, turns_since_event=40)
        r = judge(st, "응")
        self.assertIsNone(r["pending_event"])  # 짧은 답이 이어져도 무작위 사건 없음
        self.assertIsNone(r["state"]["hint_due"])
        self.assertEqual(r["state"]["night_next"], "NIGHT_START_2")

    def test_stage3_by_route(self):
        for route in ("tease", "care", None):
            st = normalize_state({"stage": 2, "affection": 30, "route": route, "stage_user_msgs": 9,
                                  "stage_flags": ["outside"]})
            Engine._advance(st, 3, [])
            st["pending_event"], st["stage_up_due"] = None, None  # 상징 장면이 나감(S6)
            r = judge(st, msgs(NIGHT_END_AFTER))
            key = route or "care"
            self.assertEqual((r["pending_event"], r["state"]["night_next"], r["state"]["night_card"]),
                             ("NIGHT_END", f"NIGHT_START_3_{key.upper()}", "며칠 뒤 밤"), route)
            self.assertIn(END[key], chat(r["state"], "…")[1])

    def test_only_stage_2_and_3(self):
        for stage in (1, 4, 6):
            r = judge({"stage": stage, "affection": 5, "stage_user_msgs": 20, "short_streak": 0}, FIVE)
            self.assertNotEqual(r["pending_event"], "NIGHT_END", stage)
            self.assertIsNone(r["state"]["night_next"])


class NextNightTest(unittest.TestCase):
    def ready(self, **extra):
        return after_1_to_2(stage_user_msgs=7, night_next="NIGHT_START_2", used_events={"NIGHT_END_2": 1},
                            day=1, time_of_day="새벽 3시", location="네온 골목", affection=20, **extra)

    def test_reply_first_line(self):
        history = [{"r": "c", "t": "*손을 쥐었다 편다.* " + END_1}, {"r": "u", "t": "응, 내일도 열어."},
                   {"r": "c", "t": "…그래."}, {"r": "u", "t": "[다음 밤]"}]
        llm = Stub(reply={"location": None})
        out = engine(llm).reply(self.ready(), "[다음 밤]", history, [])
        self.assertIn(START_2, llm.system)
        self.assertIn(RULE, llm.system)
        self.assertEqual(out["location"], "바 '라스트 오더' 1층")

    def test_judge_moves_night_without_affection(self):
        r = engine(NoCallStub()).judge(self.ready(), "[다음 밤]", "*문 종이 울린다.* " + START_2)
        st = r["state"]
        self.assertEqual((st["day"], st["time_of_day"], st["location"]), (2, "밤", "바 '라스트 오더' 1층"))
        self.assertEqual((st["night_next"], st["night_card"], st["affection"]), (None, None, 20))
        self.assertEqual(r["flags"], [])
        self.assertEqual(st["stage_user_msgs"], 7)  # 「[다음 밤]」은 셈하지 않음
        llm = Stub({"affection_delta": 9, "flags": ["care"], "time_advance": True})  # 요약 요청이 있어도 0
        r = engine(llm).judge(self.ready(), "[다음 밤]", "…", need_summary=True)
        self.assertEqual((r["state"]["affection"], r["flags"], r["state"]["day"]), (20, [], 2))

    def test_three_days_and_scene_closes(self):
        st = normalize_state({"stage": 3, "affection": 35, "route": "tease", "night_next": "NIGHT_START_3_TEASE",
                              "used_events": {"NIGHT_END_3": 1, "ROUTE_STORM": 1}, "scene": "STORM",
                              "scene_judges_left": 2, "day": 2})
        self.assertIn(START_3["tease"], chat(st)[1])
        self.assertNotIn("폭우로 길이 막혀", chat(st)[1])  # 첫마디 답장에는 갈래 장면을 잇지 않음
        r = judge(st, "[다음 밤]")
        st = r["state"]  # S4-1: 밤 넘기기로 닫힌 장면은 마친 것이 아님 → 다음 밤에 처음부터 다시
        self.assertEqual((st["day"], st["night_next"], r["pending_event"], st["scene"], st["scene_msgs"]),
                         (5, None, "STORM", "STORM", 0))
        self.assertTrue(any("중단(밤 넘기기)" in c for c in r["changes"]))
        care = normalize_state({"stage": 3, "route": "care", "night_next": "NIGHT_START_3_CARE"})
        self.assertIn(START_3["care"], chat(care)[1])

    def test_route_scene_waits_for_next_night(self):
        st = {"stage": 2, "affection": 20, "route": "tease", "stage_user_msgs": 2}
        self.assertIsNone(judge(st, "…")["pending_event"])  # 첫 밤에는 위장 연인 부탁 없음
        r = judge(dict(self.ready(route="tease"), affection=20), "[다음 밤]")
        self.assertEqual(r["pending_event"], "DATE")  # 다음 밤이 되면 갈래 장면

    def test_marker_without_night_next_is_plain(self):
        out, system = chat(normalize_state({"stage": 2}), "[다음 밤]")
        self.assertNotIn(RULE, system)
        self.assertIsNone(normalize_state({"night_next": "WHATEVER"})["night_next"])
        self.assertEqual(normalize_state({"night_next": "RIFT_NIGHT"})["night_card"], "며칠 뒤 밤")
        x = {"stage": 2, "episode": "s2", "episode_stage": 2, "location": "크리스마스 거리", "night_next": "NIGHT_START_2"}
        self.assertIsNone(normalize_state(x)["night_next"])

    def test_fallback_without_marker(self):
        st = self.ready(night_wait=NIGHT_WAIT_MAX - 1)
        st = judge(st, "응")["state"]
        self.assertEqual((st["night_next"], st["day"]), (None, 2))


class RiftNightTest(unittest.TestCase):
    def test_rift_goes_to_rift_night(self):
        st = {"stage": 5, "affection": 60, "route": "care", "crisis": "on", "turn": 10, "crisis_msgs": 5,
              "used_events": {"CRISIS": 5}, "day": 3}
        r = judge(st, "…")
        st = r["state"]
        self.assertEqual((r["pending_event"], st["night_next"], st["night_card"]), ("RIFT_START", "RIFT_NIGHT", "며칠 뒤 밤"))
        st = judge(dict(st, pending_event=None), "…\n그래")["state"]  # 한 번 답할 기회: 아직 세지 않음
        self.assertEqual((st["crisis_msgs"], st["night_next"]), (0, "RIFT_NIGHT"))
        self.assertNotEqual(st["pending_event"], "NIGHT_END")
        system = chat(st)[1]
        self.assertIn(RIFT_FIRST, system)
        self.assertIn("엇갈림 동안의 서아", system)
        st = judge(st, "[다음 밤]")["state"]
        self.assertEqual((st["day"], st["night_next"], st["crisis"], st["crisis_msgs"]), (6, None, "rift", 0))
        st = judge(st, "…\n…")["state"]
        self.assertEqual(st["crisis_msgs"], 2)


if __name__ == "__main__":
    unittest.main()
