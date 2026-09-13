"""
第八節:BingX Adapter 的模組佈局。

第八節建議一組模組,並且說「如果現有專案已有類似模組:不要重複建立。
應該:REFACTOR EXISTING CODE」。

這一組測試盯兩件事:
  1. 每一項建議都找得到對應的地方(不是每一項都要是新檔案)。
  2. **拆檔沒有改變任何行為** —— 公開 API 與拆之前一模一樣。

第二點是重點。這次重構動到的是唯一一條會送出訂單的路徑,
而「拆檔順便改了一個預設值」是這種重構最典型的失敗方式。
"""
import ast
import inspect
import pathlib

import pytest

from agmcis.core.enums import MarketType
from agmcis.exchange.bingx import adapter as adapter_module
from agmcis.exchange.bingx.adapter import BingXAdapter

BINGX = pathlib.Path("agmcis/exchange/bingx")


# 第八節建議的每一項 -> 它在這個系統的位置。
# 值是 None 代表「已有類似模組,指過去而不重複建立」。
LAYOUT = {
    "client.py": BINGX / "client.py",
    "auth.py": BINGX / "auth.py",
    "signer.py": BINGX / "signer.py",
    "market.py": BINGX / "market.py",
    "contracts.py": BINGX / "contracts.py",
    "trading_rules.py": BINGX / "trading_rules.py",
    "account.py": BINGX / "account.py",
    "orders.py": BINGX / "orders.py",
    "positions.py": BINGX / "positions.py",
    "websocket.py": BINGX / "websocket.py",
    "executor.py": BINGX / "executor.py",
    "reconciler.py": BINGX / "reconciler.py",
    "rate_limiter.py": BINGX / "rate_limiter.py",
    "errors.py": BINGX / "errors.py",
}

# 純指路:整支只有 re-export,實作在別的地方。
POINTERS = {
    "websocket.py": "agmcis/exchange/bingx/stream.py",
    "executor.py": "agmcis/execution/engine.py",
    "reconciler.py": "agmcis/execution/reconciliation.py",
    "rate_limiter.py": "agmcis/exchange/rate_limiter.py",
    "errors.py": "agmcis/exchange/error_policy.py",
}

# 半指路:共用的部分 re-export,**但有一段是 bingx 專屬的**。
#
# contracts.py 就是這一種:規格的快取與校準在 specs.py(不分交易所),
# 但「ccxt 的 precision 欄位怎麼讀」只有 ccxt 的使用者需要,
# 放進共用模組會讓那個模組開始知道 ccxt 的事。
HYBRID = {
    "contracts.py": ("agmcis/exchange/specs.py", {"decimals"}),
}


# ---------------- 佈局 ----------------

def test_base_module_exists():
    assert pathlib.Path("agmcis/exchange/base.py").exists()


@pytest.mark.parametrize("name,path", sorted(LAYOUT.items()))
def test_every_recommended_module_exists(name, path):
    assert path.exists(), f"第八節建議的 {name} 找不到"


@pytest.mark.parametrize("name,target", sorted(POINTERS.items()))
def test_pointer_modules_do_not_reimplement(name, target):
    """
    「不要重複建立」是第八節的原話。指路的檔案裡不該有實作 ——
    有兩份實作的話,「到底哪一個在生效」會變成一個要追程式碼才
    答得出來的問題。
    """
    assert pathlib.Path(target).exists(), f"{name} 指向的 {target} 不存在"

    tree = ast.parse((BINGX / name).read_text(encoding="utf-8"))

    # 只允許 import、__all__ 指派、docstring。不允許 class / def。
    for node in tree.body:
        assert not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                     ast.ClassDef)), (
            f"{name} 有自己的實作({getattr(node, 'name', '?')}),"
            f"但實作應該在 {target}"
        )


@pytest.mark.parametrize("name,spec", sorted(HYBRID.items()))
def test_hybrid_modules_only_add_what_is_exchange_specific(name, spec):
    """
    半指路的檔案可以有實作,但只能是**這家交易所專屬**的那一段。
    多出來的東西代表共用邏輯被複製了一份。
    """
    target, allowed = spec

    assert pathlib.Path(target).exists()

    tree = ast.parse((BINGX / name).read_text(encoding="utf-8"))
    defined = {
        node.name for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }

    extra = defined - allowed
    assert extra == set(), (
        f"{name} 多了 {sorted(extra)} —— 共用的東西應該在 {target}"
    )


@pytest.mark.parametrize("name,target", sorted(POINTERS.items()))
def test_pointer_modules_say_where_the_real_thing_is(name, target):
    """指路的檔案要指得出路,否則它只是一個空殼。"""
    text = (BINGX / name).read_text(encoding="utf-8")
    module_path = target.replace("/", ".").removesuffix(".py")

    assert module_path in text.replace("/", "."), f"{name} 沒有指出 {target}"


