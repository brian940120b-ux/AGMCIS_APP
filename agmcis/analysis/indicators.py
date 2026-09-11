"""
技術指標計算。

Phase 6 把原本散在兩個地方的指標計算合併:
    technical_service.get_indicators()   管線 A 用(EMA20/60、RSI、MACD、ATR)
    strategy.analyze_symbol()            管線 B 用(EMA20/50、RSI、MACD、ADX、ATR、量)

兩邊算的東西高度重疊但參數不同(EMA60 vs EMA50),所以同一檔標的在兩條管線
會得到不一樣的趨勢判斷 —— 這正是「Dashboard 顯示的訊號不是下單依據」的根源之一。

現在只有一份實作,兩邊看到的是同一組數字。
"""
import logging
from dataclasses import dataclass, field
from typing import Optional

from ta.momentum import RSIIndicator
from ta.trend import ADXIndicator, EMAIndicator, MACD
from ta.volatility import AverageTrueRange, BollingerBands

logger = logging.getLogger("agmcis.indicators")

MIN_CANDLES = 60


@dataclass
class Indicators:
    symbol: str
    timeframe: str
    price: Optional[float] = None

    ema20: Optional[float] = None
    ema50: Optional[float] = None
    ema60: Optional[float] = None
    rsi: Optional[float] = None
    macd: Optional[float] = None
    macd_signal: Optional[float] = None
    macd_hist: Optional[float] = None
    adx: Optional[float] = None
    atr: Optional[float] = None
    bb_upper: Optional[float] = None
    bb_lower: Optional[float] = None
    bb_width_pct: Optional[float] = None
    volume: Optional[float] = None
    volume_ma20: Optional[float] = None

    data_ok: bool = True
    data_error: Optional[str] = None
    issues: list = field(default_factory=list)

    # ---------------- 衍生判斷 ----------------

    @property
    def trend(self):
        """UNKNOWN 代表資料不足以判斷,不是「中性」。"""
        if not self.data_ok or self.ema20 is None or self.ema50 is None:
            return "UNKNOWN"
        return "BULLISH" if self.ema20 > self.ema50 else "BEARISH"

    @property
    def atr_pct(self):
        if not self.atr or not self.price or self.price <= 0:
            return None
        return self.atr / self.price * 100

    @property
    def volume_ratio(self):
        if not self.volume or not self.volume_ma20 or self.volume_ma20 <= 0:
            return None
        return self.volume / self.volume_ma20

    @property
    def has_trend_strength(self):
        """ADX > 25 慣例上代表趨勢成形。低於 20 代表盤整。"""
        return self.adx is not None and self.adx > 25

    @property
    def is_ranging(self):
        return self.adx is not None and self.adx < 20

    def to_dict(self):
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "price": self.price,
            "ema20": self.ema20,
            "ema50": self.ema50,
            "ema60": self.ema60,
            "rsi": self.rsi,
            "macd": self.macd,
            "macd_signal": self.macd_signal,
            "macd_hist": self.macd_hist,
            "adx": self.adx,
            "atr": self.atr,
            "atr_pct": self.atr_pct,
            "bb_upper": self.bb_upper,
            "bb_lower": self.bb_lower,
            "bb_width_pct": self.bb_width_pct,
            "volume": self.volume,
            "volume_ma20": self.volume_ma20,
            "volume_ratio": self.volume_ratio,
            "trend": self.trend,
            "data_ok": self.data_ok,
            "data_error": self.data_error,
            "issues": list(self.issues),
        }


def _last(series):
    value = float(series.iloc[-1])
    return None if value != value else round(value, 8)   # NaN -> None


def compute(df, symbol, timeframe="1h", price=None):
    """
    從 OHLCV DataFrame 算出全部指標。

    df 必須已經通過資料品質檢查(agmcis/data/quality.py)。
    這裡只負責算,不負責判斷資料可不可信。
    """
    if df is None or len(df) < MIN_CANDLES:
        have = 0 if df is None else len(df)
        return Indicators(
            symbol=symbol, timeframe=timeframe, price=price,
            data_ok=False,
            data_error=f"insufficient_candles: {have}/{MIN_CANDLES}",
        )

    try:
        close, high, low, volume = df["close"], df["high"], df["low"], df["volume"]

        macd_obj = MACD(close)
        bb = BollingerBands(close, window=20, window_dev=2)

        indicators = Indicators(
            symbol=symbol,
            timeframe=timeframe,
            price=price if price is not None else _last(close),
            ema20=_last(EMAIndicator(close, window=20).ema_indicator()),
            ema50=_last(EMAIndicator(close, window=50).ema_indicator()),
            ema60=_last(EMAIndicator(close, window=60).ema_indicator()),
            rsi=_last(RSIIndicator(close, window=14).rsi()),
            macd=_last(macd_obj.macd()),
            macd_signal=_last(macd_obj.macd_signal()),
            macd_hist=_last(macd_obj.macd_diff()),
            adx=_last(ADXIndicator(high=high, low=low, close=close, window=14).adx()),
            atr=_last(AverageTrueRange(
                high=high, low=low, close=close, window=14
            ).average_true_range()),
            bb_upper=_last(bb.bollinger_hband()),
            bb_lower=_last(bb.bollinger_lband()),
            volume=_last(volume),
            volume_ma20=_last(volume.rolling(20).mean()),
        )

    except Exception as exc:
        logger.exception(
            "指標計算失敗 | %s %s | %s", symbol, timeframe, exc,
        )
        return Indicators(
            symbol=symbol, timeframe=timeframe, price=price,
            data_ok=False, data_error=f"indicator_calc_failed: {exc}",
        )

    # 關鍵指標是 NaN 的話,整組視為不可用 —— 不要讓部分 None 混進評分
    critical = {
        "ema20": indicators.ema20, "ema50": indicators.ema50,
        "rsi": indicators.rsi, "macd": indicators.macd,
        "macd_signal": indicators.macd_signal, "atr": indicators.atr,
    }
    missing = [name for name, value in critical.items() if value is None]

    if missing:
        logger.warning(
            "指標含 NaN | %s %s | %s", symbol, timeframe, missing,
        )
        indicators.data_ok = False
        indicators.data_error = f"nan_indicators: {','.join(missing)}"
        return indicators

    if indicators.bb_upper and indicators.bb_lower and indicators.price:
        indicators.bb_width_pct = (
            (indicators.bb_upper - indicators.bb_lower) / indicators.price * 100
        )

    return indicators
