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
    """
    monkeypatch.setattr(build, "BASE", tmp_path)
    assert build.current(None).commit is None


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
])
def test_the_new_system_actually_reaches_the_page(must):
    """這幾塊是 2026-09-13 換上去的。**渲染不出來就等於沒改。**"""
    import scripts.dashboard as dash

    assert must in dash.render()
