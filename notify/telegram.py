"""
AGMCIS Phase 11 — Telegram 通知
對應架構文件:模組 8「Telegram 即時通知與每日/每週摘要」

設定(加進 backend/.env):
  TELEGRAM_BOT_TOKEN=123456:ABC-DEF...   ← 跟 @BotFather 建 bot 拿到的
  TELEGRAM_CHAT_ID=123456789             ← 你和 bot 對話的 chat id

沒設定 = 安靜降級(只寫日誌不報錯),系統照常運作。

═══ 2026-09-25:每一次送出的結果都要落地 ═══
這一天查出來:PRIMARY 的記帳停了 133 小時,而**系統自己早就發現了**
—— 檢修官每 10 分鐘就報一次
「交易所一致性徹查未通過:SpecMissing: 沒有 BTC-USDT 的資金費率歷史」。

那個警報一次都沒有送到。旁邊同一份 log 寫著:

    WARNING notify.telegram: Telegram 回應 401

**警鈴一直在響,而電話線是斷的。**

一個只會寫進 log 的失敗等於沒有失敗:沒有人會去翻 journalctl 找
「我的通知有沒有送出去」。而且這件事有先天的循環 —— 通知管道壞掉
的時候,它不可能用自己來通知你。

所以 send() 每一次都把結果寫進 NOTIFY_STATE,由**面板**(另一個
獨立的管道)顯示。面板看得到「通知送不出去」,這個循環才斷得掉。
"""
from __future__ import annotations

import html as _html
import os
import time
from pathlib import Path

import requests

from core.atomic import write_json_atomic
from core.logging import get_logger

log = get_logger("notify.telegram")

MAX_LEN = 3900   # Telegram 上限 4096,留餘裕

#: 最後一次送出的結果。面板讀它來判斷「警報到底送不送得出去」。
NOTIFY_STATE = Path(__file__).resolve().parents[1] / "data" / "notify_state.json"


def _record(ok: bool, why: str) -> bool:
    """把送出結果落地。**這支自己絕不拋例外** —— 記錄失敗不能反過來
    弄壞通知,更不能弄壞呼叫它的主流程。"""
    try:
        NOTIFY_STATE.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(NOTIFY_STATE,
                          {"at": time.time(), "ok": bool(ok), "why": why})
    except Exception as e:                           # noqa: BLE001
        log.warning(f"通知狀態寫不進去:{type(e).__name__}: {e}")
    return ok


def last_delivery() -> dict:
    """最後一次送出的結果。面板用。讀不到就回 {} —— 不假裝成功。"""
    import json
    try:
        return json.loads(NOTIFY_STATE.read_text(encoding="utf-8"))
    except Exception:                                # noqa: BLE001
        return {}


def esc(s: object) -> str:
    """HTML 模式下,資料裡的動態文字(幣種、訊息內容)必須跳脫。

    格式標籤(<b>、<code>)是我們自己寫的,永遠信任;
    但插進去的數字、字串來自資料,理論上可能出現 <、>、& ——
    不跳脫的話一旦真的出現,Telegram 會整段訊息解析失敗,直接送不出去。
    """
    return _html.escape(str(s), quote=False)


def is_configured() -> bool:
    return bool(os.environ.get("TELEGRAM_BOT_TOKEN")
                and os.environ.get("TELEGRAM_CHAT_ID"))


def send(text: str, session=None, html: bool = False) -> bool:
    """送出一則訊息。成功 True;未設定或失敗 False(絕不拋例外中斷主流程)。

    html=True 時用 Telegram 的 HTML 解析模式(只認 <b> <i> <code> <pre> <a>
    等少數標籤)。選 HTML 不選 Markdown:MarkdownV2 要求跳脫 . - ! 等一大票
    符號,而我們的訊息滿是價格小數點與幣種代號的連字號,逐一跳脫容易漏,
    漏一個就整段送不出去。HTML 只需要跳脫 < > &,面更小、錯得少。

    HTML 解析失敗時自動退回純文字重送一次(剝掉標籤)—— 通知這件事本身
    比排版重要,格式化失敗不能讓訊息整個消失。
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        log.info("Telegram 未設定,略過通知")
        return _record(False, "未設定 TELEGRAM_BOT_TOKEN / CHAT_ID")
    try:
        s = session or requests
        payload = {"chat_id": chat_id, "text": text[:MAX_LEN]}
        if html:
            payload["parse_mode"] = "HTML"
        resp = s.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json=payload, timeout=10)
        if resp.status_code == 200:
            return _record(True, "已送出")
        log.warning(f"Telegram 回應 {resp.status_code}: "
                    f"{getattr(resp, 'text', '')[:200]}")
        if html:
            import re
            plain = re.sub(r"</?(?:b|i|code|pre|a)[^>]*>", "", text)
            log.info("HTML 解析失敗,退回純文字重送")
            resp2 = s.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": plain[:MAX_LEN]},
                timeout=10)
            return _record(resp2.status_code == 200,
                           f"HTTP {resp2.status_code}(純文字重送)")
        return _record(False, f"HTTP {resp.status_code}")
    except Exception as e:
        log.warning(f"Telegram 送出失敗:{type(e).__name__}: {e}")
        return _record(False, f"{type(e).__name__}: {e}"[:120])
