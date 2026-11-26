"""S5 서아의 사진 촬영 제안 (photo_3 · photo_6). 키 없이 돈다(FakeLLM).
실행: python -m unittest discover -s tests -v"""
import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import PHOTO_ASKS, normalize_state, photo_supported  # noqa: E402
from test_story_routes import REPLY, Stub, engine  # noqa: E402

TEASE_LINE = "사장님, 이런 모습 아무한테나 안 보여 주는데. 사진 한 장 찍어 둘래?"
CARE_LINE = "…오늘은 좀 기억해 두고 싶어서. 사진 한 장만 찍어 줄래?"
SIX_LINE = "우리 사진 좀 더 남겨 두자. 앞으로 같이 있을 날도 많잖아."
FLOOR2, ROOF, FLOOR1 = "바 '라스트 오더' 2층", "바 옥상", "바 '라스트 오더' 1층"
HISTORY = [{"r": "u", "t": "비 많이 오네"}, {"r": "c", "t": "*창밖을 본다.* …당분간 못 나가겠다."}]
SUPPORT = {"photo_ask_v": 1, "photo_asks": []}


def stage3(route="tease", **extra):
    """3단계, 갈래 장면이 시작 답장을 내고 장소를 옮긴 뒤(앱이 pending 을 비움)."""
    scene, loc = ("STORM", FLOOR2) if route == "tease" else ("ROOFTOP", ROOF)
    st = {"stage": 3, "affection": 32, "route": route, "turn": 8, "day": 4, "stage_user_msgs": 7,
          "used_events": {"NIGHT_END_3": 2, f"ROUTE_{scene}": 7, scene: 7},
          "scene": scene, "scene_msgs": 1, "location": loc, **SUPPORT}
    st.update(extra)
    return st


def stage6(route="care", **extra):
    """6단계, 첫 아침·조연 마무리가 지나 stayed(일상)."""
    st = {"stage": 6, "affection": 90, "route": route, "turn": 40, "day": 12, "crisis": "done",
          "morning_done": True, "mari_step": 3, "taeo_step": 3, "ending": "stayed", "stage_user_msgs": 9, **SUPPORT}
    st.update(extra)
    return st


def ask(state, dialogue=None, msg="오늘 좀 피곤해 보이네", history=HISTORY, motions=(), **reply):
    """/chat 한 번. dialogue 를 안 주면 상황에 맞는 확정 대사를 그대로 말한다고 침."""
    if dialogue is None:
        dialogue = "그래? 괜찮아. " + {3: TEASE_LINE if state.get("route") == "tease" else CARE_LINE, 6: SIX_LINE}.get(
            state.get("stage"), "")
    llm = Stub(reply={"dialogue": dialogue, **reply})
    out = engine(llm).reply(state, msg, list(history), list(motions))
    return out, llm.system or ""


