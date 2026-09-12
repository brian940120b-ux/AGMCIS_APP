"""
LIVE 確認精靈的資料層(Master Prompt 第九十二節)。

第九十二節要求從 PAPER 切到 LIVE 之前逐項確認七件事:

    Confirm Account / Exchange / Market / Risk / Leverage /
    Daily Loss / API

最後逐字輸入「I UNDERSTAND LIVE TRADING RISK」。

原本的閘門只檢查那句話、簽署時間、與批准金額。少了七項確認,
使用者可以在**完全不知道自己批准了什麼設定**的情況下簽名。

## 核心設計:確認綁定的是「當時的那組設定」

每一項確認都連著一個**當下的實際值**(例如「單筆風險 1.0%」),
而簽好的確認檔把那些值一起存下來。閘門驗證的時候會重新算一次
現在的值,對不上就作廢。

為什麼:

    12:00  使用者確認「日虧損上限 20 USDT」並簽名
    13:00  有人把 MAX_DAILY_LOSS_USDT 改成 500
    14:00  簽名還在有效期內 —— 舊的閘門會放行

那個放行是錯的。使用者批准的是 20 USDT 的那套設定,不是 500 的。
「批准過一次」不等於「批准所有設定」,跟「批准過一次」不等於
「批准所有金額」是同一條原則。

## 指紋不含機密

API 那一項確認的是「有沒有金鑰、提款權限關了沒」,
存下來的是**布林值與金鑰的後四碼**,不是金鑰本身
(第十、八十四節:金鑰不得出現在任何檔案、log 或紀錄裡)。
"""
import hashlib
import json
import logging

logger = logging.getLogger("agmcis.safety.live_confirm")

# 七項確認的代號。順序就是第九十二節列的順序。
ITEM_ACCOUNT = "account"
ITEM_EXCHANGE = "exchange"
ITEM_MARKET = "market"
ITEM_RISK = "risk"
ITEM_LEVERAGE = "leverage"
ITEM_DAILY_LOSS = "daily_loss"
ITEM_API = "api"

REQUIRED_ITEMS = (
    ITEM_ACCOUNT, ITEM_EXCHANGE, ITEM_MARKET, ITEM_RISK,
    ITEM_LEVERAGE, ITEM_DAILY_LOSS, ITEM_API,
)

ITEM_TITLES = {
    ITEM_ACCOUNT: "Confirm Account —— 這是哪一個帳戶",
    ITEM_EXCHANGE: "Confirm Exchange —— 送到哪一家交易所",
    ITEM_MARKET: "Confirm Market —— 什麼市場、什麼合約",
    ITEM_RISK: "Confirm Risk —— 每一筆最多虧多少",
    ITEM_LEVERAGE: "Confirm Leverage —— 槓桿上限",
    ITEM_DAILY_LOSS: "Confirm Daily Loss —— 一天最多虧多少",
    ITEM_API: "Confirm API —— 金鑰與權限",
}


def _key_tail(value):
    """
    金鑰的後四碼。用來確認「是我以為的那把金鑰」,
    而後四碼不足以還原金鑰本身。

    金鑰不存在時回 None —— 不回空字串,那會讓「沒有金鑰」與
    「金鑰是空的」看起來一樣。
    """
    text = str(value or "")
    return text[-4:] if len(text) >= 4 else None


