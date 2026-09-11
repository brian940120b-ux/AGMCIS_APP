"""
Correlation Engine(Master Prompt 第六十節)。

要回答的問題只有一個:**這幾個部位其實是不是同一個賭注?**

同時做多 BTC、ETH、SOL,每一筆的單筆風險都是 1%,帳面上看起來是
三個獨立的 1%。但這三檔在多數時候的日內相關係數在 0.8 以上 ——
真正承擔的是一個 3% 的「大型加密貨幣 Beta」曝險。Risk Engine 在這個
模組出現以前看不見這件事。

## 三個必須講清楚的前提

**一、用報酬率算,不是用價格算。**
兩條同時上漲的價格序列,價格相關係數幾乎一定接近 1,不管它們的
漲跌節奏有沒有關係。那個數字沒有資訊量,只反映兩者都有趨勢。

**二、樣本不足的相關係數是雜訊,不是估計值。**
20 根 K 棒算出來的 0.9 可以純粹是運氣。樣本不足時這裡回 None ——
**不是回 0**。0 代表「已知不相關」,那是這個模組最危險的一種謊。

**三、危機的時候相關係數會跑到 1。**
平時 0.3 的兩檔,在連環爆倉的時候會一起跌。所以這裡算出來的
相關係數是**平時**的下限估計,實際壓力下只會更高。用它來放寬限制
是錯的,用它來收緊限制才是對的 —— portfolio.py 只拿它做加法。
"""
import logging
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("agmcis.risk.correlation")

# 少於這個樣本數就不給相關係數。60 根 1 小時 K 棒 = 2.5 天。
MIN_SAMPLES = 60

# 超過這個絕對值視為「同一個賭注」。
DEFAULT_THRESHOLD = 0.7


def returns(closes):
    """
    收盤價 -> 對數報酬率。

    用對數報酬率而不是百分比變化:對數報酬率可加,而且對大幅波動
    比較不會產生不對稱的偏誤。非正數的價格直接讓那一段中斷 ——
    那是資料錯誤,不是行情。
    """
    values = []
    previous = None

    for close in closes:
        try:
            price = float(close)
        except (TypeError, ValueError):
            previous = None
            continue

        if price <= 0:
            previous = None
            continue

        if previous is not None:
            values.append(math.log(price / previous))

        previous = price

    return values


def pearson(a, b):
    """
    Pearson 相關係數。長度不同時取共同的**最後** n 筆 ——
    兩個序列的最新一根必須是同一個時間,對齊尾端比對齊開頭安全。

    任一邊變異數為 0(整段完全沒動)時回 None:
    相關係數在那個情況下沒有定義,回 0 會被誤讀成「已知不相關」。
    """
    n = min(len(a), len(b))
    if n < 2:
        return None

    left, right = a[-n:], b[-n:]

    mean_l = sum(left) / n
    mean_r = sum(right) / n

    cov = sum((x - mean_l) * (y - mean_r) for x, y in zip(left, right))
    var_l = sum((x - mean_l) ** 2 for x in left)
    var_r = sum((y - mean_r) ** 2 for y in right)

    if var_l <= 0 or var_r <= 0:
        return None

    return cov / math.sqrt(var_l * var_r)


def beta(symbol_returns, benchmark_returns):
    """
    相對基準(通常是 BTC)的 Beta。

    Beta 2.0 代表 BTC 動 1%、這檔平均動 2% —— 同樣的名目金額,
    實際承擔的市場曝險是兩倍。
    """
    n = min(len(symbol_returns), len(benchmark_returns))
    if n < 2:
        return None

    left, right = symbol_returns[-n:], benchmark_returns[-n:]

    mean_l = sum(left) / n
    mean_r = sum(right) / n

    cov = sum((x - mean_l) * (y - mean_r) for x, y in zip(left, right))
    var_r = sum((y - mean_r) ** 2 for y in right)

    if var_r <= 0:
        return None

    return cov / var_r


@dataclass
class CorrelationMatrix:
    """
    成對相關係數。查不到的組合回 None,**不回 0**。

    `samples` 是實際用到的最短序列長度 —— 讀的人要知道這組數字
    是用多少資料算出來的。
    """
    values: Dict[Tuple[str, str], float] = field(default_factory=dict)
    betas: Dict[str, float] = field(default_factory=dict)
    benchmark: Optional[str] = None
    samples: int = 0
    symbols: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @staticmethod
    def _key(a, b):
        return (a, b) if a <= b else (b, a)

    def get(self, a, b):
        if a == b:
            return 1.0
        return self.values.get(self._key(a, b))

    def is_correlated(self, a, b, threshold=DEFAULT_THRESHOLD):
        """
        三態:True / False / None。

        **None 代表不知道,呼叫端必須自己決定怎麼處理 ——
        而唯一安全的處理方式是當成 True。** 這個方法刻意不替呼叫端
        決定,因為「不知道」被悄悄變成 False 正是這個模組要防的事。
        """
        value = self.get(a, b)
        if value is None:
            return None
        return abs(value) >= threshold

    def to_dict(self):
        return {
            "benchmark": self.benchmark,
            "samples": self.samples,
            "symbols": list(self.symbols),
            "pairs": {f"{a}|{b}": round(v, 4) for (a, b), v in self.values.items()},
            "betas": {s: round(v, 4) for s, v in self.betas.items()},
            "warnings": list(self.warnings),
        }


