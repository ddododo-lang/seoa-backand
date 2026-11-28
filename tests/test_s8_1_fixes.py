"""S8-1: 연인 물음 판정 차례 · 장면 선물 쉼 규칙 · 열쇠 차례 · 판정 목록 · 며칠 뒤 밤 지문. 키 없이 돈다(FakeLLM).
실행: python -m unittest discover -s tests -v"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import LOVERS_FIRST_NOTE, MOMENT_AFTER_ALLEY, judge_soon, normalize_state  # noqa: E402
from test_s4_1_story import NEWS, PromptStub, step  # noqa: E402
from test_s7_lovers import ASK, WON, asked, won  # noqa: E402
from test_s8_moment import RING, M, alley_last, gid, night3, without  # noqa: E402
from test_story_routes import REPLY, Stub, engine  # noqa: E402

BASE = Path(__file__).resolve().parent.parent


def no_list(state):
    return {k: v for k, v in state.items() if k != "gift_moments"}


# ━━ 1. 연인 물음 판정 차례 ━━
class LoversFirstMessageTest(unittest.TestCase):
    def test_before_ask_is_not_answer(self):
        for msg in ("응, 같이 내려가자.", "그래, 가자."):
            r, st = step(won("care"), msg, "*본다.* " + ASK["care"], flags=["lovers_yes"])
            self.assertEqual((st["stage"], st["lovers"]), (5, "ask"), msg)
            self.assertNotIn("lovers_yes", r["flags"])
            self.assertTrue(any("lovers_yes 뺌" in c for c in r["changes"]), r["changes"])

    def test_direct_proposal_is_proposed(self):
        for msg in ("우리 사귀자.", "나 너 좋아 해.", "나랑 만나 줄래?"):
            _, st = step(won("tease"), msg, "*본다.* " + ASK["tease"], flags=["lovers_yes"])
            self.assertEqual((st["stage"], st["won_line"]), (6, "LOVERS_PROPOSED"), msg)

    def test_ring_is_yes(self):
        _, st = step(won("care", gift_moments=M), RING, "*본다.* " + ASK["care"])
        self.assertEqual((st["stage"], st["lovers"]), (6, "done"))

    def test_second_message_yes_kept(self):
        _, st = step(won("care"), "응, 같이 내려가자.\n응", "*본다.* " + ASK["care"] + "\n*웃는다.* …", flags=["lovers_yes"])
        self.assertEqual((st["stage"], st["won_line"]), (6, "LOVERS_YES_CARE"))

    def test_after_first_turn_unchanged(self):
        _, st = step(asked("care"), "응", "*본다.* …", flags=["lovers_yes"])
        self.assertEqual(st["stage"], 6)

    def test_judge_note_only_that_turn(self):
        llm = PromptStub()
        engine(llm).judge(won("care"), "응, 같이 내려가자.", "*본다.* " + ASK["care"])
        self.assertIn(LOVERS_FIRST_NOTE, llm.prompt)
        llm = PromptStub()
        engine(llm).judge(asked("care"), "응", "…")
        self.assertNotIn(LOVERS_FIRST_NOTE, llm.prompt)


# ━━ 2. 장면 선물은 「방금 줘서 쉼」에 안 막힘 ━━
class SceneGiftQuietTest(unittest.TestCase):
    def test_ring_right_after_umbrella(self):
        history = [{"r": "c", "t": "…내려가자. 같이."}, {"r": "u", "t": "[유저가 선물함: 우산] · 순간 선물"}]
        st = dict(won("care"), pending_event="LOVERS_ASK", gift_moments=without("umbrella"))
        out = engine(Stub()).reply(st, "응, 같이 내려가자.", history, [])
        self.assertEqual(out["gift_moment"]["id"], "ring")

    def test_same_gift_gap_kept(self):
        history = [{"r": "u", "t": "[유저가 선물함: 반지] · 순간 선물"}]
        st = dict(won("care"), pending_event="LOVERS_ASK", gift_moments=M)
        self.assertIsNone(engine(Stub()).reply(st, "응", history, [])["gift_moment"])


# ━━ 3. 바 여분 열쇠 차례 ━━
class FlowerDueTest(unittest.TestCase):
    def test_waits_through_crisis_and_lovers(self):
        r, st = step(alley_last(affection=60), "응", REPLY)
        self.assertEqual((st["stage"], r["pending_event"]), (5, "CRISIS"))
        self.assertEqual(gid(dict(st, pending_event="CRISIS")), "umbrella")
        _, st = step(st, "응", NEWS)
        self.assertIsNone(gid(st))
        r, st = step(st, "가지 마", WON["care"], flags=["pursue"])
        self.assertEqual(r["pending_event"], "LOVERS_ASK")
        self.assertEqual(gid(dict(st, pending_event="LOVERS_ASK")), "ring")
        _, st = step(st, "응", "*본다.* " + ASK["care"])
        self.assertIsNone(gid(st))
        self.assertIn(MOMENT_AFTER_ALLEY, st["used_events"])
        r, st = step(st, "응, 좋아.", "*본다.* …", flags=["lovers_yes"])
        self.assertEqual((st["stage"], r["pending_event"]), (6, "STAGE_UP_6"))
        self.assertTrue(st["judge_soon"])
        self.assertEqual(gid(dict(st, pending_event="STAGE_UP_6")), "flower")  # 물음까지 끝난 뒤 첫 답장
        _, st = step(st, "응", REPLY)
        self.assertIn("MOMENT_flower", st["used_events"])
        self.assertNotIn(MOMENT_AFTER_ALLEY, st["used_events"])
        self.assertIsNone(gid(st))  # 두 번 안 엶

    def test_before_crisis_opens_now(self):
        _, st = step(alley_last(affection=50), "응", REPLY)
        self.assertEqual(st["stage"], 4)
        self.assertEqual(gid(st), "flower")

    def test_due_survives_missing_list(self):
        _, st = step(alley_last(gift_moments=without("flower")), "응", REPLY)
        for _ in range(2):
            _, st = step(dict(st, gift_moments=without("flower")), "응", REPLY)
        self.assertIn(MOMENT_AFTER_ALLEY, st["used_events"])
        self.assertFalse(judge_soon(normalize_state(st)))  # 열쇠가 목록에 없으면 매 답장 판정을 요구하지 않음
        st = dict(st, gift_moments=M)
        self.assertEqual(gid(st), "flower")
        _, st = step(st, "응", REPLY)
        self.assertIn("MOMENT_flower", st["used_events"])

    def test_ending_clears(self):
        st = {"stage": 6, "route": "care", "ending": "stayed", "gift_moments": without("flower"),
              "used_events": {MOMENT_AFTER_ALLEY: 1}}
        _, st = step(st, "응", REPLY)
        self.assertNotIn(MOMENT_AFTER_ALLEY, st["used_events"])


# ━━ 4. 판정 때 받는 목록 ━━
class JudgeListTest(unittest.TestCase):
    def test_no_key_is_unknown(self):
        _, st = step(alley_last(affection=50), "응", REPLY)
        _, st2 = step(no_list(st), "응", REPLY)
        self.assertIn(MOMENT_AFTER_ALLEY, st2["used_events"])
        self.assertNotIn("MOMENT_flower", st2["used_events"])
        r = engine(Stub()).judge(no_list(dict(night3("care"), gift_moments=M)), "[다음 밤]", "*문 종이 울린다.* …")
        self.assertNotIn("MOMENT_towel", r["state"]["used_events"])

    def test_empty_list_is_none(self):
        _, st = step(alley_last(affection=50), "응", REPLY)
        _, st2 = step(dict(st, gift_moments=[]), "응", REPLY)
        self.assertIn(MOMENT_AFTER_ALLEY, st2["used_events"])  # 없었으니 열리지 않음 → 차례 남음
        r = engine(Stub()).judge(dict(night3("care"), gift_moments=[]), "[다음 밤]", "*문 종이 울린다.* …")
        self.assertNotIn("MOMENT_towel", r["state"]["used_events"])
        r = engine(Stub()).judge(dict(night3("care"), gift_moments=M), "[다음 밤]", "*문 종이 울린다.* …")
        self.assertIn("MOMENT_towel", r["state"]["used_events"])

    def test_temp_key_not_returned(self):
        r = engine(Stub()).judge({"stage": 2}, "응", REPLY)
        self.assertNotIn("_gifts_known", r["state"])


# ━━ 5. 며칠 뒤 밤 지문 ━━
class NightStartActionTest(unittest.TestCase):
    LINES = {"NIGHT_START_3_TEASE": ("- 지문: 어깨에 빗물을 묻힌 채 들어온 서아가 창가 자리에 앉자마자 턱을 괸다.",
                                     "- 대사: 우리 그날 커플이었다고 소문났더라. …책임져, 사장님."),
             "NIGHT_START_3_CARE": ("- 지문: 머리카락 끝에 빗방울을 매단 채 들어온 서아가 손등부터 내민다. 오늘은 상처가 없다.",
                                    "- 대사: 오늘은 안 다쳤어. …먼저 확인하려고 했지?")}

    def test_blocks(self):
        blocks = engine().blocks
        for name, (action, line) in self.LINES.items():
            self.assertIn(action, blocks[name])
            self.assertIn(line, blocks[name])  # 대사는 그대로

    def test_draft_saved(self):
        draft = (BASE / "docs" / "drafts" / "S8-1-원고.md").read_text(encoding="utf-8")
        for name, (action, _) in self.LINES.items():
            self.assertIn(f"- {name} 지문: " + action.removeprefix("- 지문: "), draft)


if __name__ == "__main__":
    unittest.main()
