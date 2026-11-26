"""S4-1-1 판정 오류 보완: 화해 거부 우선 · 장면 완료 횟수(reply_ok) · 성공 대사 확인 기준. 키 없이 돈다(FakeLLM).
실행: python -m unittest discover -s tests -v"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import RIFT_JUDGE_NOTE, SCENE_REPLIES_DONE, normalize_state  # noqa: E402
from test_s4_1_story import RECONCILE, crisis_start, night2, rift_night, step  # noqa: E402
from test_story_routes import REPLY, Stub, engine, judge, system_for  # noqa: E402

DOOR = {"CRISIS": 5, "RIFT_START": 12, "RECONCILE_OPEN": 15}  # 화해의 문을 연 답장은 이미 판정함


def after_door(**extra):
    return rift_night(crisis_msgs=8, used_events=dict(DOOR), **extra)


def scene_open(**extra):
    """2일째 밤, 위장 연인 장면이 시작 답장을 낸 뒤(pending 비움)."""
    _, st = step(night2("tease"), "응")
    st.update(extra)
    return st


def tick(st, last_user, reply_ok=None, last_reply=REPLY):
    return engine(Stub()).judge(st, last_user, last_reply, reply_ok=reply_ok)["state"]


class WithdrawTest(unittest.TestCase):  # A
    def test_withdraw_beats_won_line(self):  # 1
        r = engine(Stub({"flags": ["withdraw"], "affection_delta": 2})).judge(after_door(), "됐어. 이제 안 와.", RECONCILE)
        st = r["state"]
        self.assertEqual((st["stage"], st["crisis"], st["won_line"], r["pending_event"]), (5, "rift", None, None))
        self.assertEqual(st["affection"], 42)  # 깎지 않음: 판정 점수대로(40 + 2)
        self.assertTrue(any("거부 우선" in c for c in r["changes"]))

    def test_withdraw_clears_remembered_sorry(self):  # 2
        r = engine(Stub({"flags": ["withdraw"]})).judge(after_door(rift_sorry=True), "아니 다 끝났어", RECONCILE)
        st = r["state"]
        self.assertEqual((st["stage"], st["crisis"], st["rift_sorry"]), (5, "rift", False))
        self.assertNotIn("같은 말을 다시 요구하지 않는다", system_for(dict(st, pending_event=None)))

    def test_withdraw_beats_apology_in_same_message(self):  # 3
        for flags in (["apologize", "withdraw"], ["sincere", "withdraw"], ["apologize", "sincere", "withdraw"]):
            st = judge(after_door(rift_sorry=True), "미안한데 다시는 안 볼 거야", flags=flags)["state"]
            self.assertEqual((st["stage"], st["crisis"], st["rift_sorry"]), (5, "rift", False), flags)

    def test_can_reconcile_later(self):  # 4
        _, st = step(after_door(rift_sorry=True), "몰라 됐어", flags=["withdraw"])
        self.assertEqual(st["stage"], 5)
        _, st = step(st, "…그냥 그렇다고")  # 아무 말도 아님: 기억이 지워져 화해 안 함
        self.assertEqual(st["stage"], 5)
        r, st = step(st, "아까 말 미안해. 보고 싶었어", flags=["sincere"])
        self.assertEqual((st["stage"], st["crisis"], r["pending_event"]), (6, "done", "STAGE_UP_6"))

    def test_judge_guide(self):  # A2
        for text in (RIFT_JUDGE_NOTE, engine().blocks["JUDGE_SYSTEM"]):
            self.assertIn("명확하게 철회하거나 관계 회복 의사를 명확하게 거부", text)
            self.assertIn("단순한 투덜거림, 서운함, 장난, 망설임, 추가 설명 요구는", text)
            self.assertIn("사과와 명확한 관계 회복 거부가 함께", text)


class ReplyOkTest(unittest.TestCase):  # B
    def test_counts_only_true(self):  # 5
        st = tick(scene_open(), "응\n그래\n알았어", [True, False, True])
        self.assertEqual(st["scene_msgs"], 2)

    def test_all_failed_adds_nothing(self):  # 6
        st = tick(scene_open(scene_msgs=1), "응\n그래", [False, False])
        self.assertEqual((st["scene"], st["scene_msgs"]), ("DATE", 1))

    def test_done_at_four(self):  # 7
        self.assertEqual(SCENE_REPLIES_DONE, 4)
        st = tick(scene_open(), "응\n그래\n알았어", [True, True, True])
        self.assertEqual((st["scene"], st["scene_msgs"]), ("DATE", 3))
        st = tick(st, "음\n좋아", [False, True])
        self.assertIsNone(st["scene"])

    def test_reserved_and_next_night_not_counted(self):  # 8
        r = judge(night2("tease"))
        st = tick(r["state"], "응\n그래", [True, True])  # 예약만 됨(pending 그대로)
        self.assertEqual((st["scene"], st["scene_msgs"]), ("DATE", 0))
        st = tick(scene_open(), "응\n[다음 밤]\n그래", [True, True, True])  # 「[다음 밤]」 자리는 안 셈
        self.assertEqual(st["scene_msgs"], 2)
        st = tick(scene_open(), "[다음 밤]", [True])
        self.assertEqual(st["scene_msgs"], 0)

    def test_old_request_and_bad_length(self):  # 9
        self.assertEqual(tick(scene_open(), "응\n그래\n알았어")["scene_msgs"], 3)  # reply_ok 없음
        self.assertEqual(tick(scene_open(), "응\n그래\n알았어", last_reply="…")["scene_msgs"], 2)  # 예전처럼 마지막만 뺌
        for bad in ([True, False], [True] * 4, "x", [1, 0, 1], [True, None, True], {}):
            self.assertEqual(tick(scene_open(), "응\n그래\n알았어", bad)["scene_msgs"], 3, bad)
        st = tick(scene_open(), "응\n그래\n알았어", [True, True, True], last_reply="…")
        self.assertEqual(st["scene_msgs"], 3)  # 배열이 있으면 배열을 따름

    def test_server_field_optional(self):
        os.environ.setdefault("LLM_PROVIDER", "fake")
        os.environ.setdefault("DB_PATH", ":memory:")
        if os.environ["LLM_PROVIDER"] != "fake":
            self.skipTest("LLM_PROVIDER 가 fake 가 아님")
        import server
        self.assertIsNone(server.JudgeIn(char="seoa").reply_ok)
        self.assertEqual(server.JudgeIn(char="seoa", reply_ok=[True, False]).reply_ok, [True, False])
        self.assertEqual(server.JudgeIn(char="seoa", reply_ok="x").reply_ok, "x")  # 모양이 틀려도 오류 없이 받고 엔진이 무시


class WonLineCheckTest(unittest.TestCase):  # C
    def test_partial_line_keeps_won_line(self):  # 10
        st = crisis_start("care")
        _, st = step(st, "응")
        r = engine(Stub({"flags": ["pursue"]})).judge(st, "가지 마", "*일어선다.* 이제 내려가자.")  # 「같이」 없음
        self.assertEqual((r["state"]["stage"], r["state"]["won_line"]), (6, "WON_CARE"))
        self.assertIn("…내려가자. 같이.", system_for(r["state"]))
        r = engine(Stub({"flags": ["sincere"]})).judge(after_door(), "미안해", "*돌아본다.* …늦었잖아.")  # 「왔으니까」 없음
        self.assertEqual((r["state"]["stage"], r["state"]["won_line"]), (6, "WON_RECONCILE"))

    def test_full_line_not_repeated(self):  # 11
        st = crisis_start("care")
        _, st = step(st, "응")
        r = engine(Stub({"flags": ["pursue"]})).judge(st, "가지 마", "*난간에서 내려온다.* …내려가자. 같이.")
        self.assertEqual((r["state"]["stage"], r["state"]["won_line"]), (6, None))
        self.assertNotIn("…내려가자. 같이.", system_for(r["state"]))
        st = crisis_start("tease")
        _, st = step(st, "응")
        r = engine(Stub({"flags": ["pursue"]})).judge(st, "가지 마", "*고개를 든다.* …연기 아니지. 이번엔.")
        self.assertEqual(r["state"]["won_line"], None)
        self.assertEqual(normalize_state({"won_line": "WON_CARE"})["won_line"], "WON_CARE")  # 블록 이름 검증은 그대로

    def test_xmas_unchanged(self):  # 12
        xmas = {"stage": 2, "episode": "s2", "episode_stage": 5, "episode_stage_name": "엇갈림",
                "location": "크리스마스 거리"}
        r = engine(Stub({"flags": ["withdraw", "sincere"]})).judge(xmas, "가지 마", RECONCILE, reply_ok=[False])
        s = r["state"]
        self.assertEqual((s["stage"], s["crisis"], s["scene_msgs"], s["won_line"], r["pending_event"]),
                         (2, "none", 0, None, None))
        self.assertIn("크리스마스 이브", system_for(xmas))


if __name__ == "__main__":
    unittest.main()