class CompatTest(unittest.TestCase):  # 1 · 11
    def test_old_app_unchanged(self):
        for raw in ({k: v for k, v in stage3().items() if k not in SUPPORT},
                    stage3(photo_ask_v=None), stage3(photo_ask_v=True), stage3(photo_ask_v="1"), stage3(photo_ask_v=2),
                    stage3(photo_asks=None), stage3(photo_asks="photo_3"), stage3(photo_asks={"photo_3": 1})):
            out, system = ask(raw)
            self.assertNotIn("photo_ask", out, raw)
            self.assertNotIn("사진 제안", system)
            self.assertEqual(set(out), {"action", "dialogue", "motion_id", "emotion", "location", "gift_moment"})

    def test_fallback_old_and_new(self):
        class Dead(Stub):
            def chat_json(self, *a, **k):
                return "{}"
        old = engine(Dead()).reply({"stage": 3}, "응", [], [])
        self.assertNotIn("photo_ask", old)
        new = engine(Dead()).reply(stage3(), "응", HISTORY, [])
        self.assertEqual((new["dialogue"], new["photo_ask"]), ("…", None))

    def test_bad_photo_asks_values(self):  # 11
        st = normalize_state(stage3(photo_asks=["x", 3, None, {"a": 1}, "photo_3", "photo_3", "photo_6"]))
        self.assertEqual((st["photo_ask_v"], st["photo_asks"]), (1, ["photo_3", "photo_6"]))
        self.assertEqual(normalize_state({"photo_asks": "photo_3"})["photo_asks"], [])
        self.assertEqual(normalize_state({"photo_ask_v": True})["photo_ask_v"], 0)
        self.assertTrue(photo_supported(stage3(photo_asks=["x", 3])))
        out, _ = ask(stage3(photo_asks=["x", 3, None]))  # 이상한 값은 버리고, photo_3 은 아직 안 보여 줬으므로 발동
        self.assertEqual(out["photo_ask"]["id"], "photo_3")

    def test_judge_keeps_values(self):  # A1: /judge state 에서 빠지지 않음
        for raw in (stage3(photo_asks=["photo_3"]), stage6(photo_asks=["photo_3", "photo_6", "zz"])):
            st = engine(Stub()).judge(raw, "응", REPLY)["state"]
            self.assertEqual(st["photo_ask_v"], 1)
            self.assertEqual(st["photo_asks"], [x for x in raw["photo_asks"] if x != "zz"])
        st = engine(Stub()).judge({"stage": 2}, "응", REPLY)["state"]
        self.assertEqual((st["photo_ask_v"], st["photo_asks"]), (0, []))

    def test_chat_does_not_change_state(self):
        raw = stage3()
        before = copy.deepcopy(raw)
        ask(raw)
        self.assertEqual(raw, before)


class Stage3Test(unittest.TestCase):  # 2
    def test_tease(self):
        out, system = ask(stage3("tease"))
        self.assertEqual(out["photo_ask"], {"id": "photo_3", "route": "tease", "pose": "p01", "face": "smirk",
                                            "camera_face": "wink"})
        self.assertEqual(out["emotion"], "도발")
        self.assertIn(TEASE_LINE, system)
        self.assertIn("입꼬리를 올리며 당신을 빤히 바라본다", system)

    def test_care(self):
        out, system = ask(stage3("care"), emotion="평온")
        self.assertEqual(out["photo_ask"], {"id": "photo_3", "route": "care", "pose": "p01", "face": "soft",
                                            "camera_face": "shy"})
        self.assertEqual(out["emotion"], "설렘")
        self.assertIn(CARE_LINE, system)
        self.assertNotIn(TEASE_LINE, system)

    def test_line_spacing_and_quotes_ok(self):
        out, _ = ask(stage3("care"), dialogue="「...오늘은 좀 기억해 두고 싶어서.  사진 한 장만 찍어 줄래?」")
        self.assertEqual(out["photo_ask"]["id"], "photo_3")

    def test_after_scene_done(self):  # 장면이 끝나도 3단계 · 그 장소면
        out, _ = ask(stage3("tease", scene=None, scene_msgs=0))
        self.assertEqual(out["photo_ask"]["route"], "tease")

    def test_no_p01_in_motions(self):
        motions = [{"id": "m01", "name": "손 흔들기", "emo": "기쁨"}]
        out, system = ask(stage3(), motions=motions)
        self.assertNotIn("p01", system)
        self.assertIsNone(out["motion_id"])


