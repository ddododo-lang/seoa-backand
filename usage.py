"""Gemini 사용량 기록 (feat/usage-log).

Gemini 는 응답마다 usage_metadata(입력·출력·생각 토큰 수)를 돌려준다. 서버는 호출마다
- Render 로그에 한 줄: [usage] kind=chat model=… in=… out=… think=… cached=…
- 메모리 합계: 날짜(한국 시간)·종류·모델별 호출 수, 토큰, 예상 비용
을 남기고, GET /usage 로 합계를 보여 준다. 키 값·대화 내용은 기록하지 않는다.

종류(kind): chat = 서아 답장(유저 메시지 1개당 1번), judge = 관계 판정, summary = 대화 요약.
무료 Render 는 잠들거나 다시 배포하면 메모리 합계가 0 부터 다시 센다(since 가 그 시각).
긴 기간 합계는 Render 로그의 [usage] 줄이나 AI Studio Spend 화면으로 본다.
"""
import json
import logging
import os
import threading
from collections import defaultdict
from datetime import datetime, timedelta, timezone

log = logging.getLogger("seoa.llm")
KST = timezone(timedelta(hours=9))

# 100만 토큰당 달러 (입력, 출력). 출력에는 생각(thinking) 토큰도 포함해 매긴다.
# 2026-11 기준 Google 가격표. 바뀌면 Render 환경 변수 USAGE_PRICES 에 JSON 으로 덮어쓴다.
#   예: {"gemini-3.5-flash-lite": [0.30, 1.50]}
DEFAULT_PRICES = {
    "gemini-3.5-flash-lite": (0.30, 1.50),
    "gemini-3.1-flash-lite": (0.25, 1.50),
    "gemini-3.5-flash": (1.50, 9.00),
    "gemini-3-flash-preview": (0.50, 3.00),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-flash-lite": (0.10, 0.40),
}
KINDS = ("chat", "judge", "summary")


def load_prices() -> dict:
    prices = dict(DEFAULT_PRICES)
    raw = os.getenv("USAGE_PRICES", "").strip()
    if raw:
        try:
            prices.update({k: tuple(map(float, v)) for k, v in json.loads(raw).items()})
        except (ValueError, TypeError, AttributeError):
            log.warning("[usage] USAGE_PRICES 를 읽지 못해 기본 가격을 씀")
    return prices


def _n(meta, name: str) -> int:
    try:
        return int(getattr(meta, name, 0) or 0)
    except (TypeError, ValueError):
        return 0


def _empty() -> dict:
    return {"calls": 0, "failed": 0, "input_tokens": 0, "output_tokens": 0, "thinking_tokens": 0, "cached_tokens": 0}


class Usage:
    def __init__(self, clock=lambda: datetime.now(KST)):
        self._clock = clock
        self._lock = threading.Lock()
        self.prices = load_prices()
        self.since = clock()
        self.rows = defaultdict(_empty)  # (날짜, 종류, 모델) → 합계

    def record(self, kind: str, model: str, meta) -> None:
        """성공한 호출 하나. meta 는 response.usage_metadata (없으면 토큰 0)."""
        tin, tout = _n(meta, "prompt_token_count"), _n(meta, "candidates_token_count")
        think, cached = _n(meta, "thoughts_token_count"), _n(meta, "cached_content_token_count")
        with self._lock:
            row = self.rows[(self._clock().date().isoformat(), kind, model)]
            row["calls"] += 1
            row["input_tokens"] += tin
            row["output_tokens"] += tout
            row["thinking_tokens"] += think
            row["cached_tokens"] += cached
        log.info("[usage] kind=%s model=%s in=%d out=%d think=%d cached=%d", kind, model, tin, tout, think, cached)

    def record_failure(self, kind: str) -> None:
        """모든 모델이 실패한 호출 (유저에게는 「…」). 토큰은 0 으로 센다."""
        with self._lock:
            self.rows[(self._clock().date().isoformat(), kind, "(실패)")]["failed"] += 1

    def cost(self, model: str, row: dict) -> float | None:
        price = self.prices.get(model)
        if not price:
            return None
        return (row["input_tokens"] * price[0] + (row["output_tokens"] + row["thinking_tokens"]) * price[1]) / 1_000_000

    def report(self) -> dict:
        with self._lock:
            rows = {k: dict(v) for k, v in self.rows.items()}
        days: dict = {}
        totals = {k: _empty() for k in KINDS}
        by_model: dict = {}
        cost_total, unknown = 0.0, set()
        for (day, kind, model), row in sorted(rows.items()):
            c = self.cost(model, row)
            if c is None and row["calls"]:
                unknown.add(model)
            cost_total += c or 0.0
            days.setdefault(day, {}).setdefault(kind, {})[model] = dict(
                row, est_cost_usd=round(c, 6) if c is not None else None)
            t = totals.setdefault(kind, _empty())
            m = by_model.setdefault(model, dict(_empty(), est_cost_usd=0.0))
            for key in t:
                t[key] += row[key]
                m[key] += row[key]
            m["est_cost_usd"] = round(m["est_cost_usd"] + (c or 0.0), 6)
        chat = totals["chat"]["calls"]
        per_msg = {}
        if chat:
            per_msg = {
                "chat_input_tokens": round(totals["chat"]["input_tokens"] / chat),
                "chat_output_tokens": round((totals["chat"]["output_tokens"] + totals["chat"]["thinking_tokens"]) / chat),
                "judge_summary_calls": round((totals["judge"]["calls"] + totals["summary"]["calls"]) / chat, 2),
                "est_cost_usd_all_in": round(cost_total / chat, 6),  # 답장 + 판정·요약 몫까지
            }
        return {
            "since": self.since.isoformat(timespec="seconds"),
            "note": ("서버가 켜진 뒤(since)부터의 합계. 무료 Render 는 잠들거나 다시 배포하면 0 부터 다시 셈. "
                     "비용은 가격표로 계산한 예상값(실제 청구는 AI Studio Spend). chat 호출 수 = 유저 메시지 수."),
            "totals": totals,
            "by_model": by_model,
            "per_user_message": per_msg,
            "est_cost_usd": round(cost_total, 6),
            "unpriced_models": sorted(unknown),
            "prices_per_1m_usd": {k: list(v) for k, v in self.prices.items()},
            "days": days,
        }


USAGE = Usage()
