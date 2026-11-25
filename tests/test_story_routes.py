"""갈래·실패 엔딩·순간 선물·대화량 (feat/story-routes) 키 없는 테스트. 실행: python -m unittest discover -s tests -v"""
import json
import sys
import unittest
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from db import Store  # noqa: E402
from engine import Engine, normalize_state  # noqa: E402
from llm import FakeLLM  # noqa: E402

PROMPT = str(BASE_DIR / "yoon_seoa_prompt.md")
LONG = ["오늘은 일부러 일찍 문 열었어. 네가 비 맞고 올까 봐 수건도 데워 놨고, 늘 마시던 거 미리 준비해 뒀어.",
        "그 상처 아직 아프지? 무리하지 말고 오늘은 그냥 여기서 쉬어. 내가 옆에서 아무 말 안 하고 있을게.",
        "솔직히 말하면 네가 안 오는 날은 하루 종일 이 자리만 계속 보게 돼. 그러니까 다치지 말고, 매일 와 줘."]


class Stub(FakeLLM):
    """판정 JSON·답장 JSON 을 정해 두는 LLM."""

    def __init__(self, verdict=None, reply=None):
        super().__init__()
        self.verdict = {"affection_delta": 0, "flags": [], "time_advance": False, "time_of_day": "",
                        "summary": "", "reason": "", **(verdict or {})}
        self.reply_data = {"action": "잔을 내려놓는다.", "dialogue": "…그래.", "motion_id": None,
                           "emotion": "평온", "location": None, **(reply or {})}
        self.system = None

    def complete(self, system, prompt, json_mode=False, budget_sec=None):
        return json.dumps(self.verdict, ensure_ascii=False) if json_mode else "요약"

    def chat_json(self, system, messages, schema, budget_sec=None):
        self.system = system
        return json.dumps(self.reply_data, ensure_ascii=False)


def engine(llm=None):
    return Engine(llm=llm or Stub(), store=Store(":memory:"), prompt_path=PROMPT)


def judge(state, last_user="…", **verdict):
    return engine(Stub(verdict)).judge(state, last_user, "…")


def system_for(state):
    st = normalize_state(state)
    return engine()._build_system(st, st["pending_event"], [])


class RouteNormalizeTest(unittest.TestCase):  # 1
    def test_only_two_values(self):
        self.assertEqual([normalize_state({"route": r})["route"] for r in ("tease", "care", "x", None)],
                         ["tease", "care", None, None])

    def test_server_never_changes_route(self):
        self.assertEqual(judge({"stage": 2, "affection": 41, "route": "care"})["state"]["route"], "care")


N2, N3 = {"NIGHT_END_2": 0}, {"NIGHT_END_3": 0}  # S4-C: 2·3단계 갈래 장면은 밤을 넘긴 뒤 (판정이 used_events 를 고치므로 dict() 로 복사해 씀)


