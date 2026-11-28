"""이름 구분 (feat/name-clarity): 유저 이름과 캐릭터 이름이 헷갈리지 않게. 키 없이 돈다.
실행: python -m unittest discover -s tests -v"""
import json
import sys
import unittest
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "tests"))

from engine import SUMMARY_SYSTEM, name_clash_note, name_out_of_action, normalize_state  # noqa: E402
from llm import FakeLLM  # noqa: E402
from test_s2_user_name import engine, system_for  # noqa: E402


class ReplyStub(FakeLLM):
    """정해 둔 답장 JSON 을 돌려주는 LLM."""

    def __init__(self, action, dialogue="…왔어."):
        super().__init__()
        self.out = json.dumps({"action": action, "dialogue": dialogue, "motion_id": None,
                               "emotion": "평온", "location": None}, ensure_ascii=False)

    def chat_json(self, system, messages, schema, budget_sec=None):
        return self.out


class ActionNameTest(unittest.TestCase):
    def test_name_ssi_becomes_dangsin_with_josa(self):
        self.assertEqual(name_out_of_action("도윤 씨가 웃자 서아가 잔을 민다.", "도윤"), "당신이 웃자 서아가 잔을 민다.")
        self.assertEqual(name_out_of_action("서아가 도윤씨를 본다.", "도윤"), "서아가 당신을 본다.")
        self.assertEqual(name_out_of_action("도윤 씨는 조용하다. 도윤 씨와 걷는다.", "도윤"), "당신은 조용하다. 당신과 걷는다.")
        self.assertEqual(name_out_of_action("서아가 도윤 씨의 손을 잡는다.", "도윤"), "서아가 당신의 손을 잡는다.")
        self.assertEqual(name_out_of_action("도윤 씨에게 기댄다.", "도윤"), "당신에게 기댄다.")

    def test_plain_word_same_as_name_untouched(self):  # 이름만 나온 건 바꾸지 않음(낱말일 수 있음)
        self.assertEqual(name_out_of_action("하늘이 밝아 온다.", "하늘"), "하늘이 밝아 온다.")
        self.assertEqual(name_out_of_action("서아가 잔을 민다.", ""), "서아가 잔을 민다.")
        self.assertIsNone(name_out_of_action(None, "도윤"))

    def test_reply_applies_only_to_action(self):
        eng = engine(ReplyStub("서아가 도윤 씨를 빤히 본다.", "도윤 씨, 그건 아니지."))
        out = eng.reply({"stage": 3, "user_name": "도윤", "name_state": "accepted"}, "안녕", [], [])
        self.assertEqual(out["action"], "서아가 당신을 빤히 본다.")
        self.assertEqual(out["dialogue"], "도윤 씨, 그건 아니지.")  # 대사는 이름 규칙대로 그대로


class ClashTest(unittest.TestCase):
    def test_clash_names(self):
        for name, who in (("서아", "서아"), ("윤서아", "서아"), ("마리", "마리"), ("태오", "태오"), ("강태오", "태오")):
            note = name_clash_note(name)
            self.assertIn(f"인물 {who}", note)
            self.assertIn(f"「{name} 씨」", note)
        self.assertIsNone(name_clash_note("도윤"))
        self.assertIsNone(name_clash_note(""))

    def test_note_in_system_main_and_episode(self):
        self.assertIn("# 이름 구분", system_for({"stage": 2, "user_name": "마리"}))
        self.assertIn("# 이름 구분", system_for({"stage": 2, "user_name": "서아", "episode": "s2", "episode_stage": 1}))
        self.assertNotIn("# 이름 구분", system_for({"stage": 2, "user_name": "도윤"}))


class PromptRuleTest(unittest.TestCase):
    def test_rules_in_prompt(self):
        sys_text = system_for(normalize_state({"stage": 2, "user_name": "도윤"}))
        self.assertIn("서아는 \"서아\"(또는 주어 생략)로만", sys_text)
        self.assertIn("지문에 「나는」「내가」를 쓰지 않는다", sys_text)
        self.assertIn("「○○ 씨」 포함", sys_text)

    def test_summary_names(self):
        self.assertIn("유저는 「유저」, 캐릭터는 「서아」로만", SUMMARY_SYSTEM)


if __name__ == "__main__":
    unittest.main()
