"""
市場資料品質 gate。

Phase 2 的核心:**資料異常時系統必須回 NO TRADE,而不是猜測。**

原本的行為是:指標算失敗 -> 回傳全 None -> 評分函式給 50 分「中性」
-> 系統分不出「市場中性」與「資料壞掉」的差別,有可能在壞資料上開倉。
Phase 0.5 已經讓指標失敗變成 data_ok=False,這裡再往前一步 ——
在指標計算**之前**就檢查原始 K 棒本身是否可信。

檢查項目(對應 Master Prompt 第 50 條):
  缺 K 棒 / 重複 K 棒 / 時間戳錯誤 / 離群值 / gap / 錯誤 symbol / 資料過期

嚴重度分兩級:
  ERROR   資料不可信,一律 NO TRADE
  WARNING 值得記錄但不阻擋交易
"""
import time
from dataclasses import dataclass, field
from typing import List, Optional

# 各時間框架的毫秒數。用來判斷 K 棒之間應該相隔多久。
TIMEFRAME_MS = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "6h": 21_600_000,
    "12h": 43_200_000,
    "1d": 86_400_000,
    "1D": 86_400_000,
}

SEVERITY_ERROR = "ERROR"
SEVERITY_WARNING = "WARNING"

# 單根 K 棒相對前一根收盤的變動超過這個比例就視為離群。
# 加密貨幣本來就會暴漲暴跌,門檻放寬到 50%,只抓明顯的壞資料(例如價格變成 0 或 10 倍)。
OUTLIER_CHANGE_PCT = 50.0

# 最後一根 K 棒允許落後多久(以該時間框架的幾倍計)。
STALE_MULTIPLIER = 3


@dataclass
class QualityIssue:
    code: str
    severity: str
    detail: str

    def __str__(self):
        return f"[{self.severity}] {self.code}: {self.detail}"


@dataclass
class QualityReport:
    symbol: str
    timeframe: str
    candle_count: int = 0
    issues: List[QualityIssue] = field(default_factory=list)

    @property
    def errors(self):
        return [i for i in self.issues if i.severity == SEVERITY_ERROR]

    @property
    def warnings(self):
        return [i for i in self.issues if i.severity == SEVERITY_WARNING]

    @property
    def ok(self):
        """只有完全沒有 ERROR 才算可信。WARNING 不阻擋交易。"""
        return not self.errors

    @property
    def summary(self):
        if self.ok and not self.warnings:
            return None
        return "; ".join(str(i) for i in self.issues)

    def add(self, code, severity, detail):
        self.issues.append(QualityIssue(code, severity, detail))
        return self

    def to_dict(self):
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "candle_count": self.candle_count,
            "ok": self.ok,
            "errors": [str(i) for i in self.errors],
            "warnings": [str(i) for i in self.warnings],
        }


def check_ohlcv(rows, symbol, timeframe, min_candles=60, now_ms=None):
    """
    檢查原始 OHLCV 列 [[ts, o, h, l, c, v], ...]。

    回傳 QualityReport。report.ok 為 False 時呼叫端必須拒絕交易 ——
    不要回退到「用有問題的資料算個大概」。
    """
    report = QualityReport(symbol=symbol, timeframe=timeframe)

    if not rows:
        return report.add("EMPTY", SEVERITY_ERROR, "沒有取得任何 K 棒")

    report.candle_count = len(rows)

    if len(rows) < min_candles:
        report.add(
            "INSUFFICIENT_CANDLES", SEVERITY_ERROR,
            f"只有 {len(rows)} 根,指標需要至少 {min_candles} 根",
        )

    _check_shape_and_values(rows, report)

    # 形狀就壞掉的話,後面的時間序列檢查沒有意義
    if any(i.code in ("MALFORMED_ROW", "NON_NUMERIC") for i in report.errors):
        return report

    _check_timestamps(rows, timeframe, report)
    _check_ohlc_relationships(rows, report)
    _check_outliers(rows, report)
    _check_staleness(rows, timeframe, report, now_ms)
    _check_volume(rows, report)

    return report


def _check_shape_and_values(rows, report):
    for index, row in enumerate(rows):
        if not isinstance(row, (list, tuple)) or len(row) < 6:
            report.add(
                "MALFORMED_ROW", SEVERITY_ERROR,
                f"第 {index} 根 K 棒格式不正確: {row!r}",
            )
            return

        try:
            values = [float(v) for v in row[:6]]
        except (TypeError, ValueError):
            report.add(
                "NON_NUMERIC", SEVERITY_ERROR,
                f"第 {index} 根 K 棒含非數值: {row!r}",
            )
            return

        timestamp, open_, high, low, close, volume = values

        if any(v != v for v in values):  # NaN
            report.add("NAN_VALUE", SEVERITY_ERROR, f"第 {index} 根 K 棒含 NaN")
            return

        if min(open_, high, low, close) <= 0:
            report.add(
                "NON_POSITIVE_PRICE", SEVERITY_ERROR,
                f"第 {index} 根 K 棒有非正價格: O={open_} H={high} L={low} C={close}",
            )
            return

        if volume < 0:
            report.add(
                "NEGATIVE_VOLUME", SEVERITY_ERROR,
                f"第 {index} 根 K 棒成交量為負: {volume}",
            )
            return

        if timestamp <= 0:
            report.add(
                "INVALID_TIMESTAMP", SEVERITY_ERROR,
                f"第 {index} 根 K 棒時間戳無效: {timestamp}",
            )
            return