class RouteEventsTest(unittest.TestCase):  # 2
    def test_stage2_by_route(self):
        self.assertEqual(judge({"stage": 2, "affection": 14, "route": "tease", "used_events": dict(N2)}, affection_delta=1)["pending_event"], "DATE")
        self.assertEqual(judge({"stage": 2, "affection": 15, "route": "care", "used_events": dict(N2)})["pending_event"], "WALK_HOME")

    def test_stage2_needs_15_and_no_outside(self):  # S4: 15
        self.assertNotIn(judge({"stage": 2, "affection": 14, "route": "care"})["pending_event"], ("DATE", "WALK_HOME"))
        self.assertNotIn(judge({"stage": 2, "affection": 45, "route": "care", "stage_flags": ["outside"]})["pending_event"],
                         ("DATE", "WALK_HOME"))

    def test_stage3_and_4(self):
        self.assertEqual(judge({"stage": 3, "affection": 55, "route": "tease", "used_events": dict(N3)})["pending_event"], "STORM")
        self.assertEqual(judge({"stage": 3, "affection": 55, "route": "care", "used_events": dict(N3)})["pending_event"], "ROOFTOP")
        for route in ("tease", "care"):
            self.assertEqual(judge({"stage": 4, "affection": 65, "route": route})["pending_event"], "ALLEY")

    def test_once_per_route_event(self):
        first = judge({"stage": 2, "affection": 41, "route": "tease", "used_events": dict(N2)})["state"]
        self.assertEqual(first["pending_event"], "DATE")
        again = judge(dict(first, pending_event=None, scene=None, scene_judges_left=0))
        self.assertNotEqual(again["pending_event"], "DATE")

    def test_other_route_events_never_random(self):
        e = engine(Stub())
        for route, banned in (("tease", "ROOFTOP"), ("care", "STORM")):
            for _ in range(40):
                st = normalize_state({"stage": 3, "affection": 10, "route": route, "short_streak": 5,
                                      "used_events": {"ROUTE_STORM": 0, "ROUTE_ROOFTOP": 0}})
                ev = e._tick_events(st, "응")
                self.assertNotIn(ev, ("STORM", "ROOFTOP", "DATE"))

    def test_null_route_keeps_old_rules(self):
        self.assertIsNone(Engine._route_event(normalize_state({"stage": 2, "affection": 45})))
        st = normalize_state({"stage": 3})
        Engine._advance(st, 4, [])
        self.assertIn("비밀1", st["revealed_secrets"])  # 예전처럼 4단계에 오르면 공개

    def test_alley_reveals_secret_once(self):
        st = normalize_state({"stage": 3, "route": "care"})
        Engine._advance(st, 4, [])
        self.assertNotIn("비밀1", st["revealed_secrets"])  # 갈래가 있으면 「그 골목」 몫
        r = judge(dict(st, affection=65, pending_event=None))
        self.assertEqual((r["pending_event"], r["state"]["revealed_secrets"]), ("ALLEY", ["비밀1"]))
        self.assertIn("배웅해 줬던 날", system_for(r["state"]))  # care 갈래만
        tease = judge({"stage": 4, "affection": 65, "route": "tease"})["state"]
        self.assertNotIn("배웅해 줬던 날", system_for(tease))

    def test_scene_lasts_two_judges(self):
        st = judge({"stage": 2, "affection": 41, "route": "tease", "used_events": dict(N2)})["state"]
        st["pending_event"] = None  # 앱이 답장 뒤 비움
        self.assertIn("위장 연인 부탁", system_for(st))
        st = judge(st)["state"]
        self.assertIn("위장 연인 부탁", system_for(dict(st, pending_event=None)))
        st = judge(st)["state"]
        self.assertIsNone(st["scene"])

    def test_pursue_and_morning_blocks(self):
        self.assertIn('"네온 골목"', system_for({"stage": 5, "route": "tease", "departure_days_left": 2}))
        self.assertIn('"바 옥상"', system_for({"stage": 5, "route": "care", "departure_days_left": 2}))
        self.assertIn("셔츠, 빌렸어", system_for({"stage": 6, "route": "tease"}))
        self.assertIn("해 뜨는 것도, 처음 봐", system_for({"stage": 6, "route": "care"}))
        self.assertNotIn("붙잡기 (", system_for({"stage": 5, "departure_days_left": 2}))  # 갈래 없으면 없음

    def test_event_locations(self):
        for ev, loc in (("ROOFTOP", "바 옥상"), ("ALLEY", "비 내리는 골목")):
            out = engine(Stub()).reply({"stage": 4, "route": "care", "pending_event": ev}, "…", [], [])
            self.assertEqual(out["location"], loc)


