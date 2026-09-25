"""一鍵救援腳本 · 2026-09-25

執政官在手機上用 Termius。每多一個指令就多一次打字、多一次貼上、
多一次「上一段成功了沒」要自己判斷 —— 而這五步**有先後依賴**:
沒換到新版,死結修復就不在;沒解開死結,驗證就沒有東西可看。

這組測試守的是:**腳本裡叫到的東西真的存在**。

一支救援腳本在半夜跑到第三段才因為打錯一個屬性名而爆掉,
比沒有這支還糟 —— 因為前兩段已經改過東西了。bash -n 不會抓到
內嵌 python 的問題,所以這裡逐段 parse,並且逐個確認符號存在。
"""
from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SH = ROOT / "scripts/rescue.sh"
SRC = SH.read_text(encoding="utf-8")


def test_bash_syntax_is_valid():
    r = subprocess.run(["bash", "-n", str(SH)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_every_embedded_python_block_parses():
    """bash -n 看不見 heredoc 裡的 python。跑到那一段才爆就太遲了。"""
    blocks = re.findall(r"<<'PYEOF'\n(.*?)\nPYEOF", SRC, re.S)
    assert len(blocks) == 3, f"預期 3 段內嵌 python,實際 {len(blocks)}"
    for i, b in enumerate(blocks, 1):
        ast.parse(b)                                  # 語法錯就在這裡炸


def test_the_symbols_it_calls_actually_exist():
    """打錯一個屬性名,第三段才會發現 —— 而那時帳本已經被改過了。"""
    from notify import telegram
    from portfolio import paper
    from portfolio.account import Account

    for name in ("PRIMARY", "CONTROL", "MAX_UNIVERSE", "tick"):
        assert hasattr(paper, name), f"paper.{name} 不存在"
    for cfg in (paper.PRIMARY, paper.CONTROL):
        for f in ("state_path", "curve_path", "name"):
            assert hasattr(cfg, f), f"Config.{f} 不存在"
    for name in ("send", "is_configured", "last_delivery"):
        assert hasattr(telegram, name), f"telegram.{name} 不存在"
    for f in ("positions", "funding_through_ms"):
        assert f in Account.__dataclass_fields__ or hasattr(Account, f), f


def test_tick_really_returns_the_keys_the_script_prints():
    """第三段印的每一個 key,tick() 都要真的回得出來。"""
    src = (ROOT / "portfolio/paper.py").read_text(encoding="utf-8")
    ret = src[src.index('return {"liquidated"'):]
    for k in ("holdings", "symbols", "funding_gaps", "orders",
              "equity", "return_pct"):
        assert f'"{k}"' in ret, f"tick() 沒有回 {k}"


def test_it_stops_when_the_deploy_fails():
    """換版沒過就**全部不跑**。

    死結修復不在舊版裡,硬跑下去等於用舊的程式碼改帳本 ——
    那比不跑更糟,而且會把「還沒修好」偽裝成「修了沒用」。
    """
    assert "後面全部不跑" in SRC
    assert "exit 1" in SRC


def test_it_says_which_steps_mutate():
    """哪一段會改東西,要標出來。使用者有權在按下去之前知道。"""
    assert "會改帳本" in SRC
    assert "唯讀" in SRC


def test_it_never_asks_for_the_token_to_be_pasted():
    """§10 / §84:金鑰不得出現在對話、log、前端或任何輸出。"""
    assert "不要把 token 貼進對話" in SRC
    assert "TELEGRAM_BOT_TOKEN" not in SRC.split("getUpdates")[1], \
        "不要在輸出裡回顯 token"
