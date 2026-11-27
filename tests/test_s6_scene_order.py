"""S6 장면 순서 버그 수정 (2026-11-27 실제 플레이). 키 없이 돈다(FakeLLM).
실행: python -m unittest discover -s tests -v"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import CRISIS_NEWS_KEYS, CRISIS_NEWS_TRIES, normalize_state  # noqa: E402
from test_s4_1_story import crisis_start, step  # noqa: E402
from test_story_routes import Stub, engine, system_for  # noqa: E402

NEWS = {"tease": "*전화를 본다.* 태오야. 큰 건. 도시 밖. …새벽에 떠나래.",
        "care": "*난간에 올라앉는다.* 태오가 큰 일을 물어 왔어. 도시 밖. …새벽에 떠나."}
OFF_SCRIPT = "*돌아본다.* …말할 기회는 많았잖아."  # 실제 플레이: 소식 대신 유저 말에 답함
WATCH = "# 위기 (이번 유저 메시지를 보고)"


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


if __name__ == "__main__":
    unittest.main()
