"""
四種交易模式(Master Prompt 第九十一節)。

第九十一節要求 UI「必須非常清楚」現在是 MANUAL / PAPER / TEST / LIVE
哪一種,而 LIVE 要用多重確認避免誤觸。

## 模式是**推導出來的**,不是第四個設定

系統原本有三個旗標:

    TRADING_MODE          paper / live
    AUTO_TRADING          自動開倉開不開
    EXCHANGE_USE_TESTNET  連 BingX 的測試環境(VST)

再加一個 `MODE=` 設定會變成第四個真相來源,而四個之中只要有一個
跟其他三個不一致,畫面上顯示的模式就會是錯的 —— 而那正是第九十一節
想避免的事情。所以這裡**只推導,不儲存**。

## 優先順序:TEST 蓋過 LIVE

`TRADING_MODE=live` 加上 `EXCHANGE_USE_TESTNET=true` 是 TEST,不是 LIVE。

理由很簡單:**沒有真錢在動。** 反過來判定的話,一個在測試網上跑的
系統會顯示紅色的 LIVE 警告,而使用者會學會忽略那個警告 ——
然後在真的 LIVE 的那一天,它看起來一模一樣。
"""
from agmcis.config import settings

MANUAL = "MANUAL"
PAPER = "PAPER"
TEST = "TEST"
LIVE = "LIVE"

ORDER = (MANUAL, PAPER, TEST, LIVE)

# 每一種模式,一句話說清楚「什麼會自動發生」。
# 這是使用者真正需要知道的事 —— 模式的名字本身沒有資訊。
DESCRIPTIONS = {
    MANUAL: "模擬倉,而且**不會自動開倉**。每一筆都要人決定。",
    PAPER: "模擬倉,自動開倉開著。用真實行情,但不送真實訂單。",
    TEST: "連 BingX 測試環境(VST)。訂單是真的送出去的,但錢不是真的。",
    LIVE: "**真錢。** 每一筆都是實單。",
}

# 真錢會動的只有一種。
REAL_MONEY = frozenset({LIVE})


def current(settings_module=None):
    """
    現在是哪一種模式。純推導 —— 見模組說明。
    """
    s = settings_module or settings

    # TEST 蓋過 LIVE:沒有真錢在動的時候不該顯示 LIVE。
    if bool(getattr(s, "EXCHANGE_USE_TESTNET", False)):
        return TEST

    if str(getattr(s, "TRADING_MODE", "paper")).lower() == "live":
        return LIVE

    if not bool(getattr(s, "AUTO_TRADING_ENABLED", False)):
        return MANUAL

    return PAPER


def is_real_money(settings_module=None):
    return current(settings_module) in REAL_MONEY


def snapshot(settings_module=None):
    """
    給 UI 用的完整狀態。四種模式全部列出來並標明現在是哪一個 ——
    只顯示現在這一個的話,使用者不知道還有哪些選項,
    也看不出「我以為在 PAPER 但其實在 MANUAL」。
    """
    s = settings_module or settings
    mode = current(s)

    return {
        "mode": mode,
        "description": DESCRIPTIONS[mode],
        "is_real_money": mode in REAL_MONEY,
        # 推導這個模式的三個旗標。顯示出來,因為「為什麼是這個模式」
        # 比「是哪個模式」更常是使用者真正的問題。
        "derived_from": {
            "TRADING_MODE": str(getattr(s, "TRADING_MODE", "paper")).lower(),
            "AUTO_TRADING": bool(getattr(s, "AUTO_TRADING_ENABLED", False)),
            "EXCHANGE_USE_TESTNET": bool(getattr(s, "EXCHANGE_USE_TESTNET", False)),
        },
        "all_modes": [
            {
                "mode": name,
                "description": DESCRIPTIONS[name],
                "current": name == mode,
                "is_real_money": name in REAL_MONEY,
            }
            for name in ORDER
        ],
    }