def build_items(settings_module=None):
    """
    現在要確認的七件事,每一項帶著**當下的實際值**。

    純讀取,不碰網路也不碰資料庫 —— 精靈與閘門兩邊都會呼叫它,
    而兩邊算出來的必須是同一組值。
    """
    if settings_module is None:
        from agmcis.config import settings as settings_module

    s = settings_module
    limits = s.risk_limits_dict()

    return [
        {
            "item": ITEM_ACCOUNT,
            "title": ITEM_TITLES[ITEM_ACCOUNT],
            "values": {
                "app_env": s.APP_ENV,
                "trading_mode": s.TRADING_MODE,
                "testnet": bool(s.EXCHANGE_USE_TESTNET),
                "api_key_tail": _key_tail(s.EXCHANGE_CREDENTIALS.get("apiKey")),
            },
        },
        {
            "item": ITEM_EXCHANGE,
            "title": ITEM_TITLES[ITEM_EXCHANGE],
            "values": {
                "exchange": s.EXCHANGE,
                "testnet": bool(s.EXCHANGE_USE_TESTNET),
            },
        },
        {
            "item": ITEM_MARKET,
            "title": ITEM_TITLES[ITEM_MARKET],
            "values": {
                "market_type": s.MARKET_TYPE,
                "watchlist": list(s.WATCHLIST_SYMBOLS),
            },
        },
        {
            "item": ITEM_RISK,
            "title": ITEM_TITLES[ITEM_RISK],
            "values": {
                "max_risk_per_trade_pct": limits["max_risk_per_trade_pct"],
                "max_exposure_pct": limits["max_exposure_pct"],
                "max_open_positions": limits["max_open_positions"],
                "max_drawdown_pct": limits["max_drawdown_pct"],
            },
        },
        {
            "item": ITEM_LEVERAGE,
            "title": ITEM_TITLES[ITEM_LEVERAGE],
            "values": {
                "max_leverage": limits["max_leverage"],
                "max_live_leverage": limits["max_live_leverage"],
                "safe_live_mode": limits["safe_live_mode"],
            },
        },
        {
            "item": ITEM_DAILY_LOSS,
            "title": ITEM_TITLES[ITEM_DAILY_LOSS],
            "values": {
                "max_daily_loss_usdt": limits["max_daily_loss_usdt"],
                "max_weekly_loss_usdt": limits["max_weekly_loss_usdt"],
                "max_live_daily_loss_usdt": limits["max_live_daily_loss_usdt"],
                "max_live_trades_per_day": limits["max_live_trades_per_day"],
            },
        },
        {
            "item": ITEM_API,
            "title": ITEM_TITLES[ITEM_API],
            "values": {
                # 金鑰本身絕不進來(第十、八十四節)。
                "has_credentials": bool(s.has_exchange_credentials()),
                "api_key_tail": _key_tail(s.EXCHANGE_CREDENTIALS.get("apiKey")),
                "allow_public_data_only": bool(s.ALLOW_PUBLIC_DATA_ONLY),
            },
        },
    ]


def fingerprint(items=None, settings_module=None):
    """
    七項確認值的指紋。

    用排序過的 JSON 算 SHA-256:任何一個值變了,指紋就變。
    比逐欄位比對簡單,而且新增欄位時不會忘記把它納入檢查。
    """
    if items is None:
        items = build_items(settings_module)

    payload = {entry["item"]: entry["values"] for entry in items}
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def verify(data, settings_module=None):
    """
    驗證一份簽好的確認檔的七項確認。

    回傳 (ok, 說明)。**任何不確定都算沒通過** —— 一個在自己
    看不懂輸入時放行的檢查不是檢查。
    """
    confirmations = data.get("confirmations")

    if not isinstance(confirmations, dict):
        return False, (
            "確認檔缺少 confirmations。第九十二節要求逐項確認七件事,"
            "請用 scripts/live_confirm.py 重新產生。"
        )

    missing = [name for name in REQUIRED_ITEMS if confirmations.get(name) is not True]
    if missing:
        return False, (
            "以下項目沒有確認:"
            + "、".join(ITEM_TITLES[name].split(" —— ")[0] for name in missing)
        )

    signed_fingerprint = data.get("settings_fingerprint")
    if not signed_fingerprint:
        return False, (
            "確認檔缺少 settings_fingerprint。沒有它就無法確認"
            "簽署當下的設定與現在是否相同。"
        )

    current = fingerprint(settings_module=settings_module)
    if signed_fingerprint != current:
        return False, (
            "設定在簽署之後被改過。確認的是**當時的那組設定**,"
            "不是「以後所有的設定」——\n"
            "請比對 scripts/live_confirm.py 顯示的值,確認無誤後重新簽署。"
        )

    return True, "七項確認齊全,而且設定沒有在簽署之後被改過。"
