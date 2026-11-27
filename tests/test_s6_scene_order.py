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

    def test_news_in_batch(self):
        st = crisis_start("care")
        r, st = step(st, "응\n가지 마", NEWS["care"] + "\n" + OFF_SCRIPT, flags=["pursue"])  # 소식 뒤 한 개
        self.assertEqual(st["stage"], 6)
        r, st = step(crisis_start("care"), "응\n가지 마", OFF_SCRIPT + "\n" + NEWS["care"], flags=["pursue"])
        self.assertEqual((st["stage"], st["crisis_news"], st["crisis_msgs"]), (5, True, 0))  # 둘 다 소식 전

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
        r, st = step(st, "응\n그래", ASK_REPLY + "\n" + WALK)  # 앱은 묶음의 답장을 모두 이어 보냄
        self.assertEqual((st["stage_up_due"], r["pending_event"]), (None, None))
        st = dict(self.rise(), pending_event=None)
        r, st = step(st, "응\n그래", WALK + "\n" + WALK)  # 묶음 어디에도 없음 → 다시
        self.assertEqual((st["stage_up_due"], r["pending_event"]), (3, "STAGE_UP_3"))

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



class FarewellOnceTest(unittest.TestCase):  # C
    C1 = "이 장면 안에서 서아가 먼저 집에 가겠다·내일 보자고 작별하지 않는다. 작별은 밤 마무리에서 한 번만 한다."
    C2 = ("작별 인사는 이 답장에서 한 번만. 새 질문·한 잔 더 같은 새 제안을 꺼내지 않는다.",
          "그다음 유저 답장에는 짧게 한 번만 받고(한 문장 대사), 다시 작별을 반복하지 않는다.")

    def test_scene_blocks(self):
        e = engine()
        for name in ("EVENT_DATE", "EVENT_WALK_HOME"):
            self.assertIn(self.C1, e.blocks[name])
        for name in ("EVENT_ROOFTOP", "EVENT_STORM", "EVENT_ALLEY"):
            self.assertNotIn(self.C1, e.blocks[name])
        st = {"stage": 2, "route": "care", "day": 2, "scene": "WALK_HOME", "scene_msgs": 1,
              "used_events": {"NIGHT_END_2": 1, "ROUTE_WALK_HOME": 2}}
        self.assertIn(self.C1, system_for(st))

    def test_night_end_blocks(self):
        e = engine()
        for name in ("NIGHT_END_1", "NIGHT_END_TEASE", "NIGHT_END_CARE"):
            for line in self.C2:
                self.assertIn(line, e.blocks[name])
        for name in ("NIGHT_START_2", "NIGHT_START_3_CARE"):
            self.assertNotIn(self.C2[0], e.blocks[name])
        st = {"stage": 3, "route": "care", "pending_event": "NIGHT_END", "night_next": "NIGHT_START_3_CARE"}
        s = system_for(st)
        self.assertIn("…바래다준 거, 고마웠어.", s)
        self.assertIn(self.C2[1], s)
        after = system_for(dict(st, pending_event=None))  # 마무리 다음 답장(시간 카드 전 한 번)
        self.assertIn(self.C2[1], after)
        self.assertNotIn("…바래다준 거, 고마웠어.", after)
        self.assertNotIn(self.C2[1], system_for({"stage": 3, "route": "care"}))
        self.assertNotIn(self.C2[1], system_for({"stage": 5, "crisis": "rift", "night_next": "RIFT_NIGHT"}))

    def test_night_end_after_8(self):
        from engine import Engine
        self.assertEqual(NIGHT_END_AFTER, 8)
        for stage, route in ((2, None), (3, "tease")):
            st = normalize_state({"stage": stage, "route": route, "stage_user_msgs": 1 + 7, "stage_up_msgs": 1})
            Engine._night_end(st, [])  # 상징 장면 답장 뒤 7개
            self.assertIsNone(st["pending_event"], stage)
            st = normalize_state({"stage": stage, "route": route, "stage_user_msgs": 1 + 8, "stage_up_msgs": 1})
            Engine._night_end(st, [])  # 8개
            self.assertEqual(st["pending_event"], "NIGHT_END", stage)



