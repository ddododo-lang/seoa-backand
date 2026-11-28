"""S1 본편·크리스마스 설정 분리. 키 없이 돈다. 실행: python -m unittest discover -s tests -v"""
import sys
import unittest
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from db import Store  # noqa: E402
from engine import Engine, normalize_state  # noqa: E402
from llm import FakeLLM  # noqa: E402

PROMPT = str(BASE_DIR / "yoon_seoa_prompt.md")
BEFORE = (BASE_DIR / "tests" / "fixtures" / "base_system_before_s1.txt").read_text(encoding="utf-8")
MOTIONS = [{"id": "m20", "name": "도발하기", "emo": "장난"}]
XMAS = {"stage": 3, "affection": 45, "episode": "s2", "episode_stage": 2,
        "episode_stage_name": "티키타카", "location": "서아의 자취방"}
MAIN_ONLY = ["다섯 곳", "마리", "강태오", "비밀1", "크롭 탑", "일째", "출발까지", "2층에서는 피하지 않는다"]
DELETED = " (특별 에피소드 크리스마스의 자취방은 반대로 서아가 먼저 다가간다.)"
# 나눔 때 한 줄이 공통·본편 두 블록으로 갈린 줄: 원래 줄 → 각각 들어 있어야 할 조각
SPLIT = {
    "   - 괄호 안 행동이 서아에게 닿으면(손을 잡는다, 우산을 씌운다 등) 현재 단계의 태도에 맞게 받아들이거나 피한다. "
    "단, 바 '라스트 오더' 2층에서는 피하지 않는다(아래 배경의 2층 원칙).": [
        "   - 괄호 안 행동이 서아에게 닿으면(손을 잡는다, 우산을 씌운다 등) 현재 단계의 태도에 맞게 받아들이거나 피한다.",
        "   - 단, 바 '라스트 오더' 2층에서는 피하지 않는다(아래 배경의 2층 원칙).",
    ],
    "- 바 '라스트 오더'의 사장. 과거 도시 최고의 해결사였고, 은퇴했지만 실력은 그대로다. 바에서 시비가 붙으면 몇 초 만에 정리한다.": [
        "- 바 '라스트 오더'의 사장.",
        "- 과거 도시 최고의 해결사였고, 은퇴했지만 실력은 그대로다. 바에서 시비가 붙으면 몇 초 만에 정리한다.",
    ],
    # S2 유저 이름: [말투]의 이름 한 줄을 네 줄로 바꿈
    '- 유저의 이름은 지어내지 않는다. 유저가 대화에서 자기 이름을 말하면(지금까지의 이야기에 남아 있어도 같다) '
    '그때부터 진지한 순간에만 그 이름을 부른다. 이름을 모르면 계속 "사장님"이다.': [
        "- 유저의 이름은 현재 상태의 「유저 이름」이다. 없음이면 지어내지 않고, 유저가 대화에서 자기 이름을 말하면 그 이름을 쓴다.",
        '- 본 이야기에서는 현재 상태의 「이름 부르기」를 따른다. 이름을 부르지 않을 때는 "사장님"이다.',
        '- 이름을 부를 수 있을 때도 진지한 순간에만 이름을 부르고, 평소에는 "사장님"이다.',
    ],
}
# S3 대화 품질: 바꾸거나 지운 줄(앞부분으로 찾음) → 대신 들어 있어야 할 조각 (빈 목록이면 지움)
S3_CHANGED = {
    "2. 응답은 OUTPUT_FORMAT의 JSON": ["2. 응답은 OUTPUT_FORMAT의 JSON 하나로만 한다. action은 지문(서아의 행동·표정·속마음), dialogue는 서아의 대사."],
    "4. action은 이번 턴에 고른 몸짓": ["4. 외모는 화면에 보이므로 길게 묘사하지 않는다."],
    "12. 반복 금지": ["12. 반복 금지: 현재 상태의 \"최근 서아 응답\"에 있는 표현, 문장 시작, 말버릇을 다시 쓰지 않는다. 아래"],
    "17. 유저 메시지 「[유저가 선물함": ["지문·대사는 [말투]·[지문] 규칙대로."],
    "- 설명하지 않는다. 이유·사정을": [],
    "- 대사는 많아야 두 마디.": [],
    "- 지문도 짧게.": [],
    "- 예: 「방금 찍었어.": [],
    # 이름 구분(feat/name-clarity): 지문 주어 규칙을 넓힘
    "3. 서술에서 유저는": ['3. 서술(지문)에서 유저는 "당신", 서아는 "서아"(또는 주어 생략)로만 지칭한다.'],
}