class DepartureTest(unittest.TestCase):  # 3·6 (S4: 남은 날·실패 엔딩 대신 위기 → 연인 / 엇갈림 → 화해, tests/test_s4_crisis.py)
    def test_no_days_left_any_more(self):
        st = normalize_state({"stage": 4})
        Engine._advance(st, 5, [])
        self.assertEqual((st["departure_days_left"], st["pending_event"]), (None, "CRISIS"))
        r = judge({"stage": 5, "affection": 80, "departure_days_left": 3}, time_advance=True)
        self.assertIsNone(r["state"]["departure_days_left"])
        self.assertNotIn(r["pending_event"], ("DAYS_LEFT_2", "DAYS_LEFT_1", "LAST_NIGHT"))

    def test_rewind_state_continues(self):
        st = normalize_state({"stage": 5, "affection": 76, "departure_days_left": 3, "ending": None, "route": "care"})
        self.assertEqual((st["crisis"], st["ending"]), ("on", None))  # 예전 5단계 저장은 위기 중으로
        r = judge(st, "가지 마.", flags=["pursue"])
        self.assertEqual(r["state"]["stage"], 6)

    def test_rewind_rule_and_strict_day(self):
        e = engine()
        self.assertIn("「…다시, 그 밤.」", e.blocks["BASE_SYSTEM"])
        self.assertIn("5단계(엇갈림)에서는 장면이 분명히 다음 날로 넘어갈 때만", e.blocks["JUDGE_SYSTEM"])


class GiftMomentTest(unittest.TestCase):  # 4
    M = [{"id": "firstaid", "name": "구급상자", "when": "서아가 다쳐서 왔을 때"},
         {"id": "cocktail", "name": "칵테일", "when": "지쳐 보일 때"}]

    def reply(self, gm, history=(), msg="괜찮아?", moments=None):
        llm = Stub(reply={"gift_moment": gm})
        out = engine(llm).reply({"stage": 2, "gift_moments": self.M if moments is None else moments}, msg, list(history), [])
        return out["gift_moment"], llm.system

    def test_normalize(self):
        st = normalize_state({"gift_moments": [{"id": "a", "name": "A", "when": "w"}, {"name": "id 없음"}, "x"]})
        self.assertEqual([g["id"] for g in st["gift_moments"]], ["a"])
        self.assertEqual(normalize_state({})["gift_moments"], [])

    def test_in_list_only(self):
        self.assertEqual(self.reply({"id": "firstaid", "reason": "서아가 다쳐서 왔어요"})[0]["id"], "firstaid")
        self.assertIsNone(self.reply({"id": "ring", "reason": "x"})[0])
        gm, system = self.reply({"id": "firstaid", "reason": "x"}, moments=[])
        self.assertIsNone(gm)
        self.assertNotIn("순간 선물 (지금 열 수 있는 것)", system)

    def test_gap(self):
        given = [{"r": "u", "t": "[유저가 선물함: 구급상자] · 순간 선물"}]
        five = given + [{"r": "u", "t": f"말 {i}"} for i in range(5)]
        ten = given + [{"r": "u", "t": f"말 {i}"} for i in range(10)]
        self.assertIsNone(self.reply({"id": "firstaid", "reason": "x"}, five)[0])  # 같은 선물 10개 안
        self.assertEqual(self.reply({"id": "cocktail", "reason": "x"}, five)[0]["id"], "cocktail")  # 다른 선물은 됨
        self.assertIsNone(self.reply({"id": "cocktail", "reason": "x"}, given)[0])  # 방금 줬으면 잠깐 쉼
        self.assertEqual(self.reply({"id": "firstaid", "reason": "x"}, ten)[0]["id"], "firstaid")

    def test_schema(self):
        from engine import FALLBACK, REPLY_SCHEMA
        self.assertTrue(REPLY_SCHEMA["properties"]["gift_moment"]["nullable"])
        self.assertIsNone(FALLBACK["gift_moment"])