# ---------------- 行為沒變 ----------------

# 拆檔之前 BingXAdapter 的公開方法。少一個都代表有呼叫端會壞掉。
PUBLIC_API = {
    "capabilities", "clock_status", "sync_server_time", "to_market_symbol",
    "load_markets", "list_markets", "get_trading_rules",
    "get_ticker", "get_ohlcv", "get_tickers", "get_order_book",
    "get_long_short_ratio", "get_liquidations",
    "get_funding_rate", "get_open_interest",
    "get_balance", "get_positions",
    "create_order", "cancel_order", "get_order",
    "get_leverage", "get_position_mode", "set_leverage", "set_margin_mode",
    "ping",
}


@pytest.mark.parametrize("name", sorted(PUBLIC_API))
def test_the_public_api_survived_the_split(name):
    assert hasattr(BingXAdapter, name), f"拆檔弄丟了 {name}"
    assert callable(getattr(BingXAdapter, name))


def test_the_adapter_is_still_one_object():
    """
    改成「adapter 持有一個 market 物件」的話,每個呼叫端都要改。
    mixin 讓檔案分開而物件不變。
    """
    a = BingXAdapter(exchange_factory=lambda mt: None)

    for name in PUBLIC_API:
        assert callable(getattr(a, name))


def test_the_old_private_names_still_resolve():
    """
    拆檔之前這幾個是模組層級的私有函式,有測試直接用它們。
    """
    for name in ("_build_ccxt_exchange", "_ccxt_type",
                 "_build_rate_limiter", "_as_float", "_CCXT_MARKET_TYPE"):
        assert hasattr(adapter_module, name), f"舊名稱 {name} 不見了"


def test_standard_futures_is_still_refused():
    a = BingXAdapter(exchange_factory=lambda mt: None)

    from agmcis.core.errors import ExchangeUnavailableError

    with pytest.raises(ExchangeUnavailableError):
        a.to_market_symbol("BTC/USDT", MarketType.STANDARD)


def test_the_symbol_format_did_not_change():
    a = BingXAdapter(exchange_factory=lambda mt: None)

    assert a.to_market_symbol("BTC/USDT") == "BTC/USDT:USDT"
    assert a.to_market_symbol("btc/usdt") == "BTC/USDT:USDT"
    # 已經是 ccxt 格式的不再加一次
    assert a.to_market_symbol("BTC/USDT:USDT") == "BTC/USDT:USDT"


def test_writes_are_still_marked_as_writes():
    """
    `is_write=True` 讓逾時走 ACTION_RECONCILE 而不是重試。
    拆檔弄丟這個旗標,重複開倉就會回來。
    """
    for name in ("create_order", "cancel_order", "set_leverage",
                 "set_margin_mode"):
        source = inspect.getsource(getattr(BingXAdapter, name))
        assert "is_write=True" in source, f"{name} 少了 is_write=True"


def test_reads_are_not_marked_as_writes():
    for name in ("get_ticker", "get_order", "get_balance", "get_positions"):
        source = inspect.getsource(getattr(BingXAdapter, name))
        assert "is_write=True" not in source, f"{name} 不該標成寫入"


def test_precision_parsing_did_not_change():
    """
    ccxt 的 precision 可能是小數位數也可能是最小單位。
    把 0.01 當成小數位數會得到 0 位,於是每一張單都被四捨五入成整數。
    """
    from agmcis.exchange.bingx.contracts import decimals

    assert decimals(2) == 2
    assert decimals(0.001) == 3
    assert decimals(0.01) == 2
    assert decimals(None) is None
    assert decimals("abc") is None


# ---------------- 金鑰只在一個地方 ----------------

def test_only_auth_touches_the_credentials():
    """
    金鑰洩漏是一個要靠結構防的問題。散在各處的時候,任何人加一行
    debug log 都可能把它印出來,而那一行會通過 code review。
    """
    offenders = []

    for path in BINGX.glob("*.py"):
        if path.name == "auth.py":
            continue
        text = path.read_text(encoding="utf-8")
        for line_no, line in enumerate(text.splitlines(), start=1):
            if line.lstrip().startswith("#"):
                continue
            if "EXCHANGE_CREDENTIALS" in line:
                offenders.append(f"{path.name}:{line_no}")

    assert offenders == [], (
        f"只有 auth.py 該碰 EXCHANGE_CREDENTIALS,這幾處也碰了:{offenders}"
    )


def test_auth_never_exposes_the_secret():
    from agmcis.exchange.bingx import auth

    text = auth.describe()

    from agmcis.config import settings
    secret = settings.EXCHANGE_CREDENTIALS.get("secret")

    if secret:
        assert secret not in text
    assert "secret" not in text.lower() or "no credentials" in text


