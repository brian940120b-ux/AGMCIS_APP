"""
版本戳 —— 「我看到的是新的嗎」要一眼答得出來 · 2026-09-13

═══ 為什麼需要這一條 ═══
執政官 pull 完看面板,說「完全沒變化」。而面板每次請求都重新 render、
`Cache-Control: no-store`,沒有任何快取 —— 所以幾乎一定是服務還在跑
舊的程式碼。

**但「幾乎一定」不是「一定」。** 那張截圖分不出四種情況:
沒 pull 到 / pull 了沒重啟 / 重啟失敗 / 真的換了但我改的有 bug。

分不出來的時候,兩邊都會開始用猜的。那是一個來回好幾輪的死結。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import build


def test_it_finds_the_commit_of_this_repo():
    """讀得到就要讀到。這個倉庫是 git worktree —— refs 在共用的
    commondir,只找 gitdir 會什麼都找不到(第一版真的踩到)。"""
    got = build.current(__file__)
    assert got.commit, "讀不到 commit —— worktree 的 commondir 沒處理?"
    assert len(got.commit) == 12
    assert all(c in "0123456789abcdef" for c in got.commit)


def test_an_unreadable_repo_gives_none_not_a_made_up_version(tmp_path,
                                                             monkeypatch):
    """讀不到就回 None。**不要編一個版本號出來。**

    一個假的版本號比沒有版本號糟得多:它會讓人相信自己在跑新版。

    2026-09-18 改:`current().commit` 現在是 import 時凍住的快照
    (見 RUNNING_COMMIT),所以這條改成直接驗讀取函式本身,
    外加 `on_disk` —— 那一格才是每次呼叫重讀的。
    """
    assert build._head_commit(tmp_path) is None
    monkeypatch.setattr(build, "BASE", tmp_path)
    assert build.current(None).on_disk is None


def test_the_description_says_so_when_the_version_is_unknown():
    """讀不到就要明講,不留空白 —— 空白會被讀成「沒問題」。"""
    unknown = build.Build(commit=None, source_mtime=None,
                          started_at=time.time())
    assert "版本不明" in unknown.describe()


def test_uptime_is_what_distinguishes_a_restart_that_did_not_take():
    """**版本號對了但啟動時間很舊,就是重啟沒生效。**

    只印 commit 是不夠的:pull 完檔案是新的,而行程還是舊的。
    """
    old = build.Build(commit="abc123def456", source_mtime=None,
                      started_at=time.time() - 3 * 3600)
    assert "小時前啟動" in old.describe()

    fresh = build.Build(commit="abc123def456", source_mtime=None,
                        started_at=time.time() - 5)
    assert "秒前啟動" in fresh.describe()


def test_the_dashboard_puts_the_stamp_where_it_is_seen_first():
    """版本戳要在**標頭**。

    「我看到的是新的嗎」這個問題發生在看畫面的第一秒,
    不是捲到頁尾的時候。
    """
    import scripts.dashboard as dash

    head = dash.render().split("</header>")[0]
    stamp = dash._build().describe()
    assert stamp in head, "版本戳不在標頭裡"


@pytest.mark.parametrize("must", [
    "指令單", "U 本位標準合約 · 手動送單", "還沒關掉的洞",
    "交易所帳戶 —— U 本位標準合約",
    "幣種 —— 能不能做,以及現在該不該做",
    "研究提案 —— 系統想改什麼",
    # 拿掉整張卡,但這兩件事必須還在 —— 它們一個決定下單量,
    # 一個決定什麼時候可以用真錢。藏起來比佔一張卡糟得多。
    "數量依據",
    "實盤資格",
])
def test_the_new_system_actually_reaches_the_page(must):
    """這幾塊是 2026-09-13 換上去的。**渲染不出來就等於沒改。**"""
    import scripts.dashboard as dash

    assert must in dash.render()


@pytest.mark.parametrize("gone", [
    "權益曲線", "相關性集中度</h2>", "事件日曆", "策略監控", "今日訂單",
    # 2026-09-18:「決策變數」那張卡併進「幣種」了 —— 手機上原本有
    # **兩張幣種表**,一張說「能不能做」、一張說「該不該做」,
    # 同一批幣、兩個地方看,而它們排序還不一樣。
    # 比的是 <h2> 不是那四個字:那句話本身還在(它是現在的決策規則),
    # 消失的只有那張卡。
    "<h2>決策變數</h2>",
    # 2026-09-18 執政官:「我現在只要標準合約的,其他的一律我不想看到。」
    # 紙上帳本那張卡算的是**永續**的成本(資金費、費率),
    # 而它的權益縮成指令單裡的一行 —— 因為那個數字決定下單量,
    # 藏起來就等於看不到「為什麼是這個量」。
    "<h2>部位大小的依據",
    "<h2>實盤資格契約</h2>",
])
def test_the_things_the_consul_asked_to_remove_are_gone(gone):
    """2026-09-13 執政官:「我想專注在 U 本位標準合約,其他不要。」

    ⚠️ 砍的是**顯示**,不是風控 —— 相關性與事件日曆仍然是部位大小
    的閘門,只是不再各佔一張卡。相關性搬進了指令單裡
    (`gate_correlation`),而 `tests/test_correlation.py` 照原樣
    釘著它的每一個數字:量到卻沒人看得到的數字等於沒量。
    """
    import scripts.dashboard as dash

    assert gone not in dash.render()


def test_the_correlation_number_survived_the_cut():
    """砍面板不能把 §60 的數字一起砍掉 —— 它只是換了位置。"""
    import scripts.dashboard as dash

    assert hasattr(dash, "gate_correlation")


def test_the_page_has_no_card_that_is_not_the_current_system():
    """**面板上每一張卡都要是現在這套系統的。**

    2026-09-18 執政官:「確認現在面板所有資訊只有新的我們現在做的,
    之前舊的一律不要看到。」

    這條把那次清點釘住:多一張卡就要在這裡登記,而登記的時候
    會被迫問一次「它屬於現在這套嗎」。**沒有這條,舊卡會慢慢長回來** ——
    每一張都有它當時的理由,而沒有人會回頭全部看一遍。
    """
    import re

    import scripts.dashboard as dash

    expected = {
        "指令單 —— 要你自己按的",
        "交易所帳戶 —— U 本位標準合約",
        "幣種 —— 能不能做,以及現在該不該做",
        "還沒關掉的洞",
        "研究提案 —— 系統想改什麼",
        "系統",
    }
    found = {re.sub(r"<[^>]+>", "", h).strip()
             for h in re.findall(r"<h2>(.*?)</h2>", dash.render(), re.S)}
    assert found == expected, (
        f"多出來的:{sorted(found - expected)}\n"
        f"不見了的:{sorted(expected - found)}")


# ══════════════════════════════════════════════════════════
# pull 了但沒重啟
# ══════════════════════════════════════════════════════════
def test_the_stamp_reports_the_commit_the_process_started_from():
    """**不是工作目錄現在的 commit。**

    第一版每次呼叫都重讀 .git,所以 `git pull` 之後不重啟,版本戳會
    顯示**新的** commit —— 而跑的還是舊的程式碼。
    一個會說謊的版本戳比沒有版本戳危險:它讓「我明明更新了」
    變成一句**有證據的錯話**。
    """
    assert build.RUNNING_COMMIT == build.current(__file__).commit


def test_pull_without_restart_is_called_out_by_name():
    """這個問題 2026-09-18 之前已經被問了四輪。"""
    stale = build.Build(commit="aaaaaaaaaaaa", source_mtime=None,
                        started_at=time.time() - 85 * 3600,
                        on_disk="bbbbbbbbbbbb")
    assert stale.stale is True
    text = stale.describe()
    assert "pull 了但沒重啟" in text
    assert "bbbbbbbbbbbb" in text, "要說出磁碟上是哪一版"


def test_a_freshly_restarted_process_is_not_flagged_stale():
    same = build.Build(commit="aaaaaaaaaaaa", source_mtime=None,
                       started_at=time.time(), on_disk="aaaaaaaaaaaa")
    assert same.stale is False
    assert "沒重啟" not in same.describe()


def test_an_unknown_commit_is_never_called_stale():
    """讀不到 commit 的時候不准亂喊 —— 那會變成一個永遠在響的警報,
    而永遠在響的警報等於沒有警報。"""
    assert build.Build(commit=None, source_mtime=None,
                       started_at=time.time(), on_disk="x").stale is False
    assert build.Build(commit="x", source_mtime=None,
                       started_at=time.time(), on_disk=None).stale is False