def _check_timestamps(rows, timeframe, report):
    timestamps = [float(r[0]) for r in rows]

    duplicates = len(timestamps) - len(set(timestamps))
    if duplicates:
        report.add(
            "DUPLICATE_CANDLE", SEVERITY_ERROR,
            f"有 {duplicates} 根重複時間戳的 K 棒",
        )

    out_of_order = sum(
        1 for a, b in zip(timestamps, timestamps[1:]) if b <= a
    )
    if out_of_order:
        report.add(
            "OUT_OF_ORDER", SEVERITY_ERROR,
            f"有 {out_of_order} 處時間戳沒有遞增",
        )

    interval = TIMEFRAME_MS.get(timeframe)
    if not interval or len(timestamps) < 2:
        return

    gaps = []
    for a, b in zip(timestamps, timestamps[1:]):
        delta = b - a
        if delta > interval * 1.5:
            missing = int(round(delta / interval)) - 1
            if missing > 0:
                gaps.append(missing)

    if gaps:
        total_missing = sum(gaps)
        # 少量缺漏在交易所很常見(維護、低流動性),超過 5% 才視為不可信
        severity = (
            SEVERITY_ERROR if total_missing > len(timestamps) * 0.05
            else SEVERITY_WARNING
        )
        report.add(
            "MISSING_CANDLES", severity,
            f"缺少約 {total_missing} 根 K 棒,分布在 {len(gaps)} 個區段",
        )


def _check_ohlc_relationships(rows, report):
    """high 必須是四者最大、low 必須是四者最小。違反代表資料來源有問題。"""
    broken = 0
    for row in rows:
        _, open_, high, low, close = (float(v) for v in row[:5])
        if high < low or high < max(open_, close) or low > min(open_, close):
            broken += 1

    if broken:
        report.add(
            "INVALID_OHLC", SEVERITY_ERROR,
            f"有 {broken} 根 K 棒的 OHLC 關係不成立(high/low 不是極值)",
        )


def _check_outliers(rows, report):
    closes = [float(r[4]) for r in rows]
    outliers = []

    for index, (previous, current) in enumerate(zip(closes, closes[1:]), start=1):
        if previous <= 0:
            continue
        change_pct = abs(current - previous) / previous * 100
        if change_pct > OUTLIER_CHANGE_PCT:
            outliers.append((index, round(change_pct, 1)))

    if outliers:
        detail = ", ".join(f"#{i} {pct}%" for i, pct in outliers[:3])
        report.add(
            "OUTLIER", SEVERITY_ERROR,
            f"有 {len(outliers)} 根 K 棒單根變動超過 {OUTLIER_CHANGE_PCT}%: {detail}",
        )


def _check_staleness(rows, timeframe, report, now_ms=None):
    interval = TIMEFRAME_MS.get(timeframe)
    if not interval:
        return

    now_ms = now_ms if now_ms is not None else time.time() * 1000
    last_ts = float(rows[-1][0])
    age_ms = now_ms - last_ts

    if age_ms > interval * STALE_MULTIPLIER:
        report.add(
            "STALE_DATA", SEVERITY_ERROR,
            f"最後一根 K 棒已經是 {round(age_ms / 60000, 1)} 分鐘前"
            f"({timeframe} 應該在 {round(interval * STALE_MULTIPLIER / 60000, 1)} 分鐘內)",
        )


def _check_volume(rows, report):
    """整段視窗成交量全為 0 代表該合約沒有在交易,訊號沒有意義。"""
    volumes = [float(r[5]) for r in rows]
    if volumes and sum(volumes) == 0:
        report.add("ZERO_VOLUME", SEVERITY_ERROR, "整段視窗成交量為 0")
        return

    zero_count = sum(1 for v in volumes if v == 0)
    if volumes and zero_count > len(volumes) * 0.3:
        report.add(
            "SPARSE_VOLUME", SEVERITY_WARNING,
            f"{zero_count}/{len(volumes)} 根 K 棒成交量為 0,流動性可能不足",
        )


def check_ticker(ticker, symbol, max_age_seconds=60, now_ms=None):
    """
    檢查 ticker 是否可信。價格是下單與停損判斷的依據,不能用壞值。
    """
    report = QualityReport(symbol=symbol, timeframe="ticker")

    if not ticker:
        return report.add("EMPTY", SEVERITY_ERROR, "沒有取得 ticker")

    price = ticker.get("price")

    if price is None:
        return report.add("NO_PRICE", SEVERITY_ERROR, "ticker 沒有價格")

    try:
        price = float(price)
    except (TypeError, ValueError):
        return report.add("NON_NUMERIC", SEVERITY_ERROR, f"價格非數值: {price!r}")

    if price != price or price <= 0:
        return report.add("NON_POSITIVE_PRICE", SEVERITY_ERROR, f"價格無效: {price}")

    bid, ask = ticker.get("bid"), ticker.get("ask")
    if bid and ask:
        try:
            bid, ask = float(bid), float(ask)
            if ask < bid:
                report.add(
                    "CROSSED_BOOK", SEVERITY_ERROR,
                    f"ask {ask} 低於 bid {bid},報價交叉",
                )
            elif bid > 0:
                spread_pct = (ask - bid) / bid * 100
                if spread_pct > 1.0:
                    report.add(
                        "WIDE_SPREAD", SEVERITY_WARNING,
                        f"買賣價差 {round(spread_pct, 2)}%,流動性可能不足",
                    )
        except (TypeError, ValueError):
            pass

    timestamp = ticker.get("timestamp")
    if timestamp:
        now_ms = now_ms if now_ms is not None else time.time() * 1000
        age_seconds = (now_ms - float(timestamp)) / 1000
        if age_seconds > max_age_seconds:
            report.add(
                "STALE_TICKER", SEVERITY_ERROR,
                f"ticker 已經是 {round(age_seconds, 1)} 秒前的資料"
                f"(上限 {max_age_seconds} 秒)",
            )

    return report
