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
    html = dash._ticket_card(a_ticket(), now=NOW)
    assert html.startswith('<div class="ticket" ')
    assert html.count("<table>") == html.count("</table>")


def test_the_card_shows_its_sections_in_order():
    """要填的 → 填完核對 → 打算在哪裡結束。順序照動手的先後。"""
    html = dash._ticket_card(a_ticket(), now=NOW)
    fill = html.index("要填的")
    check = html.index("填完之後畫面上應該是")
    exit_ = html.index("打算在哪裡結束")
    assert fill < check < exit_, "區塊順序亂了 —— 照著填會填錯"


def test_every_field_the_app_asks_for_reaches_the_page():
    """`fields()` 對了但沒印出來,跟沒做一樣。"""
    t = a_ticket()
    html = dash._ticket_card(t, now=NOW)
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


# ══════════════════════════════════════════════════════════
# 三、過期的指令單要撤掉 —— 但不是消失
# ══════════════════════════════════════════════════════════
#
# 2026-09-18 執政官:「如果訊號單有效期限過了就撤掉。」
#
# 撤掉 = **不能再按**。撤掉 ≠ 消失:一張安靜不見的單會讓畫面變成
# 「今天沒有要按的」,而那是假話 —— 實際上是「有,但窗口關了」。
# 兩者的處置完全不同:前者什麼都不用做,後者要等下一張重算出來。

def an_expired_ticket():
    from datetime import timedelta
    return build(symbol="AAVEUSDT", action=OPEN_LONG, quantity=5.4,
                 price=128.52, leverage=3.0, stop_pct=25.0,
                 strategy="50MA", signal_day="2026-09-18",
                 exit_price=112.40, exit_rule="跌破 50 日均線",
                 now=NOW - timedelta(hours=3))


def test_an_expired_ticket_is_marked_and_cannot_be_tapped():
    html = dash._ticket_card(an_expired_ticket(), now=NOW)
    assert 'class="ticket gone"' in html
    assert "已過期 · 不要按" in html
    assert "不要照它按" in html


def test_an_expired_ticket_still_shows_what_it_was():
    """**撤掉不是消失。** 看得到它曾經在,才知道錯過的是什麼。"""
    html = dash._ticket_card(an_expired_ticket(), now=NOW)
    assert "AAVEUSDT" in html
    assert "系統會重算" in html, "要說接下來會怎樣,不然看起來像壞了"


def test_a_live_ticket_is_not_marked_expired():
    html = dash._ticket_card(a_ticket(), now=NOW)
    assert 'class="ticket gone"' not in html
    assert "已過期" not in html


def test_every_ticket_carries_an_unambiguous_utc_deadline():
    """**時區不能靠猜。**

    前端用 Date.parse 讀這個字串。沒有 Z 或 +00:00 的話,瀏覽器會
    當成**手機本地時間** —— 在 UTC+8 就是差 8 小時,而一張早該撤掉
    的單會看起來還有八小時可用。
    """
    import re
    html = dash._ticket_card(a_ticket(), now=NOW)
    m = re.search(r'data-until="([^"]+)"', html)
    assert m, "少了 data-until,前端不知道什麼時候該撤"
    assert m.group(1).endswith("Z"), f"時區不明確:{m.group(1)}"


def test_the_page_withdraws_tickets_without_waiting_for_a_reload():
    """真正會過期的是**開著沒關的頁面** —— 伺服器每 180 秒重算一次,
    但手機擺著半小時,那張單就死在畫面上了,而它跟活的長得一樣。"""
    page = dash.render()
    assert "setInterval(tickExpiry, 1000)" in page
    assert ".ticket.gone .cp{pointer-events:none" in page, \
        "撤掉之後複製鈕還能按 —— 那等於沒撤"