def build(closes_by_symbol, min_samples=MIN_SAMPLES, benchmark=None):
    """
    從 {symbol: [close, ...]} 建出相關係數矩陣。

    序列長度不同是正常的(不同標的上市時間不同),pearson() 會對齊尾端。
    任何一檔樣本不足,它與其他檔的所有組合都不給值 ——
    而不是「用它有的那幾根硬算」。
    """
    matrix = CorrelationMatrix(benchmark=benchmark)

    series = {}
    for symbol, closes in (closes_by_symbol or {}).items():
        values = returns(closes)
        if len(values) < min_samples:
            matrix.warnings.append(
                f"{symbol} 只有 {len(values)} 筆報酬率(需要 {min_samples}),"
                f"不納入相關性計算"
            )
            continue
        series[symbol] = values

    matrix.symbols = sorted(series)

    if len(matrix.symbols) < 2:
        if closes_by_symbol:
            matrix.warnings.append(
                "可用標的少於兩檔,算不出任何相關係數"
            )
        return matrix

    matrix.samples = min(len(v) for v in series.values())

    for i, a in enumerate(matrix.symbols):
        for b in matrix.symbols[i + 1:]:
            value = pearson(series[a], series[b])
            if value is None:
                matrix.warnings.append(f"{a} / {b} 變異數為 0,相關係數無定義")
                continue
            matrix.values[matrix._key(a, b)] = value

    if benchmark and benchmark in series:
        for symbol in matrix.symbols:
            if symbol == benchmark:
                continue
            value = beta(series[symbol], series[benchmark])
            if value is not None:
                matrix.betas[symbol] = value

    return matrix


def build_from_market(symbols, timeframe="1h", limit=200,
                      benchmark="BTC/USDT", fetch=None,
                      min_samples=MIN_SAMPLES):
    """
    去抓資料再建矩陣。抓不到的標的會被跳過並記在 warnings ——
    **不會**讓整個矩陣變成空的,因為那會讓「不知道」擴散到所有組合。

    fetch(symbol) -> DataFrame 或 None,預設用 agmcis.data.market_data。
    """
    if fetch is None:
        from agmcis.data import market_data

        def fetch(symbol):
            return market_data.get_ohlcv(symbol, timeframe=timeframe, limit=limit)

    wanted = list(dict.fromkeys(list(symbols or []) + ([benchmark] if benchmark else [])))

    closes = {}
    failures = []

    for symbol in wanted:
        try:
            frame = fetch(symbol)
        except Exception as exc:
            logger.warning("Correlation | FETCH_FAILED | %s | %s", symbol, exc)
            failures.append(f"{symbol}: {type(exc).__name__}")
            continue

        if frame is None or len(frame) == 0:
            failures.append(f"{symbol}: 沒有資料")
            continue

        closes[symbol] = list(frame["close"])

    matrix = build(closes, min_samples=min_samples, benchmark=benchmark)
    for failure in failures:
        matrix.warnings.append(f"取不到 K 線:{failure}")

    return matrix


# ---------------- 快取 ----------------
#
# 相關係數變得很慢:兩檔標的的關係不會在十分鐘內翻轉。但抓 N 檔標的的
# K 線是 N 次 API 呼叫,每次下單前都重抓會把 rate limit 吃光,而且
# 讓風控判斷的延遲跟標的數量成正比。
#
# 快取鍵包含標的集合:換了一組標的就是另一個矩陣,不能沿用。

CACHE_TTL_SECONDS = 900

_CACHE = {"key": None, "matrix": None, "expires_at": 0.0}


def get_matrix(symbols, timeframe="1h", limit=None, benchmark="BTC/USDT",
               ttl=CACHE_TTL_SECONDS, now=None, fetch=None):
    """
    取得(必要時重建)相關係數矩陣。

    抓資料失敗**不會**拋例外,會回一個 warnings 裡寫著失敗原因、
    values 是空的矩陣 —— 而空矩陣在 portfolio.assess() 眼裡等於
    「全部當成相關」,也就是最保守的那一邊。
    """
    import time

    from agmcis.config import settings

    if limit is None:
        limit = getattr(settings, "CORRELATION_LOOKBACK", 200)

    clock = now if now is not None else time.time()
    key = (tuple(sorted(set(symbols or []))), timeframe, limit, benchmark)

    if _CACHE["key"] == key and _CACHE["expires_at"] > clock:
        return _CACHE["matrix"]

    try:
        matrix = build_from_market(
            symbols, timeframe=timeframe, limit=limit,
            benchmark=benchmark, fetch=fetch,
        )
    except Exception as exc:
        logger.exception("Correlation | BUILD_FAILED")
        matrix = CorrelationMatrix(
            benchmark=benchmark,
            warnings=[f"相關係數建立失敗:{type(exc).__name__}: {exc}"],
        )

    _CACHE.update({"key": key, "matrix": matrix, "expires_at": clock + ttl})
    return matrix


def clear_cache():
    _CACHE.update({"key": None, "matrix": None, "expires_at": 0.0})
