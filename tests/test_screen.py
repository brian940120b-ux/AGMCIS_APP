"""
合約幣種篩選 · 2026-09-13

守一件事,而它是這個專案反覆犯的那個錯:
**「不知道」不准被算成「通過」。**

一個因為沒問到而沒發現問題的檢查,如果回報通過,就是在說謊。
在挑合約標的這件事上,那句謊話的代價是:一張永遠不會成交的單,
或一個流動性薄到強平時沒人接的倉。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from portfolio.screen import (BLOCK, GATES, PASS, UNKNOWN, Candidate,
                              evaluate, summary)

SYMS = ["BTC-USDT", "TINY-USDT", "NEW-USDT", "GONE-USDT", "MYSTERY-USDT"]
VOL = {"BTC-USDT": 5e9, "TINY-USDT": 1e5, "NEW-USDT": 8e8,
       "GONE-USDT": 3e8}                       # MYSTERY 沒有成交額
BARS = {"BTC-USDT": 1400, "TINY-USDT": 1400, "NEW-USDT": 30,
        "GONE-USDT": 1400, "MYSTERY-USDT": 1400}
KNOWN = {"BTC-USDT": True, "TINY-USDT": True, "NEW-USDT": True,
         "GONE-USDT": False, "MYSTERY-USDT": None}
STATUS = {s: True for s in SYMS}


def run(**over):
    kw = dict(symbols=SYMS, volumes=VOL, bars=BARS, recognised=KNOWN,
              min_volume=1e7, min_bars=1000, tradable_status=STATUS)
    kw.update(over)
    return {c.symbol: c for c in evaluate(**kw)}


def test_a_healthy_symbol_passes_every_gate():
    c = run()["BTC-USDT"]
    assert c.tradable is True
    assert c.verdict == "可以"
    assert all(c.gates[g] == PASS for g in GATES)


def test_thin_liquidity_is_blocked_and_says_why_it_matters_for_futures():
    c = run()["TINY-USDT"]
    assert c.tradable is False
    assert c.gates["流動性"] == BLOCK
    assert "強平時沒有人接" in c.notes["流動性"]


def test_too_little_history_is_blocked():
    c = run()["NEW-USDT"]
    assert c.gates["歷史長度"] == BLOCK
    assert "50 日均線" in c.notes["歷史長度"]


def test_a_symbol_the_product_does_not_know_is_blocked():
    c = run()["GONE-USDT"]
    assert c.gates["標準合約認得"] == BLOCK
    assert c.tradable is False


# ══════════════════════════════════════════════════════════
# 「不知道」不是「通過」
# ══════════════════════════════════════════════════════════
def test_unknown_is_not_tradable():
    """MYSTERY 每一關都過,只有「認不認得」問不出來 —— 就是不能交易。"""
    c = run()["MYSTERY-USDT"]
    assert c.gates["標準合約認得"] == UNKNOWN
    assert c.tradable is False
    assert c.verdict.startswith("不知道")


def test_missing_volume_is_unknown_not_zero():
    """問不到成交額 ≠ 成交額是 0。回 0 會讓它看起來像「薄到不能交易」,
    那是一個**看起來合理的錯答案**,而它會掩蓋掉「我們沒問到」。"""
    c = evaluate(["X-USDT"], {}, {"X-USDT": 1400}, {"X-USDT": True},
                 1e7, 1000, {"X-USDT": True})[0]
    assert c.gates["流動性"] == UNKNOWN
    assert c.quote_volume is None


def test_missing_status_is_unknown_not_assumed_tradable():
    c = evaluate(["X-USDT"], {"X-USDT": 1e9}, {"X-USDT": 1400},
                 {"X-USDT": True}, 1e7, 1000, {})[0]
    assert c.gates["交易所狀態"] == UNKNOWN
    assert c.tradable is False


def test_the_summary_counts_unknown_separately():
    """**不知道單獨一格,不併進任何一邊。** 併進去就看不見了。"""
    got = summary(list(run().values()))
    assert got["total"] == 5
    assert got["tradable"] == 1                  # 只有 BTC
    assert got["blocked"] == 3                   # TINY / NEW / GONE
    assert got["unknown"] == 1                   # MYSTERY
    assert got["tradable"] + got["blocked"] + got["unknown"] == got["total"]


# ══════════════════════════════════════════════════════════
# 每一關都要留下答案
# ══════════════════════════════════════════════════════════
def test_every_gate_is_recorded_even_the_ones_that_passed():
    """「為什麼這個幣不在裡面」必須是一個回答得出來的問題。"""
    for c in run().values():
        assert set(c.gates) == set(GATES), c.symbol


def test_the_recognised_gate_admits_it_is_only_necessary():
    """「allOrders 認得」不等於「可以交易」—— 這句話一定要在。"""
    c = run()["BTC-USDT"]
    assert "必要條件,不是充分條件" in c.notes["標準合約認得"]


def test_tradable_ones_come_first_then_by_volume():
    ordered = evaluate(SYMS, VOL, BARS, KNOWN, 1e7, 1000, STATUS)
    assert ordered[0].symbol == "BTC-USDT"
    rest = [c.quote_volume or 0.0 for c in ordered[1:]]
    assert rest == sorted(rest, reverse=True)


def test_the_app_symbol_drops_the_dash():
    assert Candidate(symbol="BTC-USDT").app_symbol == "BTCUSDT"