def test_the_fingerprint_is_only_four_characters():
    from agmcis.exchange.bingx import auth
    from unittest.mock import patch

    from agmcis.config import settings

    with patch.dict(settings.EXCHANGE_CREDENTIALS,
                    {"apiKey": "ABCDEFGHIJKLMNOP"}):
        assert auth.fingerprint() == "MNOP"


def test_a_short_key_has_no_fingerprint():
    """
    後四碼不足以還原金鑰 —— 但一把只有三個字元的金鑰,後四碼就是它本身。
    """
    from agmcis.exchange.bingx import auth
    from unittest.mock import patch

    from agmcis.config import settings

    with patch.dict(settings.EXCHANGE_CREDENTIALS, {"apiKey": "abc"}):
        assert auth.fingerprint() is None


# ---------------- 簽章 ----------------

def test_we_do_not_hand_roll_the_signature():
    """
    第五節:不要靠模型記憶猜 API。自己手寫一份簽章等於把「猜」
    寫進送出訂單的那條路徑上,而簽章錯的失敗訊息看起來像金鑰有問題。
    """
    from agmcis.exchange.bingx import signer

    assert signer.SIGNS_REQUESTS == "ccxt"

    for path in BINGX.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for forbidden in ("hmac.new", "hashlib.sha256", "urlencode("):
            assert forbidden not in text, (
                f"{path.name} 看起來在自己簽章({forbidden})—— 見 signer.py"
            )


# ---------------- 檔案大小 ----------------

def test_no_module_grew_back_into_a_god_file():
    """
    拆完之後最容易發生的事,是所有新東西又加回 adapter.py。
    """
    for path in BINGX.glob("*.py"):
        lines = len(path.read_text(encoding="utf-8").splitlines())
        assert lines < 400, f"{path.name} 有 {lines} 行,又長回去了"


# ---------------- ccxt 真正給的形狀 ----------------
#
# 這一組驗的是同一類 bug:**我讀的欄位,ccxt 不是那樣給的。**
#
# 這一類最危險的地方是測試會是綠的 —— 假 exchange 是我們寫的,
# 照著我們的假設寫。所以這裡的輸入刻意用 ccxt 4.5.78 的 bingx
# **真正會產生**的形狀,不是我們想像中的形狀。

def _adapter(exchange):
    built = BingXAdapter(exchange_factory=lambda *a, **kw: exchange)
    built._markets_loaded = True
    return built


def test_next_funding_time_reads_the_next_one():
    """
    ccxt 的 fundingTimestamp 是**這一次**結算的時間,不是下一次。
    bingx 目前一律設成 None,但欄位叫 next 就該先讀 next ——
    哪天 ccxt 開始填它,舊寫法會在 next 裡放上一次的時間。
    """
    from unittest.mock import MagicMock

    exchange = MagicMock()
    exchange.fetch_funding_rate.return_value = {
        "fundingRate": 0.0001,
        "fundingTimestamp": 1_600_000_000_000,
        "nextFundingTimestamp": 1_700_000_000_000,
        "markPrice": 50000.0,
        "indexPrice": 49999.0,
    }

    result = _adapter(exchange).get_funding_rate("BTC/USDT")

    assert result["next_funding_time"] == 1_700_000_000_000


def test_a_linear_open_interest_lands_in_value_not_amount():
    """
    ccxt 對 linear 永續把 openInterestAmount 設成 None,數字放在
    openInterestValue,單位是 USDT。舊寫法 `amount or value` 把張數
    與 USDT 塞進同一個欄位,而欄位名叫 open_interest 讓人以為是張數。
    """
    from unittest.mock import MagicMock

    exchange = MagicMock()
    exchange.fetch_open_interest.return_value = {
        "openInterestAmount": None,
        "openInterestValue": 3_289_641_547.10,
        "timestamp": 1_700_000_000_000,
    }

    result = _adapter(exchange).get_open_interest("BTC/USDT")

    assert result["open_interest_amount"] is None
    assert result["open_interest_value"] == 3_289_641_547.10


def test_an_inverse_open_interest_lands_in_amount_not_value():
    from unittest.mock import MagicMock

    exchange = MagicMock()
    exchange.fetch_open_interest.return_value = {
        "openInterestAmount": 749.116,
        "openInterestValue": None,
        "timestamp": 1_700_000_000_000,
    }

    result = _adapter(exchange).get_open_interest("BTC/USDT")

    assert result["open_interest_amount"] == 749.116
    assert result["open_interest_value"] is None


def test_the_two_open_interest_units_stay_separate_fields():
    """
    張數與名目價值是兩個量。它們共用一個欄位的那一刻,
    就沒有人知道拿到的是哪一個了。
    """
    from agmcis.exchange.bingx import market

    source = inspect.getsource(market.MarketMixin.get_open_interest)

    assert '.get("openInterestAmount") or ' not in source
    assert "open_interest_amount" in source
    assert "open_interest_value" in source
