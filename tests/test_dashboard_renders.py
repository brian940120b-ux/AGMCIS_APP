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


# ══════════════════════════════════════════════════════════
# 四、指令單的欄位內容逐項對 · 2026-09-18
# ══════════════════════════════════════════════════════════
#
# 執政官:「檢查訊號單資訊…有些資訊也不太對。
#           做多出場價怎麼會比開倉價低?」
#
# 查下去,四個錯,而且三個是同一類:把一個**開倉**的假設套到別的
# 情況上,或把一個**當時成立**的字串寫死。

def a_close_ticket():
    from portfolio.ticket import CLOSE
    return build(symbol="AAVEUSDT", action=CLOSE, quantity=5.4,
                 price=128.52, leverage=3.0, stop_pct=25.0,
                 strategy="50MA", signal_day="2026-09-18",
                 exit_price=112.40, exit_rule="跌破 50 日均線", now=NOW)


def test_a_close_ticket_never_asks_for_margin_or_a_stop():
    """**這是這張卡上最貴的一種錯。**

    平倉單原本印的是 ④ 本金 231.34 / ⑤ 止損 160.65 / 槓桿 3 ——
    那是**開倉**的欄位。照著按會開一個新倉,而不是把舊的平掉:
    一張叫你平倉的單,把人帶去開倉。
    """
    t = a_close_ticket()
    labels = " ".join(lab for lab, _, _ in t.fields())
    assert "本金" not in labels
    assert "止損" not in labels
    assert "槓桿" not in labels
    assert "保證金模式" not in labels
    assert "數量" in labels, "平倉要知道平多少"


def test_a_close_ticket_says_to_go_to_the_positions_list():
    """平倉在 App 上是從持倉那一列進去的 —— 回開單畫面按會開新倉。"""
    notes = " ".join(n for _, _, n in a_close_ticket().fields())
    assert "持倉" in notes and "開新倉" in notes


def test_after_closing_the_position_should_be_zero_not_the_size():
    """原本印的是要平掉的數量(5.4)。而 5.4 在平倉**之後**出現在
    畫面上,代表的是沒平乾淨 —— 剛好是相反的意思。"""
    rows = a_close_ticket().verify_after()
    assert rows[0][0] == "成交後持倉"
    assert rows[0][1] == "0"
    assert "沒平乾淨" in rows[0][2]


def test_a_close_ticket_does_not_list_an_exit_line_to_watch():
    """平倉單本身就是出場。再列一次「打算在哪裡結束」會讓人以為
    平完之後還有一條線要顧。"""
    rows = a_close_ticket().exit_plan()
    assert rows[0][0] == "這張就是出場單"


def test_the_exit_line_is_not_called_a_price_and_says_it_is_not_a_target():
    """執政官:「做多出場價怎麼會比開倉價低?」

    **值是對的,是標籤在騙人。** 這套策略做多的理由就是「價格在均線
    之上」,所以那條均線**必然在進場價下面** —— 它是趨勢結束的位置,
    不是獲利目標。叫它「出場價」又擺在止損旁邊,讀起來就是停利單,
    而一個比進場價低的停利單當然看起來像壞掉了。
    """
    rows = a_ticket().exit_plan()
    assert rows[0][0] == "出場線(會移動)"
    assert "這不是停利" in rows[0][2]
    assert "本來就在進場價下面" in rows[0][2]


def test_the_evidence_line_names_the_strategys_own_line():
    """原本寫死「50 日均線」—— 那是**第三次**同一個錯(出場價、
    止損說明,現在是證據)。策略換成 100 日均線,這行會繼續說 50,
    而旁邊的數字是另一條線的值:一句看起來有憑有據的假話。"""
    t = build(symbol="X", action=OPEN_LONG, quantity=1.0, price=100.0,
              leverage=3.0, stop_pct=25.0, strategy="s",
              signal_day="2026-09-18", exit_price=90.0,
              exit_rule="跌破 100 日均線", signal=(100.0, 90.0, 11.1),
              now=NOW)
    ev = dict(t.why())["證據"]
    assert "100 日均線" in ev
    assert "50 日均線" not in ev


def test_no_ticket_card_prints_literal_markup():
    """說明文字裡有 **粗體** 標記,而卡片原本對它們做 html.escape ——
    所以螢幕上會出現字面的星號。

    整頁掃描那條測試抓不到這個:離線環境沒有訊號就沒有指令單,
    那張卡一行都不會被執行到。所以這裡直接渲染三種單各一張。
    """
    import re
    from portfolio.ticket import CLOSE, OPEN_SHORT
    for act in (OPEN_LONG, OPEN_SHORT, CLOSE):
        t = build(symbol="AAVEUSDT", action=act, quantity=5.4,
                  price=128.52, leverage=3.0, stop_pct=25.0,
                  strategy="50MA", signal_day="2026-09-18",
                  exit_price=112.40, exit_rule="跌破 50 日均線", now=NOW)
        html_ = dash._ticket_card(t, now=NOW)
        found = re.findall(r"[^<>]{0,40}\*\*[^<>]{0,40}", html_)
        assert not found, f"{act} 卡上有字面星號:{found}"
        assert "&lt;b&gt;" not in html_, f"{act} 卡上有字面 HTML 標籤"


def test_a_shorts_stop_is_described_as_above_the_price():
    """做空的止損在**上面**。寫死「下方」是假話 —— 而且是那種
    讀起來很順、但會讓人把單子填反的假話。"""
    from portfolio.ticket import OPEN_SHORT
    t = build(symbol="X", action=OPEN_SHORT, quantity=1.0, price=100.0,
              leverage=3.0, stop_pct=25.0, strategy="s",
              signal_day="2026-09-18", exit_price=130.0,
              exit_rule="站上 50 日均線", now=NOW)
    text = " ".join(str(x) for r in t.exit_plan() for x in r)
    assert "上方" in text
