"""S4-1 핵심 서사 진행 보완 (A 필수 갈래 사건 · 관계 상승 · B 위기·엇갈림·화해 · C 단계 기억·안정성). 키 없이 돈다(FakeLLM).
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
        out = engine(Stub(reply={"action": "당신을 따라 계단을 오른다."})).reply(st, "응", [], [])  # S6: 이동 대목이 있을 때
        self.assertEqual(out["location"], "바 '라스트 오더' 2층")
        out = engine().reply(dict(st, scene_msgs=3, location="바 '라스트 오더' 1층"), "응", [], [])
        self.assertIsNone(out["location"])  # 이미 진행된 장면(세 번째 답장 뒤)은 모델에 맡김

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


class PromptStub(Stub):
    """판정 입력(prompt)을 남겨 두는 Stub."""

    def complete(self, system, prompt, json_mode=False, budget_sec=None):
        self.prompt = prompt
        return super().complete(system, prompt, json_mode, budget_sec)


WON_CARE = "*난간에서 내려온다.* …내려가자. 같이."
NEWS = "*휴대폰 화면을 끈다.* 태오야. 큰 건. 도시 밖. …새벽에 떠나래."  # S6: 위기 소식 답장(두 갈래 확인 기준을 모두 포함)
RECONCILE = "*몸을 돌린다.* …늦었잖아. 그래도, 왔으니까."


def crisis_start(route="care"):
    """4→5 판정을 지나 위기 소식(CRISIS)이 예약된 state."""
    r = judge({"stage": 4, "affection": 60, "stage_user_msgs": 6, "route": route, "used_events": {"ROUTE_ALLEY": 1},
               "turn": 9}, flags=["sincere"])  # 위기 전 진심
    assert r["state"]["stage"] == 5 and r["pending_event"] == "CRISIS"
    return dict(r["state"], pending_event=None)


def rift_night(**extra):
    """엇갈림, 「며칠 뒤 밤」이 지난 상태."""
    return {"stage": 5, "affection": 40, "route": "care", "crisis": "rift", "turn": 20,
            "used_events": {"CRISIS": 5, "RIFT_START": 12}, **extra}


class CrisisJudgeTest(unittest.TestCase):  # B1
    def test_sincere_before_crisis_does_not_win(self):
        st = crisis_start()
        self.assertEqual((st["stage"], st["crisis"]), (5, "on"))
        _, st = step(st, "가지 마", NEWS, flags=["sincere"])  # 소식 답장을 판정하는 차례(그 전 메시지)
        self.assertEqual((st["stage"], st["crisis_msgs"]), (5, 0))
        _, st = step(st, "가지 마", flags=["pursue"])
        self.assertEqual(st["stage"], 6)

    def test_batch_after_news_counts(self):
        st = crisis_start()
        llm = PromptStub({"flags": ["pursue"]})
        r = engine(llm).judge(st, "응\n가지 마\n여기 있어", REPLY)  # 첫째만 소식 전
        self.assertEqual(r["state"]["stage"], 6)
        self.assertIn("[위기 판정]", llm.prompt)
        self.assertIn("첫 번째 유저 메시지는 서아가 소식을 꺼내기 전의 말이다", llm.prompt)
        self.assertIn("정확한 단어가 아니어도 sincere 또는 pursue", llm.prompt)
        st = crisis_start()
        r, st = step(st, "응\n그렇구나\n음")
        self.assertEqual((st["stage"], st["crisis_msgs"]), (5, 2))

    def test_won_line_already_shown_counts(self):
        st = crisis_start()
        _, st = step(st, "응", NEWS)
        r = engine(Stub()).judge(st, "…있어 줘", WON_CARE)  # 판정 flags 가 비어도 성공 대사가 나왔으면 성공
        self.assertEqual((r["state"]["stage"], r["state"]["won_line"], r["pending_event"]), (6, None, "STAGE_UP_6"))

    def test_fallback_news_is_redone(self):
        st = crisis_start()
        r, st = step(st, "응", "…")
        self.assertEqual((r["pending_event"], st["crisis_msgs"]), ("CRISIS", 0))
        _, st = step(st, "가지 마", flags=["sincere"])  # 다시 낸 소식 답장을 판정하는 차례
        self.assertEqual(st["stage"], 5)

    def test_six_fail_then_rift(self):
        st = crisis_start()
        _, st = step(st, "응", NEWS)
        for _ in range(2):
            r, st = step(st)
        self.assertEqual((st["crisis"], r["pending_event"], st["rift_sorry"]), ("rift", "RIFT_START", False))


class RiftTest(unittest.TestCase):  # B2
    def open_door(self, st):
        for _ in range(4):
            r, st = step(st)
            if r["pending_event"] == "RECONCILE_OPEN":
                return st
        raise AssertionError("문이 안 열림")

    def test_early_apology_remembered(self):
        r, st = step(rift_night(turn=13), "그 밤에 미안했어\n진짜로", flags=["apologize"])  # RIFT_START 뒤 한 번 답한 말
        self.assertTrue(st["rift_sorry"])
        self.assertEqual(st["stage"], 5)  # 문이 열리기 전에는 화해 안 함
        st = self.open_door(st)  # 화해의 문은 반드시 겪음(최소 메시지 그대로)
        self.assertIn("같은 말을 다시 요구하지 않는다", system_for(st))
        _, st = step(st, "응")  # 문을 연 답장을 판정하는 차례
        self.assertEqual(st["stage"], 5)
        r, st = step(st, "응")  # 문 뒤 유저 답: 다시 사과하지 않아도
        self.assertEqual((st["stage"], st["won_line"], r["pending_event"]), (6, "WON_RECONCILE", "STAGE_UP_6"))
        self.assertFalse(st["rift_sorry"])

    def test_withdraw_no_forced_reconcile(self):
        _, st = step(rift_night(), "미안", flags=["apologize"])
        self.assertTrue(st["rift_sorry"])
        _, st = step(st, "아니 됐어. 다 끝났어", flags=["withdraw"])
        self.assertFalse(st["rift_sorry"])
        st = self.open_door(st)
        _, st = step(st, "응")
        _, st = step(st, "응")
        self.assertEqual(st["stage"], 5)
        _, st = step(st, "미안해 그날", flags=["apologize"])
        self.assertEqual(st["stage"], 6)

    def test_withdraw_after_door(self):
        _, st = step(rift_night(), "미안", flags=["apologize"])
        self.assertTrue(st["rift_sorry"])
        st = self.open_door(st)
        _, st = step(st, "응")
        _, st = step(st, "몰라 이제 상관없어", flags=["withdraw"])
        self.assertEqual((st["stage"], st["rift_sorry"]), (5, False))

    def test_name_apology_is_not_crisis_apology(self):
        st = rift_night(user_name="도윤", name_state="refused")
        r, st = step(st, "이름 거절해서 미안", flags=["apologize"])
        self.assertEqual((st["name_state"], st["rift_sorry"]), ("forgiven", False))
        st = rift_night(user_name="도윤", name_state="forgiven")
        _, st = step(st, "그날 미안해", flags=["apologize"])
        self.assertTrue(st["rift_sorry"])

    def test_before_rift_messages_not_remembered(self):
        st = dict(rift_night(), night_next="RIFT_NIGHT", turn=12)  # RIFT_START 를 정한 바로 다음 판정
        _, st = step(st, "미안", flags=["apologize"])  # 엇갈림 답장을 받기 전의 말
        self.assertFalse(st["rift_sorry"])

    def test_fallback_door_is_redone(self):
        st = rift_night(crisis_msgs=6, used_events={"CRISIS": 5, "RIFT_START": 12, "RECONCILE_OPEN": 20}, turn=21)
        r, st = step(st, "응", "…")
        self.assertEqual(r["pending_event"], "RECONCILE_OPEN")

    def test_rift_sorry_normalize(self):
        self.assertFalse(normalize_state({"stage": 5, "crisis": "on", "rift_sorry": True})["rift_sorry"])
        self.assertTrue(normalize_state({"stage": 5, "crisis": "rift", "rift_sorry": True})["rift_sorry"])
        self.assertFalse(normalize_state({})["rift_sorry"])


class WonLineTest(unittest.TestCase):  # B3
    def test_reconcile_line_not_repeated(self):
        st = rift_night(crisis_msgs=8, rift_sorry=True, used_events={"CRISIS": 5, "RIFT_START": 12, "RECONCILE_OPEN": 15})
        r = engine(Stub()).judge(st, "응", RECONCILE)
        st = r["state"]
        self.assertEqual((st["stage"], st["won_line"]), (6, None))
        self.assertNotIn("…늦었잖아. 그래도, 왔으니까.", system_for(st))
        self.assertIn("매일 온 거, 술 때문 아니었어.", system_for(st))


class NoteStub(Stub):
    """판정 입력을 모두 남기고, 단계 기록을 정해 두는 Stub."""

    def __init__(self, note="", **verdict):
        super().__init__(dict(verdict, stage_note=note))
        self.prompts = []

    def complete(self, system, prompt, json_mode=False, budget_sec=None):
        self.prompts.append(prompt)
        return super().complete(system, prompt, json_mode, budget_sec)


def talk(n, tag):
    """유저·서아 n 턴 대화 기록."""
    out = []
    for i in range(n):
        out += [{"r": "u", "t": f"{tag} 유저{i}"}, {"r": "c", "t": f"{tag} 서아{i}"}]
    return out


class StageNoteTest(unittest.TestCase):  # C1·C2
    def test_note_without_need_summary_uses_recent_slice(self):
        st = normalize_state({"stage": 2, "stage_user_msgs": 2, "stage_note_queue": [{"stage": 1, "after": 2, "msgs": 3}]})
        llm = NoteStub("서아가 손을 맡겼다.")
        recent = talk(3, "경계") + talk(3, "티키")
        r = engine(llm).judge(st, "티키 유저2", REPLY, need_summary=False, recent=recent)
        part = llm.prompts[0].split("[단계 기록용 대화]\n", 1)[1].split("\n\n", 1)[0]
        self.assertIn("경계 유저0", part)
        self.assertNotIn("티키", part)  # 다음 단계 대화는 넣지 않음
        self.assertNotIn("[요약 요청]", llm.prompts[0])
        self.assertEqual((r["state"]["stage_notes"], r["state"]["stage_note_due"], r["state"]["stage_note_queue"]),
                         ({"1": "서아가 손을 맡겼다."}, None, []))

    def test_short_recent_defers(self):
        st = normalize_state({"stage": 2, "stage_user_msgs": 1, "stage_note_queue": [{"stage": 1, "after": 0, "msgs": 3}]})
        for recent in ([], talk(1, "경계")[:1] + talk(2, "티키")):
            llm = NoteStub("지어낸 기록")
            r = engine(llm).judge(st, "응", REPLY, recent=recent)
            self.assertNotIn("[단계 기록 요청]", llm.prompts[0])
            self.assertEqual((r["state"]["stage_notes"], r["state"]["stage_note_due"]), ({}, 1))

    def test_rapid_up_down_keeps_requests(self):
        st = normalize_state({"stage": 2, "stage_user_msgs": 9})
        Engine._advance(st, 3, [])  # 2 마침
        st["name_state"], st["user_name"] = "accepted", "도윤"
        Engine._set_stage(st, 2)  # 이름 거절로 내려감: 3은 마친 것이 아님
        self.assertEqual([e["stage"] for e in st["stage_note_queue"]], [2])
        Engine._set_stage(st, 3)  # 사과로 다시 3
        st["stage_user_msgs"] = 9
        Engine._advance(st, 4, [])  # 3 마침
        self.assertEqual(([e["stage"] for e in st["stage_note_queue"]], st["stage_note_due"]), ([2, 3], 2))
        st = normalize_state(st)  # 앱이 돌려보내도 그대로
        self.assertEqual([e["stage"] for e in st["stage_note_queue"]], [2, 3])
        r = engine(NoteStub("")).judge(st, "응", REPLY, recent=talk(20, "x"))  # 기록이 비면 남음
        self.assertEqual([e["stage"] for e in r["state"]["stage_note_queue"]], [2, 3])
        r = engine(NoteStub("2단계 기록")).judge(r["state"], "응", REPLY, recent=talk(20, "x"))
        self.assertEqual((r["state"]["stage_note_due"], r["state"]["stage_notes"]), (3, {"2": "2단계 기록"}))
        r = engine(NoteStub("3단계 기록")).judge(r["state"], "응", REPLY, recent=talk(20, "x"))
        self.assertEqual((r["state"]["stage_note_due"], sorted(r["state"]["stage_notes"])), (None, ["2", "3"]))

    def test_drop_is_not_completion_and_no_overwrite(self):
        st = {"stage": 3, "affection": 45, "user_name": "도윤", "name_state": "accepted", "stage_notes": {"2": "옛 기록"}}
        r = judge(st, "부르지 마", flags=["refuse_name"])
        self.assertEqual((r["state"]["stage"], r["state"]["stage_note_queue"]), (2, []))
        st = normalize_state({"stage": 2, "stage_user_msgs": 9, "stage_notes": {"2": "옛 기록"}})
        Engine._advance(st, 3, [])
        self.assertEqual(st["stage_note_queue"], [])  # 이미 기록이 있는 단계는 다시 부탁하지 않음

    def test_legacy_due_and_episode(self):
        st = normalize_state({"stage": 3, "stage_user_msgs": 4, "stage_note_due": 2})
        self.assertEqual(st["stage_note_queue"], [{"stage": 2, "after": 4, "msgs": 0}])
        self.assertEqual(normalize_state({"stage_note_queue": "x", "stage_note_due": None})["stage_note_queue"], [])
        xmas = {"stage": 3, "episode": "s2", "episode_stage": 2, "location": "크리스마스 거리",
                "stage_note_queue": [{"stage": 2, "after": 0, "msgs": 3}]}
        llm = NoteStub("x")
        r = engine(llm).judge(xmas, "응", REPLY, recent=talk(10, "x"))
        self.assertNotIn("[단계 기록 요청]", llm.prompts[0])
        self.assertEqual(r["state"]["stage_note_queue"], [{"stage": 2, "after": 0, "msgs": 3}])  # 본편 값은 보관만


class StabilityTest(unittest.TestCase):  # C4
    def test_next_night_changes_nothing_else(self):
        st = {"stage": 5, "affection": 40, "crisis": "rift", "route": "care", "night_next": "RIFT_NIGHT", "turn": 30,
              "used_events": {"CRISIS": 5, "RIFT_START": 12}, "stage_user_msgs": 7, "short_streak": 1,
              "stage_flags": ["care"], "day": 4}
        r = engine(Stub({"affection_delta": 9, "flags": ["sincere"]})).judge(st, "[다음 밤]", REPLY)
        s = r["state"]
        self.assertEqual((s["affection"], s["stage_user_msgs"], s["crisis_msgs"], s["short_streak"], s["stage_flags"]),
                         (40, 7, 0, 1, ["care"]))
        self.assertEqual((s["day"], s["night_next"]), (7, None))
        s2 = engine(Stub()).judge(s, "[다음 밤]", REPLY)["state"]  # 같은 「[다음 밤]」이 또 와도
        self.assertEqual((s2["day"], s2["crisis_msgs"]), (7, 0))

    def test_old_departed_save_and_no_countdown(self):
        st = normalize_state({"stage": 5, "ending": "departed", "departure_days_left": 0})
        self.assertEqual((st["ending"], st["departure_days_left"]), ("departed", None))
        self.assertIsNone(judge(dict(st, short_streak=9))["pending_event"])
        st = crisis_start()
        for _ in range(4):
            r, st = step(st, time_advance=True)
            self.assertIsNone(st["ending"])
            self.assertIsNone(st["departure_days_left"])
            self.assertNotIn(r["pending_event"], ("DEPARTURE", "DAYS_LEFT_2", "DAYS_LEFT_1", "LAST_NIGHT"))

    def test_xmas_unchanged(self):
        xmas = {"stage": 2, "episode": "s2", "episode_stage": 5, "episode_stage_name": "엇갈림",
                "location": "크리스마스 거리", "route": None}
        r = judge(xmas, "가지 마", flags=["sincere", "outside"], affection_delta=5)
        s = r["state"]
        self.assertEqual((s["stage"], s["route"], s["scene"], s["crisis"], r["pending_event"]), (2, None, None, "none", None))
        system = system_for(dict(xmas, pending_event="DATE"))
        self.assertIn("크리스마스 이브", system)
        self.assertNotIn("바에서 다시 출발하지 않는다", system)
        self.assertNotIn("[위기 판정]", system)


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
