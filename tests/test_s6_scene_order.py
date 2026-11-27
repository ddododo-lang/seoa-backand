"""S6 장면 순서 버그 수정 (2026-11-27 실제 플레이). 키 없이 돈다(FakeLLM).
실행: python -m unittest discover -s tests -v"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import CRISIS_NEWS_KEYS, CRISIS_NEWS_TRIES, NIGHT_END_AFTER, STAGE_UP_TRIES, normalize_state  # noqa: E402
from test_s4_1_story import crisis_start, step  # noqa: E402
from test_story_routes import Stub, engine, system_for  # noqa: E402

NEWS = {"tease": "*전화를 본다.* 태오야. 큰 건. 도시 밖. …새벽에 떠나래.",
        "care": "*난간에 올라앉는다.* 태오가 큰 일을 물어 왔어. 도시 밖. …새벽에 떠나."}
OFF_SCRIPT = "*돌아본다.* …말할 기회는 많았잖아."  # 실제 플레이: 소식 대신 유저 말에 답함
WATCH = "# 위기 (이번 유저 메시지를 보고)"
ASK = "…나 이제 사장님 이름 부르고 싶어. 도환이라고 부를래."
WALK = "*골목 끝에서 걸음을 늦춘다.* …여기서부턴 혼자 갈게."  # 상징 장면 없이 배웅을 이어 간 답장
ASK_REPLY = "*걸음을 멈춘다.* " + ASK


def msgs(n):
    return "\n".join(["응"] * n)


def walk_home_last():
    """11-27 재현: 2일째 밤 다정 갈래, 배웅(WALK_HOME) 마지막 대목을 앞둔 2단계. 유저 이름 도환."""
    return {"stage": 2, "affection": 30, "route": "care", "day": 2, "stage_user_msgs": 12, "turn": 20,
            "location": "비 내리는 골목", "scene": "WALK_HOME", "scene_msgs": 3, "user_name": "도환",
            "used_events": {"NIGHT_END_2": 3, "ROUTE_WALK_HOME": 15, "WALK_HOME": 15}}


class CrisisNewsTest(unittest.TestCase):  # A
    def test_a1_line_in_both_blocks(self):
        e = engine()
        line = "이번 답장이 장면 시작이면, 유저의 이번 메시지가 무엇이든 1대목 원고로 답한다(지문 첫 문장과 대사 그대로)."
        for route in ("TEASE", "CARE"):
            block = e.blocks[f"CRISIS_{route}"]
            self.assertIn(line, block)
            self.assertIn("유저의 말은 1대목 다음 대목에서 받는다.", block)
            self.assertLess(block.index(line), block.index("1. (시작)"))
        self.assertEqual(CRISIS_NEWS_KEYS, {"tease": "새벽에떠나래", "care": "새벽에떠나"})

    def test_no_news_rebooks_and_no_win(self):
        for route in ("tease", "care"):
            st = crisis_start(route)
            r, st = step(st, "(돌아선 서아를 붙잡는다)", OFF_SCRIPT, flags=["pursue"])
            self.assertEqual((st["stage"], r["pending_event"], st["crisis_msgs"], st["crisis_news"]),
                             (5, "CRISIS", 0, False), route)
            r, st = step(st, "가지 마", OFF_SCRIPT, flags=["sincere"])  # 또 소식이 없음: 진심이어도 성공 아님
            self.assertEqual((st["stage"], r["pending_event"]), (5, "CRISIS"), route)
            r, st = step(st, "가지 마", NEWS[route], flags=["sincere"])  # 소식이 나간 답장을 판정: 그 전의 말
            self.assertEqual((st["stage"], st["crisis_news"], st["crisis_msgs"]), (5, True, 0), route)
            r, st = step(st, "가지 마", "*본다.* …", flags=["sincere"])  # 소식 뒤 진심
            self.assertEqual((st["stage"], r["pending_event"]), (6, "STAGE_UP_6"), route)

    def test_news_then_sincere_wins(self):
        for route in ("tease", "care"):
            _, st = step(crisis_start(route), "응", NEWS[route])
            self.assertTrue(st["crisis_news"])
            r, st = step(st, "너 없으면 안 돼", "*본다.* …", flags=["sincere"])
            self.assertEqual(st["stage"], 6, route)

    def test_watch_only_after_news(self):
        for route in ("tease", "care"):
            st = crisis_start(route)
            self.assertNotIn(WATCH, system_for(st))  # 소식을 꺼내는 답장
            _, st = step(st, "응", OFF_SCRIPT)  # 소식이 안 나감 → 다시 CRISIS
            self.assertNotIn(WATCH, system_for(st))
            self.assertNotIn(WATCH, system_for(dict(st, pending_event=None)))  # 확인 전에는 WATCH 없음
            _, st = step(dict(st, pending_event=None), "응", NEWS[route])
            self.assertIn(WATCH, system_for(st))

    def test_retry_has_limit(self):
        st = crisis_start("care")
        pending = []
        for _ in range(CRISIS_NEWS_TRIES + 2):
            r, st = step(st, "응", OFF_SCRIPT)
            pending.append(r["pending_event"])
        self.assertEqual(pending[:CRISIS_NEWS_TRIES - 1], ["CRISIS"] * (CRISIS_NEWS_TRIES - 1))
        self.assertTrue(st["crisis_news"])  # 무한히 미루지 않음
        self.assertNotIn("CRISIS", pending[CRISIS_NEWS_TRIES - 1:])

    def test_fallback_news_rebooks(self):
        r, st = step(crisis_start("tease"), "응", "…")
        self.assertEqual((r["pending_event"], st["crisis_news"]), ("CRISIS", False))

    def test_old_save_counts_as_told(self):
        self.assertTrue(normalize_state({"stage": 5, "crisis": "on", "turn": 10, "used_events": {"CRISIS": 5}})["crisis_news"])
        self.assertFalse(normalize_state({"stage": 5, "crisis": "on", "turn": 6, "used_events": {"CRISIS": 5}})["crisis_news"])
        self.assertFalse(normalize_state({"stage": 5, "crisis": "on", "turn": 10, "used_events": {"CRISIS": 5},
                                          "crisis_news": False})["crisis_news"])
        self.assertFalse(normalize_state({})["crisis_news"])

    def test_reply_location_still_set(self):
        st = crisis_start("tease")
        out = engine(Stub()).reply(dict(st, pending_event="CRISIS"), "(돌아선 서아를 붙잡는다)", [], [])
        self.assertEqual(out["location"], "네온 골목")



class StageUpOrderTest(unittest.TestCase):  # B
    def rise(self):
        r, st = step(walk_home_last(), "응", WALK)  # 배웅을 마친 판정에서 3단계
        self.assertEqual((st["stage"], r["pending_event"], st["name_state"], st["stage_up_due"]),
                         (3, "STAGE_UP_3", "asked", 3))
        self.assertTrue(st["judge_soon"])
        return r["state"]

    def test_named_scene_next_reply_before_scene_and_night(self):
        st = self.rise()
        s = system_for(st)
        self.assertIn(ASK, s)
        self.assertNotIn("알아. 문 닫은 거. …한 잔만 더.", s)
        self.assertNotIn("# 사건 — 배웅", s)
        self.assertNotIn("# 밤이 끝난다", s)

    def test_repro_model_skips_ask(self):
        """원인 재현: 상징 장면 답장에 물음이 없으면 예전에는 그대로 지나가 이름을 받아들인 것으로 넘기고 밤 마무리를 셈."""
        st = dict(self.rise(), pending_event=None)
        r, st = step(st, "조심히 가", WALK)  # 모델이 배웅을 이어 감(물음 없음)
        self.assertEqual((r["pending_event"], st["name_state"], st["stage_up_due"]), ("STAGE_UP_3", "asked", 3))
        self.assertIn(ASK, system_for(r["state"]))
        r, st = step(st, "응", WALK)
        self.assertEqual((r["pending_event"], st["name_state"]), ("STAGE_UP_3", "asked"))
        r, st = step(st, "응", ASK_REPLY)  # 이번엔 물음이 나옴
        self.assertEqual((st["stage_up_due"], st["name_state"], st["stage_up_msgs"]), (None, "asked", 3))
        self.assertIsNone(r["pending_event"])
        r, st = step(st, "좋아", "*웃는다.* …그래.")  # 유저의 대답
        self.assertEqual(st["name_state"], "accepted")

    def test_night_end_counts_after_symbol_reply(self):
        st = dict(self.rise(), pending_event=None)
        r, st = step(st, "응", ASK_REPLY)
        r, st = step(st, msgs(NIGHT_END_AFTER - 1))
        self.assertNotEqual(r["pending_event"], "NIGHT_END")
        r = engine(Stub()).judge(dict(st, stage_user_msgs=st["stage_user_msgs"]), "응", "*본다.* …그래.")
        self.assertEqual(r["pending_event"], "NIGHT_END")

    def test_no_night_end_before_symbol(self):
        from engine import Engine
        st = normalize_state(dict(self.rise(), pending_event=None, stage_user_msgs=30))
        Engine._night_end(st, [])  # 메시지가 넉넉해도 상징 장면 확인 전에는 예약하지 않음
        self.assertEqual((st["pending_event"], st["night_next"]), (None, None))
        st["stage_up_due"], st["name_state"] = None, "accepted"
        Engine._night_end(st, [])
        self.assertEqual(st["pending_event"], "NIGHT_END")

    def test_retry_limit(self):
        st = dict(self.rise(), pending_event=None)
        for _ in range(STAGE_UP_TRIES):
            r, st = step(st, "응", WALK)
        self.assertIsNone(st["stage_up_due"])  # 무한히 미루지 않음
        self.assertFalse(st["judge_soon"] and st["name_state"] != "asked")

    def test_batch_counts_as_shown(self):
        st = dict(self.rise(), pending_event=None)
        r, st = step(st, "응\n그래", WALK)  # 마지막 답장만 보이는 묶음: 중복을 피해 나간 것으로
        self.assertEqual((st["stage_up_due"], r["pending_event"]), (None, None))

    def test_symbol_not_carried_to_next_night(self):
        st = normalize_state({"stage": 3, "route": "care", "user_name": "도환", "name_state": "asked",
                              "pending_event": "STAGE_UP_3", "stage_up_due": 3, "night_next": "NIGHT_START_3_CARE"})
        llm = Stub()
        engine(llm).reply(st, "[다음 밤]", [], [])
        self.assertNotIn("이름 부르고 싶어", llm.system)  # 「[다음 밤]」 답장에는 상징 장면 없음
        self.assertNotIn("# 단계가 막 올랐다", llm.system)
        r = engine(Stub()).judge(st, "[다음 밤]", "*문 종이 울린다.* 오늘은 안 다쳤어.")
        self.assertEqual((r["state"]["stage_up_due"], r["pending_event"]), (None, None))

    def test_late_name_still_asks(self):
        st = {"stage": 3, "route": "care", "user_name": "도환", "name_state": "none", "pending_event": "STAGE_UP_3",
              "stage_up_due": 3}
        self.assertIn(ASK, system_for(st))
        r = engine(Stub()).judge(dict(st, pending_event=None), "응", ASK_REPLY)
        self.assertEqual(r["state"]["name_state"], "asked")

    def test_name_scene_beats_unshown_night_end(self):
        st = {"stage": 3, "route": "care", "user_name": "도환", "name_state": "asked", "turn": 30,
              "used_events": {"NAME_ASK": 10, "NIGHT_END_3": 29}, "pending_event": "NIGHT_END",
              "night_next": "NIGHT_START_3_CARE"}
        r = engine(Stub({"flags": ["refuse_name"]})).judge(st, "사장님이 나아", "*본다.* …그래.")
        self.assertEqual((r["pending_event"], r["state"]["night_next"]), ("NAME_REFUSED", None))
        self.assertNotIn("NIGHT_END_3", r["state"]["used_events"])  # 이름 장면 뒤 다시 예약

    def test_other_stages(self):
        for stage, line in ((2, "생각보다 괜찮네. 사장님 손."), (6, "매일 온 거, 술 때문 아니었어.")):
            from engine import Engine
            st = normalize_state({"stage": stage - 1, "route": "care", "crisis": "on" if stage == 6 else "none"})
            Engine._advance(st, stage, [])
            self.assertEqual(st["stage_up_due"], stage)
            r = engine(Stub()).judge(dict(st, pending_event=None), "응", "*본다.* …그래.")
            self.assertEqual(r["pending_event"], f"STAGE_UP_{stage}")
            r = engine(Stub()).judge(dict(st, pending_event=None), "응", "*본다.* " + line)
            self.assertIsNone(r["state"]["stage_up_due"])


if __name__ == "__main__":
    unittest.main()
