"""yoon_seoa_prompt.md 파일을 블록 단위로 잘라서 불러온다."""
import re
from pathlib import Path

BLOCK_RE = re.compile(r"<!-- BEGIN:(\w+) -->\n(.*?)\n<!-- END:\1 -->", re.S)

REQUIRED = ["BASE_SYSTEM", "MAIN_WORLD", "STATE_BLOCK", "EPISODE_STATE", "MOTION_BLOCK", "OUTPUT_FORMAT", "OPENING", "JUDGE_SYSTEM", "FIRST_NIGHT",
            "STAGE_UP_2", "STAGE_UP_3", "STAGE_UP_3_NAMED", "STAGE_UP_4", "STAGE_UP_6",
            "EVENT_NAME_REFUSED", "EVENT_NAME_FORGIVEN", "EVENT_NAME_REFUSED_AGAIN", "NAME_WATCH",
            "EVENT_WALK_HOME", "ROUTE_OUTSIDE", "EVENT_ALLEY", "EVENT_ALLEY_CARE", "ROUTE_PURSUE_TEASE", "ROUTE_PURSUE_CARE",
            "ROUTE_MORNING_TEASE", "ROUTE_MORNING_CARE", "GIFT_MOMENT", "XMAS_LATE",
            "HINT_1", "HINT_2", "HINT_3", "HINT_4", "HINT_5", "HINT_6",
            "CRISIS_TEASE", "CRISIS_CARE", "CRISIS_WATCH", "WON_TEASE", "WON_CARE", "WON_RECONCILE",
            "EVENT_RIFT_START", "RIFT", "EVENT_RECONCILE_OPEN", "RECONCILE_WATCH", "RECONCILE_REMEMBERED",
            "NIGHT_END_1", "NIGHT_END_TEASE", "NIGHT_END_CARE",
            "NIGHT_START_2", "NIGHT_START_3_TEASE", "NIGHT_START_3_CARE", "RIFT_NIGHT"] + [
    f"EVENT_{c}_{n}" for c in ("MARI", "TAEO") for n in (1, 2, 3)] + [
    f"STAGE_{i}" for i in range(1, 7)
]


def load_blocks(path: str | Path) -> dict[str, str]:
    text = Path(path).read_text(encoding="utf-8")
    blocks = {m.group(1): m.group(2).strip() for m in BLOCK_RE.finditer(text)}
    missing = [name for name in REQUIRED if name not in blocks]
    if missing:
        raise ValueError(f"프롬프트 파일에 블록이 없습니다: {missing}")
    return blocks


def render(template: str, values: dict) -> str:
    """{{변수}}를 값으로 바꾼다."""
    out = template
    for key, value in values.items():
        out = out.replace("{{" + key + "}}", str(value))
    return out
