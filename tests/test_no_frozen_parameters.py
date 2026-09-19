"""
策略參數不准寫死在「給人看的字」裡 · 2026-09-19

執政官:「去反查看有沒有哪裡不對。」

反查出來最常見的一類,是同一個錯的第五次:

  · 出場價說明寫死「策略出場是均線」   (2026-09-18 修)
  · 止損說明寫死「策略出場是均線」     (2026-09-18 修)
  · 證據那行寫死「50 日均線」          (2026-09-18 修)
  · 每張指令單的「訊號」寫死「收盤站上 50 日均線」(2026-09-19 修)
  · 幣種卡寫死「50 日均線」「27%」     (2026-09-19 修)

它們的共通點是:**寫的當下是真的。** 而它們會在策略換掉的那一刻
同時變成假話,旁邊還配著另一條線的數字 —— 一句看起來有憑有據的錯話,
比沒有說明糟得多。

所以不靠記性,靠這條測試:**現役參數一旦改動,凡是把舊值寫進
使用者看得到的字串的地方,這裡就會紅。**
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from portfolio.paper import STRATEGY, VOL_TARGET_ANNUAL_PCT

ROOT = Path(__file__).resolve().parents[1]

#: 只看**會被印出來給人看**的地方。回測說明、決策紀錄那種「當時的
#: 分析」不在此列 —— 它們記錄的是一次已經發生的判斷,本來就該凍住。
WATCHED = ("portfolio/orders.py", "portfolio/ticket.py",
           "portfolio/scorecard.py", "scripts/dashboard.py")

#: 現役參數在文字裡的樣子。從設定推出來,不手寫 ——
#: 手寫的話這條測試自己就變成下一個寫死的東西。
def _forbidden() -> list:
    out = []
    m = re.search(r"(\d+)\s*日均線", STRATEGY)
    if m:
        n = m.group(1)
        out += [f"{n} 日均線", f"{n}日均線"]
    out.append(f"{VOL_TARGET_ANNUAL_PCT:g}%")
    return out


def _user_facing_strings(path: Path):
    """模組層的字串常數,排除 docstring 與註解。"""
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    docstrings = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef,
                          ast.AsyncFunctionDef)):
            d = ast.get_docstring(n, clean=False)
            if d:
                docstrings.add(d)
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            if n.value in docstrings:
                continue
            yield n.lineno, n.value


def test_no_live_parameter_is_frozen_into_user_facing_text():
    bad = []
    for rel in WATCHED:
        path = ROOT / rel
        if not path.exists():
            continue
        for lineno, text in _user_facing_strings(path):
            for token in _forbidden():
                if token in text:
                    bad.append(f"{rel}:{lineno}  「{token}」 ← {text[:60]}")
    assert not bad, (
        "現役策略參數被寫死在使用者看得到的字串裡。\n"
        "它現在是真的,而它會在換策略的那一刻變成假話 —— \n"
        "旁邊還配著另一條線的數字。改成從設定讀:\n  "
        + "\n  ".join(bad))


def test_the_order_reason_names_the_strategy_not_a_fixed_rule():
    """每張指令單的「訊號」欄。

    不去拼「站上/突破」那種句子 —— 進場條件與出場條件對突破族根本
    不是同一條線(進場看 N 日高,出場看 M 日低),拼出來的會是另一種
    假話。直接講策略的名字,它永遠是準的。
    """
    from portfolio.orders import build_orders
    orders = build_orders({"BTC-USDT": 0.14}, {}, 10000.0,
                          {"BTC-USDT": 70000.0}, {"BTC-USDT": 60000.0},
                          strategy="100日均線之上才持有")
    assert orders, "沒產出訂單,這條測試沒測到東西"
    assert "100日均線之上才持有" in orders[0].reason
    assert "50 日均線" not in orders[0].reason


def test_an_order_without_a_strategy_name_does_not_invent_one():
    """拿不到策略名的時候說「現役策略」,不要編一個出來。"""
    from portfolio.orders import build_orders
    orders = build_orders({"BTC-USDT": 0.14}, {}, 10000.0,
                          {"BTC-USDT": 70000.0}, {"BTC-USDT": 60000.0})
    assert "現役策略" in orders[0].reason