def s3_parts(line):
    for head, parts in S3_CHANGED.items():
        if line.startswith(head):
            return parts
    return None


def engine():
    return Engine(llm=FakeLLM(), store=Store(":memory:"), prompt_path=PROMPT)


def system(state):
    return engine()._build_system(normalize_state(state), None, MOTIONS)


class SplitTest(unittest.TestCase):
    def test_xmas_has_no_main_world(self):
        s = system(XMAS)
        for word in MAIN_ONLY:  # S1-1: 「마리」「강태오」도 크리스마스 system 어디에도 없다
            self.assertNotIn(word, s, word)
        self.assertIn("본 이야기의 인물·사건·의뢰는 꺼내지 않는다.", s)
        self.assertNotIn("# 주변 인물", s)
        self.assertNotIn("# 비밀", s)
        self.assertNotIn("# 배경", s)

    def test_3d_line_is_common(self):  # S1-1: 공통 [몸짓] 절 맨 위
        base = engine().blocks["BASE_SYSTEM"]
        self.assertIn("# 몸짓\n- 화면에는 서아의 3D 모습이 함께 보이고, 네가 고른 몸짓이 그대로 재생된다.", base)
        self.assertNotIn("3D 모습", engine().blocks["MAIN_WORLD"])
        self.assertIn("3D 모습", system(XMAS))

    def test_main_has_main_world_and_every_old_line(self):
        s = system({"stage": 5, "day": 4, "departure_days_left": 2, "revealed_secrets": ["비밀1"]})
        for word in MAIN_ONLY:
            self.assertIn(word, s, word)
        self.assertNotIn(DELETED.strip(), s)
        for line in BEFORE.split("\n"):
            line = line.replace(DELETED, "")
            if not line.strip():
                continue
            s3 = s3_parts(line)
            for part in SPLIT.get(line, [line]) if s3 is None else s3:
                self.assertIn(part, s, part)

    def test_main_world_follows_base_then_state(self):
        e = engine()
        s = system({"stage": 2, "day": 3})
        i_base, i_world = s.index(e.blocks["BASE_SYSTEM"]), s.index(e.blocks["MAIN_WORLD"])
        self.assertEqual(i_base, 0)
        self.assertEqual(i_world, len(e.blocks["BASE_SYSTEM"]) + 2)
        self.assertLess(i_world, s.index("# 현재 상태"))

    def test_both_have_tone_and_rules(self):
        for st in ({}, XMAS):
            s = system(st)
            self.assertIn("기본 반말", s)
            self.assertIn("절대 규칙", s)

    def test_xmas_state_block(self):
        s = system(XMAS)
        self.assertIn("- 이 밤: 크리스마스 이브, 밤 11시에 시작된 정전", s)
        self.assertIn("- 단계: 2 (티키타카)", s)
        self.assertIn("- 장소: 서아의 자취방", s)
        self.assertNotIn("호감도:", s)
        self.assertNotIn("{{", s)

    def test_midnight_line_only_with_turns_left(self):
        self.assertIn("- 자정까지 남은 유저 메시지: 7", system(dict(XMAS, episode_turns_left=7)))
        self.assertIn("- 자정까지 남은 유저 메시지: 0", system(dict(XMAS, episode_turns_left=0)))
        self.assertNotIn("자정까지", system(XMAS))

    def test_main_has_no_episode_state(self):
        self.assertNotIn("크리스마스 이브, 밤 11시에 시작된 정전", system({}))


if __name__ == "__main__":
    unittest.main()
