"""S8: 순간 선물을 장면에 연결. 키 없이 돈다(FakeLLM).
실행: python -m unittest discover -s tests -v"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import MOMENT_AFTER_ALLEY, MOMENT_SCENES, normalize_state  # noqa: E402
from test_s4_1_story import NEWS, crisis_start, step  # noqa: E402
from test_s7_lovers import ASK, asked, won  # noqa: E402
from test_story_routes import REPLY, Stub, engine, system_for  # noqa: E402

M = [{"id": "firstaid", "name": "구급상자", "when": "서아가 다쳐서 온 밤"},
     {"id": "towel", "name": "따뜻한 수건", "when": "비에 젖어 들어왔을 때"},
     {"id": "flower", "name": "바 여분 열쇠", "when": "그 골목 이야기를 마친 뒤"},
     {"id": "umbrella", "name": "우산", "when": "위기 소식을 꺼냈을 때"},
     {"id": "ring", "name": "반지", "when": "서아가 관계를 물을 때"}]
RING = "[유저가 선물함: 반지] · 순간 선물"


def without(*ids):
    return [g for g in M if g["id"] not in ids]


def gift(state, msg="응", model=None):
    """서아 답장 하나를 만들고 gift_moment 를 돌려줌. model: 모델이 낸 gift_moment."""
    out = engine(Stub(reply={"gift_moment": model})).reply(dict({"gift_moments": M}, **state), msg, [], [])
    return out["gift_moment"]


def gid(state, msg="응", model=None):
    g = gift(state, msg, model)
    return g and g["id"]


def night3(route="care", **extra):
    return {"stage": 3, "affection": 30, "route": route, "night_next": f"NIGHT_START_3_{route.upper()}",
            "used_events": {"NIGHT_END_3": 4}, "turn": 6, **extra}


def storm(**extra):
    return {"stage": 3, "affection": 30, "route": "tease", "pending_event": "STORM", "scene": "STORM",
            "used_events": {"ROUTE_STORM": 6, "STORM": 6}, "turn": 7, **extra}


def alley_last(affection=50, **extra):
    """4단계 그 골목, 마침 직전(정상 답장 3개가 지남)."""
    return {"stage": 4, "affection": affection, "route": "care", "scene": "ALLEY", "scene_msgs": 3,
            "stage_user_msgs": 6, "used_events": {"ROUTE_ALLEY": 3, "ALLEY": 3}, "turn": 8, "gift_moments": M, **extra}


class TowelTest(unittest.TestCase):
    def test_night_start_3_both_routes(self):
        for route in ("tease", "care"):
            self.assertEqual(gid(night3(route), "[다음 밤]"), "towel", route)

    def test_night_start_2_no_towel(self):
        st = {"stage": 2, "affection": 15, "night_next": "NIGHT_START_2", "used_events": {"NIGHT_END_2": 3}}
        self.assertIsNone(gid(st, "[다음 밤]"))

    def test_storm_start(self):
        self.assertEqual(gid(storm()), "towel")
        self.assertIsNone(gid(dict(storm(), pending_event=None, scene_msgs=1)))  # 시작 답장만

    def test_night_then_storm_once(self):
        r = engine(Stub()).judge(dict(night3("tease"), gift_moments=M), "[다음 밤]", "*문 종이 울린다.* …")
        self.assertIn("MOMENT_towel", r["state"]["used_events"])
        st = dict(storm(), used_events=dict(r["state"]["used_events"], ROUTE_STORM=9, STORM=9), turn=10)
        self.assertIsNone(gid(st))

    def test_storm_judge_records(self):
        st = dict(storm(), gift_moments=M, turn=7, pending_event=None)
        r = engine(Stub()).judge(st, "응", REPLY)
        self.assertIn("MOMENT_towel", r["state"]["used_events"])

    def test_storm_not_in_list_not_recorded(self):
        st = dict(storm(), gift_moments=without("towel"), pending_event=None)
        self.assertIsNone(gid(dict(st, gift_moments=without("towel"), pending_event="STORM")))
        r = engine(Stub()).judge(st, "응", REPLY)
        self.assertNotIn("MOMENT_towel", r["state"]["used_events"])  # 열지 않았으니 다음 장면에서 열 수 있음
        self.assertEqual(gid(night3(), "[다음 밤]"), "towel")


class FlowerTest(unittest.TestCase):
    def test_after_alley_once(self):
        r, st = step(alley_last(), "응", REPLY)
        self.assertIsNone(st["scene"])
        self.assertIn(MOMENT_AFTER_ALLEY, st["used_events"])
        self.assertTrue(st["judge_soon"])  # 다음 답장 하나만
        self.assertEqual(gid(st), "flower")
        r, st = step(st, "고마워", REPLY)
        self.assertNotIn(MOMENT_AFTER_ALLEY, st["used_events"])
        self.assertIn("MOMENT_flower", st["used_events"])
        self.assertIsNone(gid(st))

    def test_not_before_done(self):
        self.assertIsNone(gid(dict(alley_last(), scene_msgs=2)))

    def test_not_in_list(self):
        _, st = step(alley_last(gift_moments=without("flower")), "응", REPLY)
        self.assertIsNone(gid(dict(st, gift_moments=without("flower"))))
        _, st = step(dict(st, gift_moments=without("flower")), "응", REPLY)
        self.assertNotIn(MOMENT_AFTER_ALLEY, st["used_events"])  # 그 답장 한 번뿐
        self.assertNotIn("MOMENT_flower", st["used_events"])

    def test_crisis_same_judge_umbrella_first(self):
        r, st = step(alley_last(affection=60), "응", REPLY)  # 골목 마침과 같은 판정에서 5단계·위기
        self.assertEqual((st["stage"], r["pending_event"]), (5, "CRISIS"))
        self.assertEqual(gid(dict(st, pending_event="CRISIS")), "umbrella")
        _, st = step(st, "응", NEWS)
        self.assertIn("MOMENT_umbrella", st["used_events"])
        self.assertIn(MOMENT_AFTER_ALLEY, st["used_events"])  # 소식 답장은 우산이 차지 → 열쇠는 다음 답장
        self.assertEqual(gid(st), "flower")
        _, st = step(st, "가지 마", "*고개를 숙인다.* …")
        self.assertIn("MOMENT_flower", st["used_events"])
        self.assertNotIn(MOMENT_AFTER_ALLEY, st["used_events"])


class UmbrellaTest(unittest.TestCase):
    def test_crisis_reply(self):
        for route in ("tease", "care"):
            st = dict(crisis_start(route), gift_moments=M)
            self.assertEqual(gid(dict(st, pending_event="CRISIS")), "umbrella", route)

    def test_news_confirmed_records_once(self):
        _, st = step(dict(crisis_start(), gift_moments=M), "응", NEWS)
        self.assertTrue(st["crisis_news"])
        self.assertIn("MOMENT_umbrella", st["used_events"])
        self.assertIsNone(gid(dict(st, pending_event="CRISIS")))

    def test_news_missing_reopens(self):
        r, st = step(dict(crisis_start(), gift_moments=M), "응", "*잔을 닦는다.* …그래.")
        self.assertEqual(r["pending_event"], "CRISIS")
        self.assertNotIn("MOMENT_umbrella", st["used_events"])
        self.assertEqual(gid(dict(st, pending_event="CRISIS")), "umbrella")  # 다시 넣는 소식 답장과 함께


class RingTest(unittest.TestCase):
    def test_lovers_ask_reply(self):
        for route in ("tease", "care"):
            st = won(route, gift_moments=M)
            self.assertEqual(gid(dict(st, pending_event="LOVERS_ASK")), "ring", route)

    def test_ask_shown_records(self):
        _, st = step(won("care", gift_moments=M), "응", "*본다.* " + ASK["care"])
        self.assertIn("MOMENT_ring", st["used_events"])
        self.assertIsNone(gid(dict(st, pending_event="LOVERS_ASK")))

    def test_ring_given_is_yes(self):
        for moments in (M, without("ring")):  # 건넨 뒤 앱 목록에서 빠져도
            st = dict(asked("care"), gift_moments=moments)
            r = engine(Stub()).judge(st, RING, "*받는다.* …")
            self.assertEqual((r["state"]["stage"], r["state"]["lovers"]), (6, "done"))
            self.assertIn("lovers_yes", r["flags"])

    def test_ring_given_in_wait(self):
        r = engine(Stub()).judge(dict(asked("tease"), lovers="wait"), RING, "*받는다.* …")
        self.assertEqual(r["state"]["stage"], 6)

    def test_ring_elsewhere_no_effect(self):
        r = engine(Stub()).judge({"stage": 4, "affection": 50, "route": "care"}, RING, REPLY)
        self.assertNotIn("lovers_yes", r["flags"])
        self.assertEqual(r["state"]["stage"], 4)
        r = engine(Stub()).judge(dict(asked("care"), gift_moments=M), "[유저가 선물함: 반지]", "*받는다.* …")
        self.assertEqual(r["state"]["stage"], 5)  # 순간 선물이 아니면 판정 모델에 맡김


class PriorityTest(unittest.TestCase):
    def test_scene_beats_model(self):
        self.assertEqual(gid(storm(), model={"id": "firstaid", "reason": "다쳤어요"}), "towel")
        self.assertEqual(gid(storm(), model=None), "towel")

    def test_same_id_keeps_reason(self):
        self.assertEqual(gift(storm(), model={"id": "towel", "reason": "흠뻑 젖었어요"}),
                         {"id": "towel", "reason": "흠뻑 젖었어요"})
        self.assertEqual(gift(storm()), {"id": "towel", "reason": "따뜻한 수건"})

    def test_not_in_list_keeps_model_path(self):
        st = dict(storm(), gift_moments=without("towel"))
        self.assertIsNone(engine(Stub()).reply(st, "응", [], [])["gift_moment"])
        out = engine(Stub(reply={"gift_moment": {"id": "firstaid", "reason": "다쳤어요"}})).reply(st, "응", [], [])
        self.assertEqual(out["gift_moment"]["id"], "firstaid")  # 모델이 스스로 내는 경로는 그대로

    def test_used_does_not_reopen(self):
        st = dict(storm(), used_events={"ROUTE_STORM": 6, "STORM": 6, "MOMENT_towel": 3})
        self.assertIsNone(gid(st))

    def test_recent_gift_quiet(self):
        history = [{"r": "u", "t": "[유저가 선물함: 구급상자] · 순간 선물"}, {"r": "c", "t": "…고마워."}]
        out = engine(Stub()).reply(dict(storm(), gift_moments=M), "응", history, [])
        self.assertIsNone(out["gift_moment"])  # 방금 순간 선물을 줬으면 쉼(앱 목록이 비워짐)

    def test_other_moments_unchanged(self):
        self.assertEqual(set(MOMENT_SCENES), {"towel", "flower", "umbrella", "ring"})
        self.assertIsNone(gid({"stage": 1, "pending_event": "INJURY"}))


class PromptTest(unittest.TestCase):
    def test_gift_moment_whens(self):
        b = engine().blocks["GIFT_MOMENT"]
        for line in ("며칠 뒤 밤, 비에 젖어 들어왔을 때 · 폭우 장면(따뜻한 수건)", "그 골목 이야기를 마친 뒤(바 여분 열쇠)",
                     "위기 소식을 꺼냈을 때(우산)", "서아가 관계를 물을 때(반지)"):
            self.assertIn(line, b)

    def test_judge_line(self):
        self.assertIn("서아가 관계를 물은 뒤 유저가 반지를 건네면 lovers_yes 다.", engine().blocks["JUDGE_SYSTEM"])

    def test_lovers_watch_line(self):
        line = ("유저가 반지를 건네며 대답하면 원고 2절(갈래별)로 받되, 지문에 반지를 받는 대목"
                "(반지를 내려다보다가 손가락에 끼운다 등)을 한 문장 넣는다.")
        self.assertIn(line, system_for(asked("care")))


class ChristmasTest(unittest.TestCase):
    def test_episode_never_opens(self):
        st = {"episode": "s2", "episode_stage": 2, "pending_event": "STORM", "gift_moments": M,
              "used_events": {MOMENT_AFTER_ALLEY: 1}}
        self.assertIsNone(gid(st))
        r = engine(Stub()).judge(st, "응", REPLY)
        self.assertFalse(any(k.startswith("MOMENT_") and k != MOMENT_AFTER_ALLEY for k in r["state"]["used_events"]))
        self.assertIsNone(gid({"episode": "s2", "episode_stage": 2}, RING))

    def test_normalize_keeps_used(self):
        st = normalize_state({"used_events": {"MOMENT_towel": 2}})
        self.assertEqual(st["used_events"]["MOMENT_towel"], 2)


if __name__ == "__main__":
    unittest.main()