class MoveTest(unittest.TestCase):  # D
    D1 = "장소를 바꾸는 이번 답장의 지문 첫 문장은 그곳으로 이동하는 대목이다"

    def reply(self, state, action, history=()):
        return engine(Stub(reply={"action": action})).reply(state, "응", list(history), [])

    def test_prompt_lines(self):
        e = engine()
        for name in ("EVENT_ROOFTOP", "EVENT_STORM", "EVENT_ALLEY", "CRISIS_TEASE", "CRISIS_CARE", "EVENT_DATE",
                     "EVENT_WALK_HOME"):
            self.assertIn(self.D1, e.blocks[name], name)
            self.assertIn("옮기기 전 장소의 물건(카운터·잔·의자)은 이 답장부터 쓰지 않는다.", e.blocks[name])
        self.assertNotIn(self.D1, e.blocks["EVENT_BLACKOUT"])
        world = e.blocks["MAIN_WORLD"]
        line = "2층과 옥상은 사장님 공간이다. 서아는 「옥상, 올라가도 돼?」처럼 먼저 묻거나 사장님을 따라간다."
        self.assertIn(line, world)
        self.assertLess(world.index("# 장소와 일관성"), world.index(line))

    def test_event_start_fills_only_with_move(self):
        st = {"stage": 3, "route": "care", "pending_event": "ROOFTOP", "scene": "ROOFTOP"}
        self.assertEqual(self.reply(st, "계단을 올라 옥상 문을 연다. 바람이 분다.")["location"], "바 옥상")
        self.assertIsNone(self.reply(st, "잔을 내려놓고 카운터에 기댄다.")["location"])
        st = {"stage": 3, "route": "tease", "pending_event": "STORM", "scene": "STORM"}
        self.assertIsNone(self.reply(st, "창밖을 본다.")["location"])
        self.assertEqual(self.reply(st, "서아가 당신을 따라 2층으로 향한다.")["location"], "바 '라스트 오더' 2층")

    def test_scene_continues_then_forced_third(self):
        # S7 A1: 옥상(ROOFTOP)은 유저가 받아 줘야 옮기므로 세 번째 답장 강제 이동은 2층(STORM)으로 확인
        base = {"stage": 3, "route": "tease", "scene": "STORM", "used_events": {"ROUTE_STORM": 1}}
        floor2 = "바 '라스트 오더' 2층"
        sit = "의자에 앉은 채 잔을 돌린다."
        self.assertIsNone(self.reply(dict(base, scene_msgs=1), sit)["location"])  # 두 번째: 이동 대목 없음 → 그대로
        self.assertEqual(self.reply(dict(base, scene_msgs=1), "계단을 올라 2층 문을 연다.")["location"], floor2)
        self.assertEqual(self.reply(dict(base, scene_msgs=2), sit)["location"], floor2)  # 세 번째: 그대로 옮김
        self.assertIsNone(self.reply(dict(base, scene_msgs=2, location=floor2), sit)["location"])  # 이미 옮김
        self.assertIsNone(self.reply(dict(base, scene_msgs=3), "계단을 내려간다.")["location"])  # 창 밖: 되돌리지 않음
        roof = {"stage": 3, "route": "care", "scene": "ROOFTOP", "used_events": {"ROUTE_ROOFTOP": 1}}
        self.assertEqual(self.reply(dict(roof, scene_msgs=1), "문을 열고 옥상으로 나선다.")["location"], "바 옥상")
        self.assertIsNone(self.reply(dict(roof, scene_msgs=2), "창가에 기대 하늘을 본다.")["location"])  # 창문으로 봄

    def test_judge_soon_until_moved(self):
        from engine import judge_soon
        self.assertTrue(judge_soon({"stage": 3, "scene": "ROOFTOP", "location": "바 '라스트 오더' 1층"}))
        self.assertFalse(judge_soon({"stage": 3, "scene": "ROOFTOP", "location": "바 옥상"}))
        self.assertFalse(judge_soon({"stage": 2, "scene": "WALK_HOME", "location": "바 '라스트 오더' 1층"}))  # 모델이 옮김
        r, st = step({"stage": 3, "route": "care", "affection": 30, "stage_user_msgs": 4, "day": 3,
                      "used_events": {"NIGHT_END_3": 1}}, "응", "*본다.* …그래.")
        self.assertEqual((r["pending_event"], st["judge_soon"]), ("ROOFTOP", True))

    def test_model_location_kept(self):
        st = {"stage": 3, "route": "care", "pending_event": "ROOFTOP", "scene": "ROOFTOP"}
        out = engine(Stub(reply={"action": "잔을 든다.", "location": "바 옥상"})).reply(st, "응", [], [])
        self.assertEqual(out["location"], "바 옥상")  # 모델이 직접 옮긴 것은 그대로(프롬프트 D1 로 이끎)



