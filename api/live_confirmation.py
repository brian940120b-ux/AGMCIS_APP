"""
LIVE 確認的網頁流程(Master Prompt 第九十一 / 九十二節)。

第九十二節要求 PAPER → LIVE 之前逐項確認七件事,最後逐字輸入
「I UNDERSTAND LIVE TRADING RISK」。第九十一節要求那個切換
「使用多重確認,避免誤觸」。

七個確認畫面加上逐字輸入一句話,不是誤觸。

## 這個端點做什麼、不做什麼

**做:** 產生與 `scripts/live_confirm.py` **完全相同**的確認檔。
走同一個 `agmcis/safety/live_confirm.py`,所以四道保護一個都沒少:
七項確認齊全、設定指紋相符、24 小時有效期、指名批准金額且不得
超過首次上限。

**不做:** 它不會讓系統開始下實單。寫出確認檔只是解開
LIVE SAFETY GATE 的**其中一項**檢查 —— 模擬盤筆數、天數、上線前
檢查、提款權限那幾項不是簽名就能通過的。

而且它**簽不了實單路徑那一項**。第七十八節要的是「有人讀過那段
會送真實訂單的程式碼」,一個網頁按鈕表達不了那件事:它只證明有人
點過一個按鈕。所以實單程式碼存在時,這裡會直接拒絕並請人改用
`python scripts/live_confirm.py` —— 那支腳本會把每個檔案的路徑與
SHA-256 印在終端機上逐檔問過去。

所以這個按鈕的最壞情況是「磁碟上多了一個確認檔」,不是「送出了
一張單」。這是它可以做成網頁的原因。

## POST 為什麼不在 api/transparency.py

那個模組有一條測試強制它全部是 GET。這裡會寫檔,是一個動作
不是查詢 —— 所以分開放,而不是去放寬那條測試。
"""
import logging

from fastapi import APIRouter, Body

from agmcis.config import modes
from agmcis.safety import live_confirm
from agmcis.safety import live_gate as gate_module

router = APIRouter()
logger = logging.getLogger("agmcis.api.live_confirmation")


@router.get("/api/trading_mode")
def api_trading_mode():
    """
    現在是 MANUAL / PAPER / TEST / LIVE 哪一種(第九十一節)。
    四種全部列出來 —— 只顯示現在這一個的話,使用者看不出
    「我以為在 PAPER 但其實在 MANUAL」。
    """
    return modes.snapshot()


@router.get("/api/live_confirmation")
def api_live_confirmation():
    """
    七項要確認的事,每一項帶著**當下的實際值**(第九十二節)。

    值是現算的,不是快取的 —— 使用者要確認的是他按下按鈕那一刻的
    設定,而確認檔存的指紋也是那一刻的。兩者必須來自同一次讀取。
    """
    items = live_confirm.build_items()

    return {
        "items": items,
        "fingerprint": live_confirm.fingerprint(items),
        "required_phrase": gate_module.REQUIRED_PHRASE,
        "valid_hours": gate_module.CONFIRMATION_VALID_HOURS,
        "max_notional_usdt": gate_module.MAX_INITIAL_NOTIONAL_USDT,
        "mode": modes.current(),
        # 已經簽過的話,顯示它還剩多久。重簽一次會覆蓋掉。
        "existing": _existing(),
    }


def _existing():
    """
    現有的確認檔狀態。讀不到就回 None ——
    **不回「有效」**,那個方向是錯的。
    """
    import json
    from pathlib import Path

    path = Path(gate_module.CONFIRMATION_FILE)
    if not path.exists():
        return None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("確認檔讀不出來 | %s", exc)
        return {"readable": False, "error": str(exc)}

    ok, detail = live_confirm.verify(data)

    return {
        "readable": True,
        "signed_at": data.get("signed_at"),
        "approved_notional_usdt": data.get("approved_notional_usdt"),
        "items_valid": ok,
        "detail": detail,
    }


@router.post("/api/live_confirmation")
def api_submit_live_confirmation(payload: dict = Body(...)):
    """
    送出七項確認。回傳 `{"ok": bool, "message": str}`。

    **任何一項不對就整份拒絕,而且不寫檔。**
    一份寫到一半的確認檔比沒有確認檔危險。
    """
    ok, message = submit(payload)

    if ok:
        logger.warning("LIVE 確認檔已由網頁產生 | %s", message.splitlines()[0])
    else:
        logger.info("LIVE 確認被拒 | %s", message)

    return {"ok": ok, "message": message}


def _live_path_blocked():
    """
    有沒有需要人逐檔讀過的實單程式碼。回傳 (可以繼續嗎, 說明)。

    掃不動也回 False —— 「掃不動」不等於「沒有」。
    """
    from agmcis.safety.live_gate import LiveGate

    try:
        found = LiveGate()._scan_live_broker_sources()
    except Exception as exc:
        return False, (
            f"掃不到實單路徑({type(exc).__name__}: {exc})。"
            f"掃不動不等於沒有,所以這裡停下來。"
        )

    if not found:
        return True, ""

    files = "、".join(sorted({where for where, _n, _w in found}))
    return False, (
        f"這個系統裡有會送出真實訂單的程式碼({files})。\n"
        f"第七十八節要求那段程式碼由人逐檔讀過並簽下原始碼雜湊,"
        f"而網頁按鈕證明不了「讀過」——它只證明有人點過按鈕。\n"
        f"請改用 python scripts/live_confirm.py,它會逐檔把路徑與 "
        f"SHA-256 印出來。"
    )


