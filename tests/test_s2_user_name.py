"""S2 유저 이름: 썸부터 부르기 · 거절하면 관계가 흔들림 · 크리스마스는 처음부터. 키 없이 돈다.
실행: python -m unittest discover -s tests -v"""
import json
import sys
import unittest
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from db import Store  # noqa: E402
from engine import Engine, clean_user_name, name_josa, normalize_state  # noqa: E402
from llm import FakeLLM  # noqa: E402

PROMPT = str(BASE_DIR / "yoon_seoa_prompt.md")
ASK = "…나 이제 사장님 이름 부르고 싶어. 도윤이라고 부를래."
OLD_UP3 = "알아. 문 닫은 거. …한 잔만 더."
OLD_UP4 = "…너 이름, 아직 안 물어봤네."


class Stub(FakeLLM):
    """판정 JSON 을 정해 두는 LLM. 판정 입력(prompt)을 남겨 둔다."""

    def __init__(self, flags=(), delta=0):
        super().__init__()
        self.verdict = {"affection_delta": delta, "flags": list(flags), "time_advance": False,
                        "time_of_day": "", "summary": "", "reason": ""}
        self.prompt = None

    def complete(self, system, prompt, json_mode=False, budget_sec=None):
        self.prompt = prompt
        return json.dumps(self.verdict, ensure_ascii=False)


def engine(llm=None):
    return Engine(llm=llm or Stub(), store=Store(":memory:"), prompt_path=PROMPT)


def judge(state, flags=(), delta=0):
    return engine(Stub(flags, delta)).judge(state, "…", "…")["state"]


def system_for(state):
    st = normalize_state(state)
    return engine()._build_system(st, st["pending_event"], [])


def at_stage2_ready(**extra):  # 2→3 조건이 찬 2단계
    return {"stage": 2, "affection": 45, "stage_flags": ["outside"], "stage_user_msgs": 20, **extra}


def asked_state():
    st = judge(at_stage2_ready(user_name="도윤"))
    st["pending_event"] = None  # 앱이 답장 뒤 비움
    return judge(st)  # 물은 답장을 판정하는 차례: 아직 asked


class NormalizeTest(unittest.TestCase):
    def test_user_name_clean(self):
        self.assertEqual(clean_user_name("  도윤 \n"), "도윤")
        self.assertEqual(clean_user_name('{도"윤」}「'), "도윤")
        self.assertEqual(clean_user_name("가나다라마바사아자차"), "가나다라마바사아")
        self.assertEqual(clean_user_name(None), "")
        self.assertEqual(clean_user_name(123), "")
        st = normalize_state({})
        self.assertEqual((st["user_name"], st["name_state"], st["judge_soon"]), ("", "none", False))
        self.assertEqual(normalize_state({"name_state": "weird"})["name_state"], "none")

    def test_judge_soon_follows_name_state(self):
        for ns, soon in [("none", False), ("asked", True), ("accepted", False), ("refused", True),
                         ("forgiven", False), ("closed", False)]:
            self.assertEqual(normalize_state({"name_state": ns, "judge_soon": not soon})["judge_soon"], soon, ns)

    def test_josa(self):
        self.assertEqual(name_josa("도윤"), "이라고")
        self.assertEqual(name_josa("민지"), "라고")
        self.assertEqual(name_josa("Alex"), "라고")

    def test_state_lines(self):
        s = system_for({"stage": 1, "user_name": "도윤"})
        self.assertIn("- 유저 이름: 도윤", s)
        self.assertIn("- 이름 부르기: 아직 안 부름", s)
        s = system_for({"stage": 1})
        self.assertIn("- 유저 이름: 없음", s)
        for ns, line in [("accepted", "진지한 순간에 이름"), ("forgiven", "진지한 순간에 이름"),
                         ("refused", "거절당함, 사과를 기다림. 이름 부르지 않음"),
                         ("closed", "다시는 이름 부르지 않음")]:
            self.assertIn(f"- 이름 부르기: {line}", system_for({"stage": 3, "user_name": "도윤", "name_state": ns}))

    def test_tone_lines(self):
        base = engine().blocks["BASE_SYSTEM"]
        self.assertNotIn("이름을 모르면 계속", base)
        self.assertIn("「…아직은 사장님이 편해.」 「친해지면. 아직은 아니고.」", base)
        self.assertIn("본 이야기에서는 현재 상태의 「이름 부르기」를 따른다.", base)