class BeforeSceneTest(unittest.TestCase):  # 3
    def test_scene_not_started(self):
        st = stage3(scene=None, scene_msgs=0, used_events={"NIGHT_END_3": 2})
        out, system = ask(st)
        self.assertIsNone(out["photo_ask"])
        self.assertNotIn("사진 제안", system)

    def test_start_reply_turn(self):  # 장면 시작 답장 자체에는 없음
        out, system = ask(stage3(pending_event="STORM", scene_msgs=0, location=FLOOR1))
        self.assertIsNone(out["photo_ask"])
        self.assertNotIn("사진 제안", system)

    def test_start_reply_was_fallback(self):  # 예비 지문이라 장소가 안 바뀜
        out, _ = ask(stage3(scene_msgs=0, location=FLOOR1))
        self.assertIsNone(out["photo_ask"])

    def test_judge_late_uses_history(self):  # scene_msgs 0 (판정 묶음) · 장소는 이미 2층
        st = stage3(scene_msgs=0)
        fb = HISTORY[:-1] + [{"r": "c", "t": "…"}]
        self.assertIsNone(ask(st, history=fb)[0]["photo_ask"])
        self.assertEqual(ask(st)[0]["photo_ask"]["id"], "photo_3")  # 직전이 정상 답장이면 첫 정상 답장은 나갔음


class OtherStagesTest(unittest.TestCase):  # 4
    def test_other_stages(self):
        for stage in (1, 2, 4, 5):
            st = stage3(stage=stage)
            out, system = ask(st, dialogue=TEASE_LINE)
            self.assertIsNone(out["photo_ask"], stage)
            self.assertNotIn("사진 제안", system)
        out, _ = ask(stage6(stage=5, crisis="on", ending=None), dialogue=SIX_LINE)
        self.assertIsNone(out["photo_ask"])

    def test_episode(self):
        for st in (stage3(episode="s2", episode_stage=3, location="크리스마스 거리"),
                   stage6(episode="s2", episode_stage=6, location="크리스마스 거리")):
            out, system = ask(st)
            self.assertIsNone(out["photo_ask"])
            self.assertNotIn("사진 제안", system)


class PriorityTest(unittest.TestCase):  # 5
    def test_pending_events_first(self):
        for ev in ("MARI_2", "BLACKOUT", "STAGE_UP_3", "NAME_REFUSED", "NIGHT_END"):
            out, system = ask(stage3(pending_event=ev))
            self.assertIsNone(out["photo_ask"], ev)
            self.assertNotIn("사진 제안", system, ev)
        out, system = ask(stage6(pending_event="BLACKOUT"))
        self.assertIsNone(out["photo_ask"])
        self.assertNotIn("사진 제안", system)

    def test_night(self):
        out, _ = ask(stage3(night_next="NIGHT_START_3_TEASE"))
        self.assertIsNone(out["photo_ask"])
        out, _ = ask(stage3(), msg="[다음 밤]")
        self.assertIsNone(out["photo_ask"])

    def test_name_scene_hint_gift_pose(self):
        self.assertIsNone(ask(stage3(user_name="도윤", name_state="asked"))[0]["photo_ask"])
        self.assertIsNone(ask(stage3(name_quiet=2))[0]["photo_ask"])
        self.assertIsNone(ask(stage3(hint_due="HINT_3"))[0]["photo_ask"])
        self.assertIsNone(ask(stage3(), msg="[유저가 선물함: 따뜻한 수건] · 순간 선물")[0]["photo_ask"])
        motions = [{"id": "m07", "name": "윙크", "emo": "장난"}]
        self.assertIsNone(ask(stage3(), msg="윙크 해 줄래?", motions=motions)[0]["photo_ask"])
        moments = [{"id": "towel", "name": "따뜻한 수건", "when": "비에 젖어 온 밤"}]
        out, _ = ask(stage3(gift_moments=moments), gift_moment={"id": "towel", "reason": "젖었어요"})
        self.assertIsNone(out["photo_ask"])

    def test_state_untouched_for_other_events(self):  # 사진 제안 때문에 다른 사건을 취소하지 않음
        st = engine(Stub()).judge(stage3(pending_event="MARI_2"), "응", REPLY)["state"]
        self.assertEqual(st["pending_event"], "MARI_2")