class InnerThoughtAndHintTest(unittest.TestCase):  # E
    HINT1 = "…이 사람, 이런 상처 같은 것도 신경 써 주려나."

    def test_prompt_lines(self):
        e = engine()
        base = e.blocks["BASE_SYSTEM"]
        e2 = ("유저 메시지의 괄호( ) 안은 유저의 행동·표정·속마음이다. 행동과 표정은 서아가 본 것으로 받는다. "
              "속마음(「속으로」「생각한다」「~싶었는데」처럼 말하지 않은 생각)은 서아가 알 수 없으니, 들은 것처럼 대답하지 않는다. "
              "보이는 표정만 받는다.")
        self.assertIn(e2, base)
        self.assertLess(base.index("# 지문"), base.index(e2))
        self.assertLess(base.index(e2), base.index("# 단계별 말투와 스킨십"))
        self.assertIn("유저가 말하지 않은 속마음(괄호 속 생각)만으로는 점수나 플래그를 주지 않는다.", e.blocks["JUDGE_SYSTEM"])

    def reply_system(self, state):
        llm = Stub()
        engine(llm).reply(state, "(음 조금은 아쉽네. 오늘은 조금 더 가까워지고 싶었는데)", [], [])
        return llm.system

    def test_hint_with_condition_met_not_sent(self):
        st = {"stage": 1, "affection": 8, "stage_user_msgs": 4, "hint_due": "HINT_1"}
        self.assertIn(self.HINT1, self.reply_system(st))
        self.assertNotIn(self.HINT1, self.reply_system(dict(st, stage_flags=["care"])))  # 구급상자로 치료한 뒤
        self.assertNotIn(self.HINT1, self.reply_system(dict(st, stage_flags=["protect"])))

    def test_hint_after_stage_change_not_sent(self):
        st = {"stage": 2, "affection": 12, "stage_user_msgs": 1, "hint_due": "HINT_1"}
        self.assertNotIn(self.HINT1, self.reply_system(st))
        self.assertNotIn("장난 말고, 진짜 말 한마디.", self.reply_system({"stage": 4, "hint_due": "HINT_3"}))

    def test_other_hint_conditions(self):
        from engine import Engine
        cases = [({"stage": 2, "stage_flags": ["outside"]}, "HINT_2"),
                 ({"stage": 2, "used_events": {"ROUTE_WALK_HOME": 1}}, "HINT_2"),
                 ({"stage": 3, "stage_flags": ["sincere"]}, "HINT_3"),
                 ({"stage": 4, "used_events": {"ROUTE_ALLEY": 1}}, "HINT_4"),
                 ({"stage": 6}, "HINT_5"),
                 ({"stage": 5, "crisis": "rift", "rift_sorry": True}, "HINT_6")]
        for raw, name in cases:
            self.assertTrue(Engine._hint_stale(normalize_state(raw), name), name)
        for raw, name in [({"stage": 2}, "HINT_2"), ({"stage": 3}, "HINT_3"), ({"stage": 4}, "HINT_4"),
                          ({"stage": 5, "crisis": "on"}, "HINT_5"), ({"stage": 5, "crisis": "rift"}, "HINT_6")]:
            self.assertFalse(Engine._hint_stale(normalize_state(raw), name), name)

    def test_judge_does_not_book_met_hint(self):
        r = engine(Stub({"flags": ["care"]})).judge({"stage": 1, "affection": 2, "stage_user_msgs": 3}, "(상처를 치료해 준다)",
                                                   "*본다.* …그래.")
        self.assertIsNone(r["state"]["hint_due"])
        r = engine(Stub()).judge({"stage": 1, "affection": 2, "stage_user_msgs": 3}, "응", "*본다.* …그래.")
        self.assertEqual(r["state"]["hint_due"], "HINT_1")


if __name__ == "__main__":
    unittest.main()