class NameFlowTest(unittest.TestCase):
    def test_stage_up_asks_name(self):
        st = judge(at_stage2_ready(user_name="도윤"))
        self.assertEqual(st["stage"], 3)
        self.assertEqual(st["pending_event"], "STAGE_UP_3")
        self.assertEqual(st["name_state"], "asked")
        self.assertTrue(st["judge_soon"])
        s = system_for(st)
        self.assertIn(ASK, s)
        self.assertIn("문 닫는 시간이 지났는데 일어나지 않는다.", s)
        self.assertNotIn(OLD_UP3, s)

    def test_asked_waits_one_judge_then_accepted(self):
        st = asked_state()
        self.assertEqual(st["name_state"], "asked")  # 유저가 아직 답하지 않음
        st = judge(st)
        self.assertEqual(st["name_state"], "accepted")
        self.assertFalse(st["judge_soon"])

    def test_refuse_goes_down(self):
        st = asked_state()
        st["affection"] = 48
        st = judge(st, ["refuse_name"], delta=-2)
        self.assertEqual(st["stage"], 2)
        self.assertEqual(st["affection"], 36)  # 48 - 2 - 10
        self.assertEqual(st["name_state"], "refused")
        self.assertEqual(st["pending_event"], "NAME_REFUSED")
        self.assertTrue(st["judge_soon"])
        self.assertEqual((st["stage_flags"], st["stage_bonus"]), ([], 0))
        s = system_for(st)
        self.assertIn("…싫어? 그럼 말구.", s)
        self.assertIn("서아가 잔을 내려놓는다. 입꼬리가 잠깐 내려갔다가, 시선이 창밖으로 간다.", s)

    def test_refuse_from_accepted(self):
        st = normalize_state({"stage": 3, "affection": 55, "user_name": "도윤", "name_state": "accepted"})
        st = judge(st, ["refuse_name"])
        self.assertEqual((st["stage"], st["name_state"], st["affection"]), (2, "refused", 45))  # 55 - 10 (2단계 상한 50 안)

    def test_refused_blocks_stage3(self):
        st = judge(at_stage2_ready(affection=50, user_name="도윤", name_state="refused"), ["outside", "care"], 5)
        self.assertEqual(st["stage"], 2)
        self.assertEqual(st["name_state"], "refused")

    def test_apology_forgives(self):
        st = judge(at_stage2_ready(affection=30, user_name="도윤", name_state="refused"), ["apologize"])
        self.assertEqual(st["stage"], 3)
        self.assertEqual(st["affection"], 40)  # 최소 40
        self.assertEqual(st["name_state"], "forgiven")
        self.assertEqual(st["pending_event"], "NAME_FORGIVEN")
        self.assertFalse(st["judge_soon"])
        s = system_for(st)
        self.assertIn("…이번 한 번만 넘어가 줄게. 도윤.", s)
        self.assertNotIn(OLD_UP3, s)
        self.assertNotIn("# 단계가 막 올랐다", s)

    def test_second_refusal_closes(self):
        st = judge({"stage": 3, "affection": 50, "user_name": "도윤", "name_state": "forgiven"}, ["refuse_name"])
        self.assertEqual((st["stage"], st["affection"], st["name_state"]), (2, 25, "closed"))
        self.assertEqual(st["pending_event"], "NAME_REFUSED_AGAIN")
        self.assertIn("…두 번은 안 물어봐, 사장님.", system_for(st))
        st4 = judge({"stage": 4, "affection": 70, "user_name": "도윤", "name_state": "forgiven"}, ["refuse_name"])
        self.assertEqual((st4["stage"], st4["affection"], st4["name_state"]), (4, 45, "closed"))  # 4단계는 그대로

    def test_closed_ignores_name_and_uses_old_scene(self):
        st = judge(at_stage2_ready(user_name="도윤", name_state="closed"), ["refuse_name", "apologize"])
        self.assertEqual(st["stage"], 3)
        self.assertEqual(st["name_state"], "closed")
        self.assertEqual(st["pending_event"], "STAGE_UP_3")
        s = system_for(st)
        self.assertIn(OLD_UP3, s)
        self.assertNotIn("이름 부르고 싶어", s)

    def test_name_flags_need_name_flow(self):
        st = judge({"stage": 2, "affection": 30, "user_name": "도윤"}, ["refuse_name"])
        self.assertEqual((st["stage"], st["affection"], st["name_state"]), (2, 30, "none"))
        self.assertEqual(st["stage_flags"], [])

    def test_judge_prompt_has_name_call(self):
        llm = Stub()
        engine(llm).judge({"stage": 3, "user_name": "도윤", "name_state": "refused"}, "…", "…")
        self.assertIn("이름 부르기: 거절당함, 사과를 기다림. 이름 부르지 않음", llm.prompt)
        js = engine().blocks["JUDGE_SYSTEM"]
        self.assertIn('"refuse_name"', js)
        self.assertIn('"apologize"', js)

    def test_stage_up4_with_name(self):
        st = normalize_state({"stage": 4, "user_name": "도윤", "name_state": "accepted", "pending_event": "STAGE_UP_4"})
        self.assertNotIn(OLD_UP4, system_for(st))


class NoNameTest(unittest.TestCase):
    def test_same_as_before(self):
        st = judge(at_stage2_ready())
        self.assertEqual((st["stage"], st["name_state"], st["judge_soon"]), (3, "none", False))
        s = system_for(st)
        self.assertIn(OLD_UP3, s)
        self.assertNotIn("이름 부르고 싶어", s)
        st4 = normalize_state({"stage": 3})
        Engine._advance(st4, 4, [])
        self.assertIn(OLD_UP4, system_for(st4))
        self.assertIn("- 이름 부르기: 진지한 순간에 이름", system_for(st4))  # 예전처럼 대화에서 알게 된 이름

    def test_flags_without_name_do_nothing(self):
        st = judge({"stage": 3, "affection": 55}, ["refuse_name", "apologize"])
        self.assertEqual((st["stage"], st["affection"], st["name_state"]), (3, 55, "none"))


class XmasTest(unittest.TestCase):
    XMAS = {"stage": 3, "affection": 45, "episode": "s2", "episode_stage": 2, "episode_stage_name": "티키타카",
            "location": "서아의 자취방", "user_name": "도윤", "name_state": "refused"}

    def test_system(self):
        s = system_for(self.XMAS)
        self.assertIn("이 밤에는 처음부터 유저 이름으로 부른다", s)
        self.assertIn("- 유저 이름: 도윤", s)
        self.assertNotIn("이름 부르기:", s)
        self.assertNotIn("마리", s)
        self.assertNotIn("강태오", s)

    def test_no_refusal_in_episode(self):
        llm = Stub(["refuse_name"])
        st = engine(llm).judge(dict(self.XMAS, name_state="accepted"), "…", "…")["state"]
        self.assertEqual((st["stage"], st["name_state"], st["affection"]), (3, "accepted", 45))
        self.assertNotIn("이름 부르기", llm.prompt)


if __name__ == "__main__":
    unittest.main()