class GuardTest(unittest.TestCase):  # 6
    def test_wrong_location(self):
        self.assertIsNone(ask(stage3("tease", location=ROOF))[0]["photo_ask"])
        self.assertIsNone(ask(stage3("care", location=FLOOR2))[0]["photo_ask"])

    def test_location_moves_in_reply(self):
        out, _ = ask(stage3("tease"), location=FLOOR1)
        self.assertIsNone(out["photo_ask"])
        out, _ = ask(stage3("tease"), location=FLOOR2)  # 같은 장소를 다시 적은 것은 이동이 아님
        self.assertEqual(out["photo_ask"]["id"], "photo_3")

    def test_model_skipped_line(self):
        out, system = ask(stage3(), dialogue="그래? 괜찮아. 비 그치면 나가자.")
        self.assertIn("사진 제안", system)
        self.assertIsNone(out["photo_ask"])
        self.assertEqual(out["emotion"], "평온")

    def test_photo_words_alone(self):  # 사진 이야기만으로는 신호 없음
        out, system = ask(stage3(photo_asks=["photo_3"]), dialogue="사진? " + TEASE_LINE)
        self.assertNotIn("사진 제안", system)
        self.assertIsNone(out["photo_ask"])


class Stage6Test(unittest.TestCase):  # 7 · 8 · 9 · 10
    def test_photo3_recorded(self):  # 7
        out, system = ask(stage3(photo_asks=["photo_3"]))
        self.assertIsNone(out["photo_ask"])
        self.assertNotIn("사진 제안", system)

    def test_before_morning_and_finale(self):  # 8
        for extra in ({"morning_done": False, "ending": None, "taeo_step": 2},
                      {"morning_done": True, "ending": None, "taeo_step": 2},
                      {"morning_done": True, "ending": None, "taeo_step": 3, "pending_event": "TAEO_3"},
                      {"morning_done": True, "ending": None, "taeo_step": 3}):
            out, system = ask(stage6("care", **extra))
            self.assertIsNone(out["photo_ask"], extra)
            self.assertNotIn("사진 제안", system)

    def test_daily(self):  # 9
        for route, face in (("care", "joy"), ("tease", "joy")):
            out, system = ask(stage6(route))
            self.assertEqual(out["photo_ask"], {"id": "photo_6", "route": route, "pose": "p01", "face": face,
                                                "camera_face": "soft"})
            self.assertEqual(out["emotion"], "기쁨")
            self.assertIn(SIX_LINE, system)

    def test_full_flow_to_stayed(self):  # 8→9: 서버 판정으로 stayed 가 된 뒤에 발동
        st = stage6("care", morning_done=True, ending=None, taeo_step=2, stage_user_msgs=3)
        r = engine(Stub()).judge(st, "응", REPLY)
        self.assertEqual(r["pending_event"], "TAEO_3")
        self.assertIsNone(ask(r["state"])[0]["photo_ask"])
        st = engine(Stub()).judge(dict(r["state"], pending_event=None), "응", REPLY)["state"]
        self.assertEqual(st["ending"], "stayed")
        self.assertEqual(ask(dict(st, pending_event=None))[0]["photo_ask"]["id"], "photo_6")

    def test_photo6_recorded(self):  # 10
        out, system = ask(stage6(photo_asks=["photo_3", "photo_6"]))
        self.assertIsNone(out["photo_ask"])
        self.assertNotIn("사진 제안", system)

    def test_photo6_without_photo3(self):  # 3단계를 놓쳐도 6단계는 따로
        self.assertEqual(ask(stage6(photo_asks=[]))[0]["photo_ask"]["id"], "photo_6")


class ManuscriptTest(unittest.TestCase):
    def test_lines_match_blocks(self):
        blocks = engine().blocks
        for (_, _), (block, line, emotion, _f) in PHOTO_ASKS.items():
            self.assertIn(f"- 대사: {line}", blocks[block])
            self.assertIn(f"- emotion: {emotion}", blocks[block])


if __name__ == "__main__":
    unittest.main()