class TalkBonusTest(unittest.TestCase):  # 5
    def bonus(self, msgs, delta=1, flags=("care",), stage_bonus=0):
        r = judge({"stage": 2, "affection": 20, "stage_bonus": stage_bonus}, "\n".join(msgs),
                  affection_delta=delta, flags=list(flags))
        return r["progress"]["talk_bonus"], r

    def test_rules(self):  # S3: 길이 보너스를 없앰. 긴 메시지도 언제나 0
        self.assertEqual(self.bonus(["응", "뭐해?", "ㅋㅋ"])[0], 0)
        self.assertEqual(self.bonus(LONG)[0], 0)
        self.assertEqual(self.bonus(LONG, flags=())[0], 0)
        self.assertEqual(self.bonus(LONG[:2] + ["응"])[0], 0)
        self.assertEqual(self.bonus(LONG, delta=-1)[0], 0)
        _, r = self.bonus(LONG, delta=2)
        self.assertEqual(r["state"]["affection"], 22)  # 판정 점수만

    def test_cap_per_stage_and_reset(self):
        b, r = self.bonus(LONG, stage_bonus=7)
        self.assertEqual((b, r["state"]["stage_bonus"]), (0, 7))
        b, r = self.bonus(LONG, stage_bonus=12)  # 예전 앱이 상한보다 큰 값을 보내도 감점 없음
        self.assertEqual((b, r["state"]["affection"]), (0, 21))
        st = normalize_state({"stage": 1, "stage_bonus": 8, "stage_user_msgs": 9})
        Engine._advance(st, 2, [])
        self.assertEqual((st["stage_bonus"], st["stage_user_msgs"]), (0, 0))

    def test_no_penalty_and_cap_respected(self):
        _, r = self.bonus(["응"] * 3, delta=0)
        self.assertEqual(r["state"]["affection"], 20)
        r = judge({"stage": 1, "affection": 30}, "\n".join(LONG), affection_delta=0, flags=["care"])
        self.assertEqual(r["state"]["affection"], 30)  # 1단계 상한 30 그대로

    def test_min_messages_and_progress(self):
        base = {"stage": 2, "affection": 45, "stage_flags": ["outside"], "stage_user_msgs": 7}  # S4: 2→3 최소 9개
        r = judge(base, "한 마디")
        self.assertFalse(r["stage_up"])
        self.assertEqual(r["progress"]["min_msgs_left"], 1)
        r = judge(dict(r["state"], pending_event=None), "한 마디 더")
        self.assertTrue(r["stage_up"])
        ep = judge({"stage": 2, "episode": "s2", "episode_stage": 2, "location": "크리스마스 거리"}, "\n".join(LONG))
        self.assertIsNone(ep["progress"])  # 에피소드는 안 셈


class XmasLateTest(unittest.TestCase):  # 9
    BASE = {"stage": 3, "episode": "s2", "episode_stage": 3, "episode_stage_name": "썸", "location": "크리스마스 거리"}

    def hint(self, left, stage=3, history=()):
        llm = Stub()
        engine(llm).reply(dict(self.BASE, episode_turns_left=left, episode_stage=stage), "…", list(history), [])
        return llm.system.split("한 줄을 넘지 않는다): ")[1].split("\n")[0] if "자정이 가까워진다" in llm.system else None

    def test_normalize(self):
        self.assertEqual(normalize_state(dict(self.BASE, episode_turns_left="8"))["episode_turns_left"], 8)
        self.assertEqual(normalize_state(dict(self.BASE, episode_turns_left=-3))["episode_turns_left"], 0)
        self.assertIsNone(normalize_state(dict(self.BASE, episode_turns_left="x"))["episode_turns_left"])
        self.assertIsNone(normalize_state({"episode_turns_left": 8})["episode_turns_left"])  # 에피소드 아니면 없음

    def test_bands_once_and_stage4(self):
        self.assertIsNone(self.hint(11))
        self.assertEqual(self.hint(8), "「…밤 꽤 깊었네.」")
        self.assertIsNone(self.hint(7, history=[{"r": "c", "t": "…밤 꽤 깊었네."}]))
        self.assertEqual(self.hint(4), "「슬슬 들어가야겠다.」")
        self.assertIn("장갑을 끼며", self.hint(1))
        self.assertIsNone(self.hint(2, stage=4))


if __name__ == "__main__":
    unittest.main()
