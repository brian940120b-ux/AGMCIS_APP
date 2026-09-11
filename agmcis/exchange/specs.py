"""
合約規格快照。

問題:系統裡有幾個「保守固定值」是我憑常識填的,不是 BingX 給的:

  * 維持保證金率 10%(強平價由它算出)
  * Taker 0.05% / Maker 0.02%
  * 資金費率 0.01% / 8h

這些值決定強平價、決定成本、決定回測與模擬盤的結論。用猜的值算出來的
「策略有優勢」不能當真 —— Master Prompt 第五節說得很直接:
**不要靠模型記憶猜 API**,規格一律以官方最新文件與交易所實際回應為準。

這個模組的做法:

  1. VPS 上跑 `scripts/verify_bingx.py --write-specs`,把交易所實際回應
     寫成一份 JSON 快照(**唯讀操作,不下單**)。
  2. 系統啟動時載入那份快照。
  3. **沒有快照時不會安靜地用猜測值。** `is_calibrated()` 回 False,
     取值時明確標記 source="DEFAULT_GUESS",報告與日誌都看得到。

快照是資料,不是程式:它可以隨時重新產生,而且不進版控
(不同帳戶的費率不一樣,VIP 等級也會變)。
"""
import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Dict, Optional

logger = logging.getLogger("agmcis.exchange.specs")

DEFAULT_SPECS_PATH = os.getenv("BINGX_SPECS_PATH", "data/bingx_specs.json")

# 快照超過這個天數就視為過期。費率與分層會變。
STALE_AFTER_DAYS = 30

SOURCE_EXCHANGE = "EXCHANGE"
SOURCE_DEFAULT = "DEFAULT_GUESS"

# 沒有快照時的保守預設。**這些是猜的**,名字就這樣寫。
DEFAULT_MAINTENANCE_MARGIN_RATIO = 0.10
DEFAULT_TAKER_FEE = 0.0005
DEFAULT_MAKER_FEE = 0.0002
DEFAULT_FUNDING_RATE_8H = 0.0001


@dataclass
class ContractSpec:
    symbol: str
    market_type: str = "perpetual"
    tick_size: Optional[float] = None
    step_size: Optional[float] = None
    min_qty: Optional[float] = None
    max_qty: Optional[float] = None
    min_notional: Optional[float] = None
    contract_size: Optional[float] = None
    max_leverage: Optional[float] = None
    maintenance_margin_ratio: Optional[float] = None
    # 維持保證金分層。BingX 依倉位大小分層,倉位越大維持保證金率越高、
    # 強平價越近。單一數字會**低估大倉位的強平風險**。
    #
    # 格式:[{"notional_floor": 0, "mmr": 0.004, "max_leverage": 125}, ...]
    # 依 notional_floor 由小到大。某個名目價值適用的是
    # 「floor <= notional 的最後一層」。
    maintenance_margin_tiers: Optional[list] = None
    taker_fee: Optional[float] = None
    maker_fee: Optional[float] = None
    funding_rate_8h: Optional[float] = None
    funding_interval_hours: Optional[float] = None

    def to_dict(self):
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class SpecSnapshot:
    exchange: str = "bingx"
    testnet: bool = False
    captured_at: Optional[str] = None
    contracts: Dict[str, ContractSpec] = field(default_factory=dict)
    notes: list = field(default_factory=list)

    @property
    def captured_datetime(self):
        if not self.captured_at:
            return None
        try:
            return datetime.fromisoformat(self.captured_at)
        except ValueError:
            return None

    def age_days(self, now=None):
        captured = self.captured_datetime
        if captured is None:
            return None
        now = now or datetime.now(timezone.utc)
        if captured.tzinfo is None:
            captured = captured.replace(tzinfo=timezone.utc)
        return (now - captured).total_seconds() / 86400.0

    @property
    def is_stale(self):
        age = self.age_days()
        return age is None or age > STALE_AFTER_DAYS

    def to_dict(self):
        return {
            "exchange": self.exchange,
            "testnet": self.testnet,
            "captured_at": self.captured_at,
            "notes": list(self.notes),
            "contracts": {k: v.to_dict() for k, v in self.contracts.items()},
        }