def submit(payload, path=None, settings_module=None):
    """
    驗證並寫檔。純函式(除了寫檔),所以測試不用發 HTTP。

    回傳 (成功?, 訊息)。
    """
    import json
    from datetime import datetime, timezone
    from pathlib import Path

    items = live_confirm.build_items(settings_module)

    # ---------- 七項確認 ----------
    confirmations = payload.get("confirmations")
    if not isinstance(confirmations, dict):
        return False, "缺少 confirmations —— 七項都要逐項確認(第九十二節)。"

    missing = [
        name for name in live_confirm.REQUIRED_ITEMS
        if confirmations.get(name) is not True
    ]
    if missing:
        titles = [live_confirm.ITEM_TITLES[n].split(" —— ")[0] for n in missing]
        return False, "以下項目沒有確認:" + "、".join(titles)

    # ---------- 設定指紋 ----------
    #
    # 前端拿到七項的時候一起拿到指紋。送回來的指紋與現在算的不一樣,
    # 代表**使用者在看那個畫面的期間設定被改過** —— 他確認的是舊的
    # 那一組值。這一條擋的是一個很窄但很真實的競態。
    seen = payload.get("fingerprint")
    current = live_confirm.fingerprint(items)

    if seen and seen != current:
        return False, (
            "設定在你確認的過程中被改過了。請重新整理,"
            "確認新的值之後再送一次。"
        )

    # ---------- 金額 ----------
    raw = payload.get("approved_notional_usdt")
    try:
        notional = float(raw)
    except (TypeError, ValueError):
        return False, f"批准金額不是數字:{raw!r}"

    if notional <= 0:
        return False, "批准金額必須大於 0。"

    if notional > gate_module.MAX_INITIAL_NOTIONAL_USDT:
        return False, (
            f"批准金額 {notional:g} 超過首次實單上限 "
            f"{gate_module.MAX_INITIAL_NOTIONAL_USDT:g} USDT。"
            f"第一次用真錢跑的規模應該小到虧光也不影響任何事。"
        )

    # ---------- 確認句 ----------
    #
    # 逐字比對,**不 strip 大小寫也不容錯**。這一句是最後一道
    # 「你確定嗎」,而一個接受近似輸入的確認句等於沒有確認句。
    phrase = payload.get("phrase")
    if phrase != gate_module.REQUIRED_PHRASE:
        return False, "確認句不符。必須逐字輸入,大小寫要一樣。"

    # ---------- 實單路徑 ----------
    #
    # 網頁簽不了這一項,而且**不該讓它簽得了**。
    #
    # 第七十八節要的是「有人讀過那段會送真實訂單的程式碼」。
    # 一個網頁按鈕表達不了那件事:它只證明有人點過一個按鈕。
    # CLI 會把每個檔案的路徑與 SHA-256 印在終端機上,逐檔問過去 ——
    # 那個過程本身就是「讀過」的證據。
    #
    # 所以實單程式碼存在時,網頁精靈在這裡停下來,不寫檔。
    ok, why = _live_path_blocked()
    if not ok:
        return False, why

    # ---------- 寫檔 ----------
    document = {
        "phrase": gate_module.REQUIRED_PHRASE,
        "signed_at": datetime.now(timezone.utc).isoformat(),
        "approved_notional_usdt": notional,
        "confirmations": {entry["item"]: True for entry in items},
        "settings_fingerprint": current,
        "confirmed_values": {e["item"]: e["values"] for e in items},
        # 從哪裡簽的。事後檢討時「是誰在什麼情境下批准的」很重要,
        # 而網頁與 CLI 的情境不一樣。
        "signed_via": "web",
    }

    target = Path(path or gate_module.CONFIRMATION_FILE)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8",
    )

    _audit(notional)

    return True, (
        f"已簽署,批准單筆名目上限 {notional:g} USDT,"
        f"{gate_module.CONFIRMATION_VALID_HOURS} 小時後失效。\n"
        f"這**不代表系統會開始下實單** —— LIVE SAFETY GATE 還有其他檢查。"
        f"跑 scripts/live_gate.py 看整體結果。"
    )


def _audit(notional):
    """
    第六十五節:重要操作要稽核。批准實單是最重要的那一種。
    寫不進去不該讓確認失敗(檔案已經寫了),但一定要喊。
    """
    try:
        from agmcis.review.decision_log import audit
        audit(
            "LIVE_CONFIRMATION_SIGNED", actor="web", target="LIVE_GATE",
            before=None, after=f"{notional:g} USDT",
            detail="七項確認 + 確認句,由網頁介面簽署",
        )
    except Exception:
        logger.exception("LIVE 確認的稽核寫入失敗")
