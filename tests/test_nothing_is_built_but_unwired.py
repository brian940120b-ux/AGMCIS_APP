"""
寫好了,但沒有人呼叫 · 2026-09-18

═══ 這一組是為了一個真實的浪費而寫的 ═══
`portfolio/ticket.catch_up()` 有完整實作、有六條測試、有詳細的
docstring —— 而**全倉庫沒有任何地方呼叫它**。

所以「模擬持有 7 個倉、真實帳戶 0 個倉」這件事在面板上永遠看不見,
指令單只會說「今天沒有要按的」。那句話是真的,也是誤導。

它跟這個專案的另一條鐵則是同一件事:

    **一個「看起來存在、實際無作用」的東西,比沒有更糟。**

沒有的東西,人知道自己沒有。有而不通的東西,人以為自己有了。

═══ 測試綠燈為什麼擋不住 ═══
`tests/test_ticket.py` 直接 import `catch_up` 來測 —— 測試本身
就是呼叫者。所以「有沒有被產品程式碼用到」這件事,
**任何單元測試都證明不了**,它們反而讓函式看起來活著。

所以這一組刻意**把 tests/ 排除在呼叫者之外**。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parents[1]

#: 功能的進入點:它們存在的唯一理由,就是被產品程式碼呼叫。
#: 加一個功能而不接上去,這裡就會紅。
#:
#: 這份清單是手寫的 —— 自動推導「哪些函式算功能」會把一堆
#: 內部小工具也算進來,然後這條測試就會被調鬆到沒有意義。
ENTRY_POINTS = {
    "catch_up": "portfolio/ticket.py",
    "make_tickets": "portfolio/ticket.py",
    "verify": "portfolio/ticket.py",
    "recognised": "exchange/bingx/standard.py",
    "rich_positions": "exchange/bingx/standard.py",
    "infer_spec": "exchange/bingx/standard.py",
    "positions_from": "exchange/bingx/standard_usdt.py",
    "liquidation_price": "exchange/bingx/standard_usdt.py",
    "judge_challenger": "portfolio/research.py",
    "block_bootstrap_pvalue": "portfolio/research.py",
    "decide": "portfolio/research.py",
    "merge": "portfolio/research.py",
    "summary": "portfolio/screen.py",
    "standard_cost_caveat": "portfolio/costs.py",
}

SKIP_DIRS = {".git", "__pycache__", "tests", ".venv", "data"}


def product_files() -> list:
    out = []
    for path in BASE.rglob("*.py"):
        if any(part in SKIP_DIRS for part in path.relative_to(BASE).parts):
            continue
        out.append(path)
    return out


def used_names(path: Path) -> set:
    """這個檔案裡**用到**的名字。

    ⚠️ 第一版只看 `ast.Call`,也就是「有沒有被呼叫」。那太窄了 ——
    `_try(sym, usdt.infer_spec, sym)` 是把方法**當 callback 傳進去**,
    它確實被用到了,但它不在 Call 的位置上。第一版因此誤報
    `infer_spec` 沒接上。

    改成看 Load 位置的 Name 與 Attribute:呼叫、當參數傳、指派給
    變數,全部算。而**純 import 不算** —— `from x import y` 不會
    產生 y 的 Name 節點,所以它自動被排除掉了,不用特別處理。

    這個放寬是有代價的:同名的區域變數會造成漏報。而那個方向是
    安全的 —— 漏報讓一條測試變綠,誤報讓一個能用的功能被當成死碼刪掉。
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError):
        return set()
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
            out.add(node.attr)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            out.add(node.id)
    return out


CALLERS = {p: used_names(p) for p in product_files()}


@pytest.mark.parametrize("name,home", sorted(ENTRY_POINTS.items()))
def test_the_feature_is_actually_wired_up(name, home):
    """**有人呼叫它,而且不是它自己、不是測試。**

    `catch_up` 2026-09-13 寫好、2026-09-18 才被發現沒接上 ——
    中間五天,面板上少了「你的真實帳戶差了 7 個倉」這句話。
    """
    home_path = BASE / home
    users = sorted(p.relative_to(BASE) for p, names in CALLERS.items()
                   if name in names and p != home_path)
    assert users, (
        f"{home} 的 {name}() **沒有任何產品程式碼呼叫它**。\n"
        "  一個「看起來存在、實際無作用」的東西,比沒有更糟 ——\n"
        "  沒有的東西,人知道自己沒有;有而不通的東西,人以為自己有了。\n"
        "  (單元測試不算呼叫者:測試本身 import 它,只會讓它看起來活著。)")


def test_the_entry_point_list_points_at_files_that_exist():
    """清單腐爛了要當場知道,不要靜靜跳過一個已經改名的函式。"""
    for name, home in ENTRY_POINTS.items():
        assert (BASE / home).exists(), f"{home} 不見了({name})"


def test_tests_are_not_counted_as_callers():
    """這條守的是這組測試自己的前提。

    如果哪天有人把 tests/ 從 SKIP_DIRS 拿掉,上面每一條都會變成
    永遠通過 —— 而那比沒有這組測試更糟。
    """
    assert "tests" in SKIP_DIRS
    assert not any("tests" in p.relative_to(BASE).parts for p in CALLERS)