class SpecStore:
    """
    快照的載入與查詢。

    取值一律回傳 (值, 來源)。呼叫端**拿不到一個匿名的數字** ——
    這樣就沒辦法把猜測值當成交易所給的值用。
    """

    def __init__(self, snapshot=None, path=None):
        self._snapshot = snapshot
        self._path = path or DEFAULT_SPECS_PATH
        self._loaded = snapshot is not None

    # ---- 載入 ----

    def load(self, path=None):
        path = path or self._path
        self._loaded = True

        if not os.path.exists(path):
            logger.warning(
                "找不到合約規格快照 %s —— 系統會使用保守預設值。"
                "請在 VPS 執行 scripts/verify_bingx.py --write-specs 產生。",
                path,
            )
            self._snapshot = None
            return None

        try:
            with open(path, encoding="utf-8") as handle:
                raw = json.load(handle)
        except Exception as exc:
            # 壞掉的快照不可以被當成「沒有快照」安靜帶過 ——
            # 那會讓系統用猜測值跑下去而沒人發現檔案壞了。
            logger.error("合約規格快照 %s 讀取失敗:%s", path, exc)
            self._snapshot = None
            raise

        contracts = {
            symbol: ContractSpec(symbol=symbol, **spec)
            for symbol, spec in (raw.get("contracts") or {}).items()
            if symbol
        }

        self._snapshot = SpecSnapshot(
            exchange=raw.get("exchange", "bingx"),
            testnet=bool(raw.get("testnet")),
            captured_at=raw.get("captured_at"),
            contracts=contracts,
            notes=list(raw.get("notes") or []),
        )

        if self._snapshot.is_stale:
            logger.warning(
                "合約規格快照已過期(%s 天前擷取,上限 %s 天)。費率與分層會變,請重新擷取。",
                round(self._snapshot.age_days() or -1, 1), STALE_AFTER_DAYS,
            )

        return self._snapshot

    @property
    def snapshot(self):
        if not self._loaded:
            self.load()
        return self._snapshot

    def is_calibrated(self, symbol=None):
        """有快照,而且(給了 symbol 時)那個標的在快照裡。"""
        snapshot = self.snapshot
        if snapshot is None:
            return False
        if symbol is None:
            return bool(snapshot.contracts)
        return symbol in snapshot.contracts

    def get(self, symbol):
        snapshot = self.snapshot
        if snapshot is None:
            return None
        return snapshot.contracts.get(symbol)

    # ---- 取值 ----

    def _value(self, symbol, attribute, default):
        spec = self.get(symbol)
        if spec is not None:
            value = getattr(spec, attribute, None)
            if value is not None:
                return float(value), SOURCE_EXCHANGE
        return default, SOURCE_DEFAULT

    def maintenance_margin_ratio(self, symbol, notional=None):
        """
        維持保證金率。

        notional 給了而且這個標的有分層資料時,回傳**那一層**的比率。
        沒有分層資料時退回單一數字 —— 那會低估大倉位的風險,
        但至少來源標記說得出它是哪裡來的。
        """
        if notional is not None:
            tier = self.tier_for(symbol, notional)
            if tier is not None and tier.get("mmr") is not None:
                return float(tier["mmr"]), SOURCE_EXCHANGE

        return self._value(symbol, "maintenance_margin_ratio",
                           DEFAULT_MAINTENANCE_MARGIN_RATIO)

    def tier_for(self, symbol, notional):
        """
        這個名目價值適用哪一層。沒有分層資料就回 None。

        規則:floor <= notional 的**最後一層**。
        名目價值小於第一層的 floor 時用第一層 ——
        第一層的 floor 通常是 0,但資料不一定乾淨。
        """
        spec = self.get(symbol)
        tiers = getattr(spec, "maintenance_margin_tiers", None) if spec else None

        if not tiers:
            return None

        try:
            ordered = sorted(tiers, key=lambda t: float(t.get("notional_floor", 0)))
        except (TypeError, ValueError):
            logger.warning("%s 的維持保證金分層資料格式有問題,忽略", symbol)
            return None

        selected = ordered[0]
        for tier in ordered:
            if float(tier.get("notional_floor", 0)) <= float(notional):
                selected = tier
            else:
                break

        return selected

    def max_leverage_for_notional(self, symbol, notional):
        """
        這個名目價值能用的最高槓桿。分層資料裡通常也帶這個。
        沒有資料就回 None(交給其他條件決定)。
        """
        tier = self.tier_for(symbol, notional)
        if tier is None:
            return None

        value = tier.get("max_leverage")
        return float(value) if value is not None else None

    def taker_fee(self, symbol):
        return self._value(symbol, "taker_fee", DEFAULT_TAKER_FEE)

    def maker_fee(self, symbol):
        return self._value(symbol, "maker_fee", DEFAULT_MAKER_FEE)

    def funding_rate_8h(self, symbol):
        return self._value(symbol, "funding_rate_8h", DEFAULT_FUNDING_RATE_8H)

    # ---- 報告 ----

    def calibration_report(self, symbols=None):
        """
        這個系統目前有多少數字是真的、多少是猜的。

        上線前必須看這份報告 —— 全部都是 DEFAULT_GUESS 的系統,
        它算出來的強平價與成本都只是估計。
        """
        snapshot = self.snapshot
        symbols = list(symbols or (snapshot.contracts if snapshot else []))

        report = {
            "calibrated": self.is_calibrated(),
            "path": self._path,
            "captured_at": snapshot.captured_at if snapshot else None,
            "age_days": round(snapshot.age_days(), 2) if snapshot and snapshot.age_days() is not None else None,
            "stale": snapshot.is_stale if snapshot else True,
            "testnet": snapshot.testnet if snapshot else None,
            "symbols": {},
            "warnings": [],
        }

        if snapshot is None:
            report["warnings"].append(
                "沒有合約規格快照。維持保證金率、費率、資金費率全部使用保守預設值 —— "
                "強平價與成本都只是估計,不是 BingX 的實際規格。"
            )
            return report

        if snapshot.is_stale:
            report["warnings"].append(
                f"快照已過期({report['age_days']} 天)。費率與維持保證金分層會變。"
            )

        if snapshot.testnet:
            report["warnings"].append(
                "快照擷取自 VST 測試環境。測試網的費率與分層不一定等於正式環境。"
            )

        for symbol in symbols:
            spec = self.get(symbol)
            tiers = getattr(spec, "maintenance_margin_tiers", None) if spec else None

            sources = {
                "maintenance_margin_ratio": self.maintenance_margin_ratio(symbol),
                "taker_fee": self.taker_fee(symbol),
                "maker_fee": self.maker_fee(symbol),
                "funding_rate_8h": self.funding_rate_8h(symbol),
            }
            report["symbols"][symbol] = {
                name: {"value": value, "source": source}
                for name, (value, source) in sources.items()
            }
            report["symbols"][symbol]["maintenance_margin_tiers"] = {
                "value": len(tiers) if tiers else 0,
                "source": SOURCE_EXCHANGE if tiers else SOURCE_DEFAULT,
            }

            if not tiers:
                report["warnings"].append(
                    f"{symbol} 沒有維持保證金分層資料。倉位越大維持保證金率越高,"
                    f"用單一數字會**低估大倉位的強平風險**。"
                )

            guessed = [n for n, (_, src) in sources.items() if src == SOURCE_DEFAULT]
            if guessed:
                report["warnings"].append(
                    f"{symbol} 的 {', '.join(guessed)} 仍是預設猜測值。"
                )

        return report


_STORE = None


def get_store():
    global _STORE
    if _STORE is None:
        _STORE = SpecStore()
    return _STORE


def set_store(store):
    """測試用。傳 None 會在下次取用時重新載入。"""
    global _STORE
    _STORE = store


def write_snapshot(snapshot, path=None):
    """把擷取到的規格寫成 JSON。由 VPS 上的驗證腳本呼叫。"""
    path = path or DEFAULT_SPECS_PATH
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)

    with open(path, "w", encoding="utf-8") as handle:
        json.dump(snapshot.to_dict(), handle, ensure_ascii=False, indent=2)

    return path
