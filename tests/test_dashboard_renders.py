"""
面板要**渲染得出來** · 2026-09-18

═══ 這一組是為了一個白畫面而寫的 ═══
執政官打開面板,整頁只有一行:

    面板渲染失敗:TypeError: bad operand type for unary +: 'str'

原因在 `_ticket_card()` 裡,是我自己上一筆 commit 改出來的:

    +
    # 填完之後用來核對的 —— 不是拿來填的
    + '<tr>...'

第一個 `+` 接續上一段,是二元加號;第二個 `+` 就變成**一元加號作用
在字串上**。`+"字串"` 在 Python 是 TypeError。中間隔了一行註解,
眼睛掃過去看不出來。

═══ 為什麼 453 條測試全綠 ═══
`test_ticket.py` 測的是 `Ticket.fields()` 回什麼,
`test_scripts_actually_start.py` 測的是行程**綁不綁得上埠** ——
它在 `s.connect_ex(...) == 0` 那一刻就 return 了,
從來沒有真的抓過一次頁面。

    綁上埠不代表渲染得出來。

而它壞掉的方式一樣惡劣:handler 把例外接住、回 200、印一行字。
systemd 說 active、deploy.sh 說換好了、/health 是好的 ——
只有真的用瀏覽器打開的人知道。那個人是執政官,不是我。

這一組就是那個「先我一步打開頁面的人」。
"""
from __future__ import annotations

import ast
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

import scripts.dashboard as dash
from portfolio.ticket import OPEN_LONG, build

NOW = datetime(2026, 9, 18, 8, 0, tzinfo=timezone.utc)


def a_ticket():
    return build(symbol="AAVEUSDT", action=OPEN_LONG, quantity=5.4,
                 price=128.52, leverage=3.0, stop_pct=25.0,
                 strategy="50MA", signal_day="2026-09-18",
                 exit_price=112.40, exit_rule="跌破 50 日均線", now=NOW)


# ══════════════════════════════════════════════════════════
# 一、指令單那張卡 —— 就是炸掉的那一塊
# ══════════════════════════════════════════════════════════
def test_the_ticket_card_actually_renders():
    """**這條就是那個白畫面。** 不是測它回什麼,是測它回得來。"""
    html = dash._ticket_card(a_ticket())
    assert html.startswith('<div class="ticket">')
    assert html.count("<table>") == html.count("</table>")


def test_the_card_shows_its_sections_in_order():
    """要填的 → 填完核對 → 打算在哪裡結束。順序照動手的先後。"""
    html = dash._ticket_card(a_ticket())
    fill = html.index("要填的")
    check = html.index("填完之後畫面上應該是")
    exit_ = html.index("打算在哪裡結束")
    assert fill < check < exit_, "區塊順序亂了 —— 照著填會填錯"


def test_every_field_the_app_asks_for_reaches_the_page():
    """`fields()` 對了但沒印出來,跟沒做一樣。"""
    t = a_ticket()
    html = dash._ticket_card(t)
    for label, value, _ in t.fields():
        assert label in html, f"要填的「{label}」沒出現在面板上"
        assert value in html, f"「{label}」的值 {value} 沒出現在面板上"
    for label, value, _ in t.verify_after():
        assert label in html, f"要核對的「{label}」沒出現在面板上"
    for label, value, _ in t.exit_plan():
        assert label in html, f"出場計畫的「{label}」沒出現在面板上"
        assert value in html, f"「{label}」的值 {value} 沒出現在面板上"


# ══════════════════════════════════════════════════════════
# 二、同一個錯誤不准再出現在任何地方
# ══════════════════════════════════════════════════════════
def test_no_stray_unary_plus_anywhere_in_the_repo():
    """`+` 接在一段字串串接後面又換行再 `+` —— 整個倉庫掃一次。

    這個錯誤語法完全合法,編譯得過、import 得過、靜態檢查看不出來,
    要到**那一行真的被執行**才炸。所以用 AST 掃,不是等它跑到。
    """
    bad = []
    for f in sorted(BASE.rglob("*.py")):
        if ".git" in f.parts or "__pycache__" in f.parts:
            continue
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
        except SyntaxError:
            continue
        for n in ast.walk(tree):
            if isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.UAdd):
                bad.append(f"{f.relative_to(BASE)}:{n.lineno}")
    assert not bad, (
        "一元 `+` —— 在 Python 裡對字串是 TypeError,對數字是沒有作用。\n"
        "兩種情況都表示這裡本來想寫的是二元加號,中間被註解或換行隔開了:\n  "
        + "\n  ".join(bad))
