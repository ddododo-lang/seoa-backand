"""S4-1 핵심 서사 진행 보완 (A 필수 갈래 사건 · 관계 상승). 키 없이 돈다(FakeLLM).
실행: python -m unittest discover -s tests -v"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import SCENE_REPLIES_DONE, STAGE_EVENTS, Engine, normalize_state  # noqa: E402
from test_story_routes import REPLY, Stub, engine, judge, system_for  # noqa: E402

THREE = "응\n그래\n알았어"
OUTSIDE_RULE = "바에서 다시 출발하지 않는다."


def step(state, last_user=THREE, last_reply=REPLY, **verdict):
    """판정 한 번 → 앱처럼 사건 답장을 낸 뒤 pending_event 를 비운 state."""
    r = engine(Stub(verdict)).judge(state, last_user, last_reply)
    return r, dict(r["state"], pending_event=None)


def night2(route="tease", **extra):
    """2단계, 2일째 밤(NIGHT_END_2 와 「[다음 밤]」이 지남)."""
    return {"stage": 2, "affection": 20, "route": route, "stage_user_msgs": 6, "day": 2,
            "used_events": {"NIGHT_END_2": 1}, "turn": 5, **extra}


class OutsideTest(unittest.TestCase):  # A1
    def test_outside_alone_does_not_go_up(self):
        st = night2("tease", affection=30, stage_user_msgs=12, stage_flags=["outside"])
        r, st = step(st, flags=["outside"])
        self.assertFalse(r["stage_up"])
        self.assertEqual(r["pending_event"], "DATE")  # 밖에 나가자고 하면 그 갈래의 핵심 사건으로 이음
        for _ in range(2):
            r, st = step(st, affection_delta=1)
        self.assertEqual(r["state"]["stage"], 3)  # DATE 를 마친 뒤에야 오름

    def test_outside_starts_event_before_affection(self):
        r = judge(night2("care", affection=5), flags=["outside"])
        self.assertEqual(r["pending_event"], "WALK_HOME")
        r = judge(night2("care", affection=5))
        self.assertIsNone(r["pending_event"])  # 밖에 나가자는 말이 없으면 호감 15 를 기다림

    def test_outside_on_first_night_waits(self):
        r = judge({"stage": 2, "affection": 30, "route": "tease", "stage_user_msgs": 2}, flags=["outside"])
        self.assertNotEqual(r["pending_event"], "DATE")  # 2단계 핵심 사건은 다음 밤에

    def test_already_outside_not_restarted_from_bar(self):
        for route, event in (("tease", "DATE"), ("care", "WALK_HOME")):
            st = normalize_state(night2(route, location="네온 골목", pending_event=event))
            self.assertIn(OUTSIDE_RULE, system_for(st))
            self.assertIn("바 밖(네온 골목)", system_for(st))
            st = normalize_state(night2(route, pending_event=event))  # 바 안이면 없음
            self.assertNotIn(OUTSIDE_RULE, system_for(st))
        self.assertIn("ROUTE_OUTSIDE", engine().blocks)


class RouteDefaultTest(unittest.TestCase):  # A2
    def test_no_route_server_picks_and_never_stuck(self):
        r, st = step(night2(None, affection=16))
        self.assertEqual((st["route"], r["pending_event"]), ("care", "WALK_HOME"))  # 동점·없음은 care
        r, st = step(night2(None, affection=16, stage_flags=["tease"]))
        self.assertEqual((st["route"], r["pending_event"]), ("tease", "DATE"))
        st = night2(None, affection=24, stage_user_msgs=9)
        for _ in range(4):
            r, st = step(st, affection_delta=1)
        self.assertEqual(st["stage"], 3)  # 갈래가 끝까지 안 와도 진행

    def test_route_never_changed(self):
        r = judge(night2("tease", affection=20, stage_flags=["care"]))
        self.assertEqual((r["state"]["route"], r["pending_event"]), ("tease", "DATE"))


class SceneDoneTest(unittest.TestCase):  # A3
    def test_reservation_alone_is_not_done(self):
        r = judge(night2("tease"))
        st = r["state"]  # 앱이 아직 사건 답장을 내지 않음(pending 그대로)
        self.assertEqual((st["scene"], st["scene_msgs"]), ("DATE", 0))
        st = judge(dict(st, affection=40, stage_user_msgs=20), THREE)["state"]
        self.assertEqual((st["scene"], st["scene_msgs"], st["stage"]), ("DATE", 0, 2))

    def test_fallback_replies_not_counted(self):
        _, st = step(night2("care"))
        _, st = step(st, "응", "…")  # 시작 답장이 예비 지문
        self.assertEqual((st["scene"], st["scene_msgs"]), ("WALK_HOME", 0))
        _, st = step(st, THREE, "…")  # 묶음의 마지막 답장이 예비 지문 → 하나 뺌
        self.assertEqual(st["scene_msgs"], 2)
        _, st = step(st, "응", REPLY)
        self.assertEqual(st["scene_msgs"], 3)
        r, st = step(st, "응", REPLY)
        self.assertIsNone(st["scene"])
        self.assertIn("갈래 장면 마침: WALK_HOME", r["changes"])
        self.assertEqual(SCENE_REPLIES_DONE, 4)

    def test_location_moves_on_first_real_scene_reply(self):
        st = normalize_state({"stage": 3, "route": "tease", "scene": "STORM", "used_events": {"ROUTE_STORM": 1}})
        out = engine().reply(st, "응", [], [])
        self.assertEqual(out["location"], "바 '라스트 오더' 2층")
        out = engine().reply(dict(st, scene_msgs=2, location="바 '라스트 오더' 1층"), "응", [], [])
        self.assertIsNone(out["location"])  # 이미 시작된 장면은 모델에 맡김

    def test_no_random_event_during_scene(self):
        st = dict(night2("tease"), scene="DATE", scene_msgs=1, used_events={"NIGHT_END_2": 1, "ROUTE_DATE": 4},
                  short_streak=9, turns_since_event=99)
        r = judge(st, "응")
        self.assertIsNone(r["pending_event"])

    def test_name_scene_drop_restarts_scene(self):
        st = {"stage": 3, "affection": 48, "route": "care", "stage_user_msgs": 5, "user_name": "도윤",
              "name_state": "accepted", "used_events": {"NIGHT_END_3": 1, "ROUTE_ROOFTOP": 4},
              "scene": "ROOFTOP", "scene_msgs": 2, "turn": 6}
        r, st = step(st, "부르지 마", flags=["refuse_name"])
        self.assertEqual((st["stage"], st["scene"], r["pending_event"]), (2, None, "NAME_REFUSED"))
        self.assertNotIn("ROUTE_ROOFTOP", st["used_events"])  # 마치지 못한 장면은 마친 것이 아님
        r, st = step(st, "미안해", flags=["apologize"])
        self.assertEqual((st["stage"], st["name_state"]), (3, "forgiven"))
        for _ in range(3):  # 이름 장면 뒤 쉬는 메시지가 지나면 다시 시작
            r, st = step(st, "응")
            if r["pending_event"] == "ROOFTOP":
                break
        self.assertEqual(r["pending_event"], "ROOFTOP")

    def test_name_event_does_not_overwrite_core_event(self):
        st = {"stage": 5, "affection": 60, "user_name": "도윤", "name_state": "accepted", "route": "care",
              "crisis": "rift", "pending_event": "RECONCILE_OPEN", "used_events": {"RIFT_START": 1}}
        r = judge(st, "부르지 마", flags=["refuse_name"])
        self.assertEqual(r["pending_event"], "RECONCILE_OPEN")
        st = {"stage": 5, "affection": 60, "user_name": "도윤", "name_state": "refused", "route": "tease",
              "crisis": "on", "pending_event": "CRISIS", "used_events": {"CRISIS": 3}, "turn": 3}
        r = judge(st, "미안", flags=["apologize"])
        self.assertEqual((r["pending_event"], r["state"]["name_state"]), ("CRISIS", "forgiven"))


def full_route(route):
    """2일째 밤부터 위기까지 판정만으로 진행해 본 사건 순서."""
    st = night2(route, affection=20, stage_user_msgs=3)
    seen = []
    for _ in range(60):
        r, st = step(st, affection_delta=3, flags=["sincere"])
        if r["pending_event"]:
            seen.append(r["pending_event"])
        if r["pending_event"] == "NIGHT_END":
            r, st = step(st, "잘 가")
            r, st = step(st, "[다음 밤]")
            seen.append("[다음 밤]")
            if r["pending_event"]:
                seen.append(r["pending_event"])
        if st["crisis"] == "on":
            return seen, st
    raise AssertionError(seen)


class OrderTest(unittest.TestCase):  # A3·A4
    def test_both_routes_to_crisis(self):
        for route, (e2, e3) in (("tease", ("DATE", "STORM")), ("care", ("WALK_HOME", "ROOFTOP"))):
            seen, st = full_route(route)
            want = [e2, "STAGE_UP_3", "NIGHT_END", "[다음 밤]", e3, "STAGE_UP_4", "ALLEY", "CRISIS"]
            self.assertEqual([e for e in seen if e in want], want, route)
            self.assertEqual((st["stage"], st["route"]), (5, route))

    def test_no_stage_up_before_night_passes(self):
        st = {"stage": 3, "affection": 60, "route": "care", "stage_user_msgs": 20, "stage_flags": ["sincere"],
              "used_events": {"NIGHT_END_3": 1, "ROUTE_ROOFTOP": 1}, "night_next": "NIGHT_START_3_CARE"}
        self.assertIsNone(Engine._next_stage(normalize_state(st)))
        self.assertEqual(Engine._next_stage(normalize_state(dict(st, night_next=None))), 4)


class LoversTest(unittest.TestCase):  # B4 (연인 단계 사건)
    def test_no_fake_lover_in_stage6(self):
        self.assertNotIn("DATE", STAGE_EVENTS[6])
        e = engine()
        for _ in range(40):
            st = {"stage": 6, "route": "tease", "ending": "stayed", "short_streak": 9, "turn": 100,
                  "used_events": {"ROUTE_DATE": 1, "ROUTE_STORM": 2}}
            ev = e._tick_events(normalize_state(st), "응")
            self.assertNotIn(ev, ("DATE", "STORM"))  # 겪은 갈래 장면은 같은 원고로 다시 안 나옴


if __name__ == "__main__":
    unittest.main()
