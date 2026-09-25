"""警報送不出去這件事,要在**面板上**看得見 · 2026-09-25

═══ 這條測試守的是這次停擺真正的原因 ═══
PRIMARY 的記帳停了 133 小時。而系統**早就發現了** —— 檢修官每 10
分鐘報一次「交易所一致性徹查未通過:SpecMissing: 沒有 BTC-USDT 的
資金費率歷史」。

那個警報一次都沒有送到。同一份 log 隔幾行寫著:

    WARNING notify.telegram: Telegram 回應 401

**警鈴一直在響,而電話線是斷的。**

這件事有先天的循環:**通知管道壞掉的時候,它不可能用自己來通知你。**
唯一的解法是讓另一條獨立的管道(面板)顯示它。

所以:
  · send() 每一次都把結果落地(不只寫 log —— 沒有人會去翻 journalctl
    找「我的通知有沒有送出去」)
  · 面板的狀態列讀它,送不出去就是紅燈
  · **沒有紀錄不等於正常** —— 有設定卻從沒送成功過,那是還沒證明它會通
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from notify import telegram  # noqa: E402


@pytest.fixture
def state(tmp_path, monkeypatch):
    p = tmp_path / "notify_state.json"
    monkeypatch.setattr(telegram, "NOTIFY_STATE", p)
    return p


class _Resp:
    def __init__(self, code, text=""):
        self.status_code = code
        self.text = text


class _Sess:
    def __init__(self, code):
        self.code = code

    def post(self, *a, **k):
        return _Resp(self.code, "unauthorized")


def test_a_401_is_recorded_not_just_logged(state, monkeypatch):
    """**這就是那次沒被發現的失敗。** 寫進 log 不算數。"""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    assert telegram.send("hi", session=_Sess(401)) is False
    got = json.loads(state.read_text(encoding="utf-8"))
    assert got["ok"] is False
    assert "401" in got["why"]


def test_a_success_is_recorded_too(state, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    assert telegram.send("hi", session=_Sess(200)) is True
    assert json.loads(state.read_text(encoding="utf-8"))["ok"] is True


def test_not_configured_is_recorded_as_not_delivered(state, monkeypatch):
    """沒設定也要記 —— 「沒送」跟「送成功」不是同一件事。"""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert telegram.send("hi") is False
    assert json.loads(state.read_text(encoding="utf-8"))["ok"] is False


def test_recording_never_breaks_the_caller(monkeypatch):
    """記錄失敗不可以反過來弄壞通知,更不可以弄壞主流程。"""
    monkeypatch.setattr(telegram, "NOTIFY_STATE",
                        Path("/proc/nonexistent/x.json"))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    assert telegram.send("hi", session=_Sess(200)) is True   # 不拋例外


def test_no_record_is_not_treated_as_healthy(state):
    """**沒有紀錄不等於正常。** 讀不到就回 {},由呼叫端判斷。"""
    assert telegram.last_delivery() == {}


def test_the_dashboard_status_strip_has_an_alert_channel_light():
    """面板是唯一不會被同一個故障弄啞的管道 —— 這一格必須在它上面。"""
    src = (ROOT / "scripts/dashboard.py").read_text(encoding="utf-8")
    body = src[src.index("def status_checks("):src.index("def block_status(")]
    assert "last_delivery" in body, "狀態列沒有檢查警報送不送得出去"
    assert '"通知"' in body
    assert "設定了但從沒送成功過" in body, "沒有紀錄被當成正常了"
