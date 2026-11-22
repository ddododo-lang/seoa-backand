"""yoon_seoa_prompt.md 파일을 블록 단위로 잘라서 불러온다."""
import re
from pathlib import Path

BLOCK_RE = re.compile(r"<!-- BEGIN:(\w+) -->\n(.*?)\n<!-- END:\1 -->", re.S)

REQUIRED = ["BASE_SYSTEM", "STATE_BLOCK", "OPENING", "JUDGE_SYSTEM"] + [
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
