"""S7: 옥상·위기 원고 정리(A) · 연인 확정(B). 키 없이 돈다(FakeLLM).
실행: python -m unittest discover -s tests -v"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import LOVERS_AGAIN_MSGS, LOVERS_ASKS_MAX, normalize_state  # noqa: E402
from test_s4_1_story import NEWS, RECONCILE, PromptStub, crisis_start, rift_night, step  # noqa: E402
from test_story_routes import Stub, engine, system_for  # noqa: E402

ASK = {"tease": "…이젠 가짜 연인 행세 안 해도 되겠네. 진짜로 만날래, 우리?",
       "care": "나도 이제 솔직하게 말할게. …앞으로도 네 옆에 있고 싶어.",
       "reconcile": "이번엔 도망 안 가. 그러니까… 나랑 만나 줄래?"}
YES = {"tease": "…취소 없기다. 이제 진짜야.", "care": "…다행이다. 진짜로.", "reconcile": "…바보. 진작 그렇게 말하지."}
PROPOSED = "…그거, 내가 먼저 하려던 말인데. 좋아."
LATER = "…알았어. 대답은 천천히 해도 돼. 근데 너무 오래 걸리면 내가 먼저 데려간다."
AGAIN = "…그래서, 대답은?"
STAGE_UP_6 = "매일 온 거, 술 때문 아니었어."
WON = {"tease": "*고개를 든다.* …연기 아니지. 이번엔.", "care": "*내려온다.* …내려가자. 같이."}
WATCH = "# 연인 확정 (이번 유저 메시지를 보고)"


def reply_with(state, action):
    return engine(Stub(reply={"action": action})).reply(state, "응", [], [])


# ━━ A. 원고 정리 ━━
class RooftopScriptTest(unittest.TestCase):  # A1
    def test_two_beats(self):
        b = engine().blocks["EVENT_ROOFTOP"]
        first = "- (아직 바 안) *창밖을 보던 서아가 눈을 크게 뜬다. 1년에 하루, 비가 그친 날이다.* 「…비, 그쳤어. 사장님, 여기 옥상… 올라가도 돼?」"
        second = "- (유저가 받아 주면) *당신을 따라 계단을 올라 옥상 문을 연다. 젖지 않은 밤공기가 쏟아진다.* 장소를 \"바 옥상\"으로 옮긴다."
        self.assertIn(first, b)
        self.assertIn(second, b)
        self.assertLess(b.index(first), b.index(second))
        self.assertLess(b.index(second), b.index("「별, 처음 봐. …이 도시에서.」"))
        self.assertIn("「…그럼 창문으로 볼게.」", b)
        self.assertNotIn("손목을 잡고", b)

    def test_first_beat_does_not_move(self):
        st = {"stage": 3, "route": "care", "pending_event": "ROOFTOP", "scene": "ROOFTOP"}
        self.assertIsNone(reply_with(st, "창밖을 보던 서아가 눈을 크게 뜬다. 1년에 하루, 비가 그친 날이다.")["location"])
        moved = reply_with(dict(st, pending_event=None, scene_msgs=1), "당신을 따라 계단을 올라 옥상 문을 연다. 젖지 않은 밤공기가 쏟아진다.")
        self.assertEqual(moved["location"], "바 옥상")


class CrisisCareScriptTest(unittest.TestCase):  # A2
    def test_move_beat_first(self):
        b = engine().blocks["CRISIS_CARE"]
        self.assertIn("1. (시작) *골목을 나와 당신과 함께 바 옥상으로 올라온다. 서아가 먼저 난간 위로 올라앉는다. "
                      "도시를 내려다보는 서아만의 자리.", b)
        self.assertNotIn("서아가 먼저 옥상 난간 위로", b)


# ━━ B. 연인 확정 ━━
def won(route="care", **extra):
    """위기 소식 뒤 마음 확인(성공 대사가 그 답장에 나옴) → 연인 확정 묻기 예약(LOVERS_ASK)."""
    _, st = step(dict(crisis_start(route), **extra), "응", NEWS)
    r, st = step(st, "가지 마", WON[route], flags=["pursue"])
    assert r["pending_event"] == "LOVERS_ASK", r["changes"]
    return st


def asked(route="care", **extra):
    """물음(1절) 답장까지 나가고, 그 답장을 판정한 뒤(유저의 대답을 기다림)."""
    r, st = step(won(route, **extra), "응", "*본다.* " + ASK[route])
    assert st["lovers"] == "ask" and r["pending_event"] is None, r["changes"]
    return st


class AskTest(unittest.TestCase):  # B2
    def test_win_keeps_stage5_and_asks_by_route(self):
        for route in ("tease", "care"):
            st = won(route)
            self.assertEqual((st["stage"], st["crisis"], st["lovers"], st["lovers_msgs"], st["lovers_asks"]),
                             (5, "done", "ask", 0, 0), route)
            self.assertTrue(st["judge_soon"])
            self.assertEqual(st["stage_label"], "고백")
            s = system_for(dict(st, pending_event="LOVERS_ASK"))
            self.assertIn(ASK[route], s, route)
            self.assertNotIn(ASK["care" if route == "tease" else "tease"], s)
            self.assertNotIn(WATCH, s)  # 물음 답장에는 WATCH 없음
            self.assertNotIn("# 단계 지침 — 5. 위기", s)  # 「새벽에 떠나야 한다」 지침 없음

    def test_call_name_or_boss(self):
        st = dict(won("care"), pending_event="LOVERS_ASK")
        self.assertIn("네 옆에 있고 싶어. 사장님은?", system_for(st))
        named = dict(st, user_name="도윤", name_state="accepted")
        self.assertIn("네 옆에 있고 싶어. 도윤은?", system_for(named))
        self.assertIn("도윤은?", system_for(dict(named, name_state="forgiven")))
        self.assertIn("사장님은?", system_for(dict(named, name_state="refused")))
        self.assertIn("사장님은?", system_for(dict(named, name_state="closed")))
        self.assertNotIn("{호칭}", system_for(named))

    def test_win_line_missing_goes_before_ask(self):
        _, st = step(crisis_start("care"), "응", NEWS)
        r, st = step(st, "가지 마", "*본다.* …", flags=["sincere"])
        self.assertEqual((st["won_line"], r["pending_event"]), ("WON_CARE", "LOVERS_ASK"))
        s = system_for(dict(st, pending_event="LOVERS_ASK"))
        self.assertLess(s.index("…내려가자. 같이."), s.index(ASK["care"]))

    def test_ask_not_shown_rebooks(self):
        r, st = step(won("care"), "응", "*잔을 든다.* …그래.")
        self.assertEqual((r["pending_event"], st["lovers"]), ("LOVERS_ASK", "ask"))
        r, st = step(st, "응", "*잔을 든다.* …그래.")
        self.assertEqual(r["pending_event"], "LOVERS_ASK")
        r, st = step(st, "응", "*잔을 든다.* …그래.")  # 세 번째: 나간 것으로 보고 진행
        self.assertIsNone(r["pending_event"])
        self.assertEqual(st["lovers"], "ask")

    def test_judge_input_line(self):
        llm = PromptStub()
        engine(llm).judge(asked("care"), "음", "…")
        self.assertIn("연인 확정: 서아가 관계를 물음", llm.prompt)
        llm = PromptStub()
        engine(llm).judge(dict(asked("care"), lovers="wait"), "음", "…")
        self.assertIn("연인 확정: 대답을 기다림", llm.prompt)
        llm = PromptStub()
        engine(llm).judge(crisis_start("care"), "음", "…")
        self.assertNotIn("연인 확정", llm.prompt)

    def test_judge_system_flags(self):
        j = engine().blocks["JUDGE_SYSTEM"]
        self.assertIn('- "lovers_yes":', j)
        self.assertIn('- "lovers_later":', j)
        self.assertIn("거절도 여기에 넣는다", j)


class YesTest(unittest.TestCase):  # B5
    def test_accept_goes_stage6_without_repeat(self):
        for route in ("tease", "care"):
            r, st = step(asked(route), "좋아", "*웃는다.* " + YES[route], flags=["lovers_yes"])
            self.assertEqual((st["stage"], st["lovers"], r["pending_event"], st["won_line"]),
                             (6, "done", "STAGE_UP_6", None), route)
            self.assertEqual(st["stage_label"], "연인")
            s = system_for(dict(st, pending_event="STAGE_UP_6"))
            self.assertIn(STAGE_UP_6, s)
            self.assertNotIn(YES[route], s)
            self.assertNotIn(WATCH, s)

    def test_accept_line_missing_goes_before_stage_up(self):
        r, st = step(asked("tease"), "응 만나자", "*웃는다.* 응.", flags=["lovers_yes"])
        self.assertEqual((st["stage"], st["won_line"], r["pending_event"]), (6, "LOVERS_YES_TEASE", "STAGE_UP_6"))
        s = system_for(dict(st, pending_event="STAGE_UP_6"))
        self.assertLess(s.index(YES["tease"]), s.index("# 단계가 막 올랐다"))

    def test_user_proposes_first_before_ask(self):
        r, st = step(won("care"), "우리 사귀자", "*본다.* " + ASK["care"], flags=["lovers_yes"])  # 묻기 전의 말
        self.assertEqual((st["stage"], st["won_line"]), (6, "LOVERS_PROPOSED"))
        s = system_for(dict(st, pending_event="STAGE_UP_6"))
        self.assertLess(s.index(PROPOSED), s.index("# 단계가 막 올랐다"))

    def test_same_batch_as_win(self):
        _, st = step(crisis_start("care"), "응", NEWS)
        r, st = step(st, "가지 마. 나랑 사귀자", WON["care"], flags=["pursue", "lovers_yes"])
        self.assertEqual((st["stage"], st["lovers"], r["pending_event"], st["won_line"]),
                         (6, "done", "STAGE_UP_6", "LOVERS_PROPOSED"))
        s = system_for(dict(st, pending_event="STAGE_UP_6"))
        self.assertNotIn(ASK["care"], s)
        self.assertLess(s.index(PROPOSED), s.index("# 단계가 막 올랐다"))
        _, st = step(crisis_start("care"), "응", NEWS)
        r, st = step(st, "사귀자", "*본다.* …", flags=["sincere", "lovers_yes"])  # 성공 대사도 없었음
        self.assertEqual(st["won_line"], "WON_CARE+LOVERS_PROPOSED")
        s = system_for(dict(st, pending_event="STAGE_UP_6"))
        self.assertLess(s.index("…내려가자. 같이."), s.index(PROPOSED))
        self.assertEqual(normalize_state(st)["won_line"], "WON_CARE+LOVERS_PROPOSED")

    def test_yes_outside_lovers_ignored(self):
        _, st = step(crisis_start("care"), "응", NEWS)
        r, st = step(st, "사귀자", "…", flags=["lovers_yes"])  # 마음 확인(sincere·pursue) 없이 lovers_yes 만
        self.assertEqual((st["stage"], st["crisis"], st["lovers"]), (5, "on", "none"))


class LaterTest(unittest.TestCase):  # B5 망설임
    def test_later_waits_then_asks_again_twice(self):
        r, st = step(asked("care"), "조금만 생각해 볼게", "*입술을 깨문다.* " + LATER, flags=["lovers_later"])
        self.assertEqual((st["stage"], st["lovers"], st["lovers_msgs"]), (5, "wait", 0))
        self.assertFalse(st["judge_soon"])
        self.assertEqual(st["stage_label"], "고백")
        s = system_for(st)
        self.assertIn(WATCH, s)
        self.assertIn(LATER, s)
        self.assertIn(YES["care"], s)
        self.assertNotIn(YES["tease"], s)
        for n in range(1, LOVERS_ASKS_MAX + 1):
            r, st = step(st, "음\n그게\n아직", "*본다.* …", flags=["lovers_later"])
            self.assertEqual((r["pending_event"], st["lovers_asks"], st["lovers_msgs"]), ("LOVERS_AGAIN", n, 0))
            s = system_for(dict(st, pending_event="LOVERS_AGAIN"))
            self.assertIn(AGAIN, s)
            self.assertNotIn(WATCH, s)
        for _ in range(3):
            r, st = step(st, "\n".join(["음"] * LOVERS_AGAIN_MSGS), "*본다.* …")
            self.assertIsNone(r["pending_event"])  # 최대 두 번
        self.assertEqual((st["stage"], st["lovers"]), (5, "wait"))
        r, st = step(st, "사귀자", "*웃는다.* " + PROPOSED, flags=["lovers_yes"])
        self.assertEqual((st["stage"], st["lovers"], r["pending_event"], st["won_line"]), (6, "done", "STAGE_UP_6", None))

    def test_wait_counts_messages(self):
        _, st = step(asked("tease"), "글쎄", "…", flags=["lovers_later"])
        r, st = step(st, "응\n그래", "…")
        self.assertEqual((r["pending_event"], st["lovers_msgs"]), (None, 2))
        r, st = step(st, "응", "…")
        self.assertEqual(r["pending_event"], "LOVERS_AGAIN")

    def test_yes_after_again(self):
        _, st = step(asked("care"), "아직 모르겠어", "…", flags=["lovers_later"])
        r, st = step(st, "응\n그래\n음", "…")
        self.assertEqual(r["pending_event"], "LOVERS_AGAIN")
        r, st = step(st, "응 만나자", "*톡 건드린다.* " + AGAIN, flags=["lovers_yes"])
        self.assertEqual((st["stage"], st["won_line"]), (6, "LOVERS_YES_CARE"))

    def test_no_events_hints_or_night_end(self):  # B6
        st = dict(asked("care"), lovers="wait", turns_since_event=99, short_streak=9, mari_step=1, taeo_step=1)
        for _ in range(2):
            r, st = step(st, "응", "…")
            self.assertIsNone(r["pending_event"])
            self.assertIsNone(st["hint_due"])
            self.assertIsNone(st["night_next"])
        r, st = step(dict(asked("care"), turns_since_event=99, short_streak=9), "음", "…")
        self.assertIsNone(r["pending_event"])


class ReconcileTest(unittest.TestCase):  # B2 화해
    def test_reconcile_asks_reconcile_line(self):
        st = rift_night(crisis_msgs=8, rift_sorry=True, used_events={"CRISIS": 5, "RIFT_START": 12, "RECONCILE_OPEN": 15})
        r, st = step(st, "응", RECONCILE)
        self.assertEqual((st["stage"], st["lovers"], r["pending_event"]), (5, "ask", "LOVERS_ASK"))
        s = system_for(dict(st, pending_event="LOVERS_ASK"))
        self.assertIn(ASK["reconcile"], s)
        self.assertNotIn(ASK["care"], s)
        self.assertNotIn("# 엇갈림", s)
        r, st = step(st, "응", "*소매를 잡는다.* " + ASK["reconcile"])
        self.assertIn(YES["reconcile"], system_for(st))
        r, st = step(st, "응", "*웃는다.* 응.", flags=["lovers_yes"])
        self.assertEqual((st["stage"], st["won_line"]), (6, "LOVERS_YES_RECONCILE"))


class SaveAndEpisodeTest(unittest.TestCase):  # B7 · 크리스마스
    def test_old_saves(self):
        self.assertEqual(normalize_state({"stage": 6, "crisis": "done"})["lovers"], "done")
        st = normalize_state({"stage": 5, "crisis": "on", "route": "care"})
        self.assertEqual((st["crisis"], st["lovers"]), ("on", "none"))
        st = normalize_state({"stage": 5, "crisis": "done"})  # 예전 저장: 5단계 done 은 위기 중으로
        self.assertEqual((st["crisis"], st["lovers"]), ("on", "none"))
        st = normalize_state({"stage": 5, "crisis": "done", "lovers": "ask", "pending_event": "LOVERS_ASK"})
        self.assertEqual((st["crisis"], st["lovers"], st["pending_event"]), ("done", "ask", "LOVERS_ASK"))
        self.assertEqual(normalize_state({"stage": 3, "lovers": "wait", "lovers_msgs": 2})["lovers"], "none")
        st = normalize_state({"stage": 5, "lovers": "wait", "lovers_msgs": 2, "lovers_asks": 9})
        self.assertEqual((st["lovers_msgs"], st["lovers_asks"]), (2, LOVERS_ASKS_MAX))
        st = normalize_state({"stage": 5, "crisis": "on", "route": "care"})  # 위기 중 예전 저장은 새 흐름으로
        _, st = step(st, "응", NEWS)
        r, st = step(st, "가지 마", "…", flags=["pursue"])
        self.assertEqual((st["stage"], r["pending_event"]), (5, "LOVERS_ASK"))

    def test_christmas_untouched(self):
        xmas = {"stage": 5, "episode": "s2", "episode_stage": 3, "lovers": "ask", "pending_event": "LOVERS_ASK"}
        st = normalize_state(xmas)
        self.assertEqual((st["lovers"], st["pending_event"]), ("none", None))
        self.assertNotEqual(st["stage_label"], "고백")
        s = system_for(xmas)
        self.assertNotIn("연인 확정", s)
        self.assertNotIn(ASK["care"], s)
        r = engine(Stub({"flags": ["lovers_yes"]})).judge(dict(xmas), "사귀자", "…")
        self.assertEqual((r["state"]["stage"], r["state"]["lovers"]), (5, "none"))


if __name__ == "__main__":
    unittest.main()
