"""S3 대화 품질 키 없는 테스트. 실행: python -m unittest discover -s tests -v"""
import sys
import unittest
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from db import Store  # noqa: E402
from engine import Engine, normalize_state  # noqa: E402
from llm import FakeLLM  # noqa: E402

PROMPT = str(BASE_DIR / "yoon_seoa_prompt.md")
MOTIONS = [{"id": "m20", "name": "도발하기", "emo": "장난"}]
XMAS = {"stage": 3, "affection": 45, "episode": "s2", "episode_stage": 2,
        "episode_stage_name": "티키타카", "location": "서아의 자취방"}

OLD_LINES = [
    "action은 서아의 행동·장면 묘사 1~2문장(짧게)",
    "dialogue는 서아의 대사 한두 마디",
    "유저가 바로 반응할 수 있는 지점(질문, 행동, 선택)에서 끝낸다.",
    "action은 이번 턴에 고른 몸짓(motion_id)과 같은 장면이어야 한다.",
    "감각 디테일을 하나 넣는다",
    "매 응답은 새로운 것 하나(정보, 질문, 행동, 장소나 분위기의 변화)를 더한다.",
    "설명하지 않는다. 이유·사정을 덧붙이지 않는다.",
    "대사는 많아야 두 마디.",
    "지문도 짧게. 한 문장이 기본이고 많아야 두 문장.",
    "「방금 찍었어. 옆에 있어도 보내는 거야. …기록용.」",
    "지문 한 문장, 대사 한두 마디",
    "서아의 행동·장면 묘사 1~2문장, 짧게 (별표 없이)",
    "이 목록에서 장면에 가장 맞는 몸짓 하나를 골라",
]
COMMON_SECTIONS = ["# 대화를 이끄는 법", "# 지문", "# 단계별 말투와 스킨십"]
NEW_RULES = [
    "2. 응답은 OUTPUT_FORMAT의 JSON 하나로만 한다. action은 지문(서아의 행동·표정·속마음), dialogue는 서아의 대사.",
    "4. 외모는 화면에 보이므로 길게 묘사하지 않는다.",
    "대사는 1~3문장. 짧고 툭 던지는 말투는 그대로지만, 단답 한 마디로만 끝내지 않는다.",
    "「뭐 할래?」「알아서 해」처럼 결정을 유저에게 넘기지 않는다.",
    "닫는 말만으로 답장을 끝내지 않는다.",
    "질문은 세 번에 한 번 정도.",
    "지문은 1~3문장: 행동 하나 + 표정 하나 + 속마음 한 조각.",
    "칼라 만지기는 진심을 말하는 순간에만.",
    "지문에서 유저는 언제나 「당신」이다.",
    "| 3 썸 | 놀리다가 말끝이 흐려진다.",
    "받아들이는 범위 안의 스킨십은 피하지 않는다.",
    '"action": "지문 1~3문장(행동·표정·속마음, 별표 없이)"',
    '"dialogue": "서아의 대사 1~3문장(따옴표 없이)"',
]
MOTION_LINES = [
    "- 지문의 표정·감정과 맞는 몸짓 하나를 고른다. 찌푸림·짜증·슬픔·긴장·당황 장면에 웃는 몸짓을 고르지 않는다.",
    "- 웃음 몸짓은 지문에서 실제로 웃을 때만, 「자신감 있게 설득하기」는 무언가를 자신만만하게 주장하거나 설득할 때만.",
    "- 맞는 것이 없으면 null(앱이 기본 대기 동작을 재생). 몸짓에 맞추려고 없던 행동을 지어내지 않는다.",
    "- 목록에 없는 id는 쓰지 않는다.",
]
PLACE_RULES = [
    "# 장소와 일관성",
    "서아가 유저를 그곳으로 데려가거나 안내하지 않는다.",
    "한 답장 안에서 장소가 두 번 바뀌지 않는다.",
    "지금 장면에 없던 물건(이불, 담요, 우산, 소파 등)을 갑자기 쓰지 않는다.",
    "유저의 고백, 함께 한 일을 부정하지 않는다.",
]


def engine(llm=None):
    return Engine(llm=llm or FakeLLM(), store=Store(":memory:"), prompt_path=PROMPT)


def system(state):
    st = normalize_state(state)
    return engine()._build_system(st, st["pending_event"], MOTIONS)


class DialogueRulesTest(unittest.TestCase):  # A9
    def test_old_lines_gone(self):
        for st in ({"stage": 2}, XMAS):
            s = system(st)
            for line in OLD_LINES:
                self.assertNotIn(line, s, line)

    def test_main_has_all_new_sections(self):
        for stage in (1, 2, 3, 4, 5, 6):
            s = system({"stage": stage})
            for text in COMMON_SECTIONS + NEW_RULES + MOTION_LINES + PLACE_RULES:
                self.assertIn(text, s, f"{stage}: {text}")

    def test_xmas_has_common_but_not_place(self):
        s = system(XMAS)
        for text in COMMON_SECTIONS + NEW_RULES + MOTION_LINES:
            self.assertIn(text, s, text)
        for text in PLACE_RULES:
            self.assertNotIn(text, s, text)

    def test_sections_live_in_right_blocks(self):
        e = engine()
        for name in COMMON_SECTIONS:
            self.assertIn(name, e.blocks["BASE_SYSTEM"])
        self.assertIn("# 장소와 일관성", e.blocks["MAIN_WORLD"])
        self.assertNotIn("# 장소와 일관성", e.blocks["BASE_SYSTEM"])
        self.assertTrue(e.blocks["MOTION_BLOCK"].rstrip().endswith("\n".join(MOTION_LINES)))

    def test_kept_lines(self):
        s = system({"stage": 4})
        self.assertIn("아래 [말투]의 대표 대사와 [예시]의 문장은 말투 참고용이며 그대로 쓰지 않는다.", s)
        self.assertIn("키스는 하지 않는다.", s)  # STAGE_4 그대로
        self.assertIn("기본 반말. 짧고 나른한 문장.", s)


if __name__ == "__main__":
    unittest.main()
