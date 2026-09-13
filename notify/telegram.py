"""
AGMCIS Phase 11 — Telegram 通知
對應架構文件:模組 8「Telegram 即時通知與每日/每週摘要」

設定(加進 backend/.env):
  TELEGRAM_BOT_TOKEN=123456:ABC-DEF...   ← 跟 @BotFather 建 bot 拿到的
  TELEGRAM_CHAT_ID=123456789             ← 你和 bot 對話的 chat id

沒設定 = 安靜降級(只寫日誌不報錯),系統照常運作。
"""
from __future__ import annotations

import html as _html
import os

import requests

from core.logging import get_logger

log = get_logger("notify.telegram")

MAX_LEN = 3900   # Telegram 上限 4096,留餘裕


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
        return False
    try:
        s = session or requests
        payload = {"chat_id": chat_id, "text": text[:MAX_LEN]}
        if html:
            payload["parse_mode"] = "HTML"
        resp = s.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json=payload, timeout=10)
        if resp.status_code == 200:
            return True
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
            return resp2.status_code == 200
        return False
    except Exception as e:
        log.warning(f"Telegram 送出失敗:{type(e).__name__}: {e}")
        return False
