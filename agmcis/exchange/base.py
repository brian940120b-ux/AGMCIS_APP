"""
ExchangeAdapter — 交易所存取的統一介面。

為什麼要這一層:
  原本 exchange_engine.py 在模組載入時就 `ccxt.bingx()` 實體化,
  沒辦法注入、沒辦法測試、沒辦法切換到 paper 或 testnet。
  所有呼叫端也直接依賴 ccxt 的回傳格式,換交易所或換函式庫就整片要改。

這個介面定義「AGMCIS 需要交易所提供什麼」,而不是「ccxt 提供什麼」。
回傳格式固定,與底層函式庫無關。

實作:
  BingXAdapter       真實 BingX(Phase 2 只讀行情;下單介面保留但未接風控)
  PaperExchange      純模擬,不送任何真實請求(Phase 10)
  BingXTestAdapter   BingX 測試環境(Phase 11)

⚠️ 下單相關方法在 Trading Rules Engine(Phase 4)與 Execution Engine(Phase 12)
   完成前一律不可直接呼叫 —— 它們沒有精度驗證、沒有風控、沒有狀態機。
"""
from abc import ABC, abstractmethod
from typing import Dict, List, Optional

from agmcis.core.enums import MarketType


class ExchangeAdapter(ABC):
    """
    所有方法在失敗時的約定:
      - 「核心行情」(ticker / ohlcv)失敗 -> 拋 ExchangeUnavailableError。
        呼叫端必須知道自己沒有資料,不能拿舊值當成新值默默用下去。
      - 「輔助資料」(funding / open interest / order book)失敗 -> 回傳 None。
        這些缺了不該讓整個 Dashboard 掛掉。
    """

    name: str = "base"

    # ---------------- 市場資訊 ----------------

    @abstractmethod
    def to_market_symbol(self, symbol: str, market_type: MarketType) -> str:
        """把 'BTC/USDT' 轉成該交易所 / 該市場型態的符號。"""

    @abstractmethod
    def list_markets(self, market_type: MarketType) -> List[Dict]:
        """
        該市場型態下所有可交易的合約。

        Standard 與 Perpetual 必須分開回傳 —— 兩者的 contract_size、
        tick_size、step_size 與槓桿上限都不同。
        """

    @abstractmethod
    def get_trading_rules(self, symbol: str, market_type: MarketType):
        """
        單一合約的交易規則(TradingRules)。

        ⚠️ 一律從交易所動態取得,不可寫死。精度或最小量搞錯會被拒單,
        或成交出非預期的數量。
        """

    # ---------------- 行情 ----------------

    @abstractmethod
    def get_ticker(self, symbol: str, market_type: MarketType) -> Dict:
        """
        必須包含 mark_price 與 index_price(可以是 None)。

        **不可以用 last 冒充標記價。** 強平與未實現損益用的都是標記價;
        用最新成交價代替會在插針行情裡算錯,而那正是最需要算對的時候。
        取不到就誠實地回 None,讓呼叫端知道它沒有這個資訊。
        """
        """失敗拋 ExchangeUnavailableError。"""

    @abstractmethod
    def get_ohlcv(self, symbol: str, timeframe: str, limit: int,
                  market_type: MarketType) -> List[List]:
        """
        回傳 ccxt 慣例的原始列:[[timestamp_ms, open, high, low, close, volume], ...]
        刻意不轉成 dict 或 DataFrame —— 這一層不決定上層要用什麼格式。
        失敗拋 ExchangeUnavailableError。
        """

    @abstractmethod
    def get_tickers(self, market_type: MarketType) -> Dict[str, Dict]:
        """
        一次取得該市場所有合約的 ticker,用來做成交量排名。
        失敗拋 ExchangeUnavailableError —— 排名拿不到資料時應該是空的,
        而不是用殘缺清單去挑交易標的。
        """

    @abstractmethod
    def get_order_book(self, symbol: str, limit: int,
                       market_type: MarketType) -> Optional[Dict]:
        """失敗回傳 None。"""

    # ---------------- 衍生品資料(合約特有) ----------------

    @abstractmethod
    def get_funding_rate(self, symbol: str, market_type: MarketType) -> Optional[Dict]:
        """失敗回傳 None。Standard Futures 沒有資金費率,應回傳 None。"""

    @abstractmethod
    def get_long_short_ratio(self, symbol: str, market_type=None) -> Optional[Dict]:
        """
        多空持倉比。交易所不支援就回 None。

        **不要回 1.0 當預設值。** 「沒有資料」與「多空平衡」是兩件事,
        而後者是一個確定的判斷。
        """
        return None

    def get_liquidations(self, symbol: str, limit: int = 50,
                         market_type=None) -> Optional[Dict]:
        """近期爆倉。交易所不支援就回 None。"""
        return None

    def get_open_interest(self, symbol: str, market_type: MarketType) -> Optional[Dict]:
        """失敗回傳 None。"""

    # ---------------- 帳戶(需要 API Key) ----------------

    @abstractmethod
    def get_balance(self) -> Dict:
        ...

    @abstractmethod
    def get_positions(self, symbols: Optional[List[str]] = None) -> List[Dict]:
        ...

    # ---------------- 下單 ----------------

    @abstractmethod
    def create_order(self, symbol: str, side, quantity: float, order_type,
                     price: Optional[float] = None, market_type=None,
                     client_order_id: Optional[str] = None,
                     reduce_only: bool = False, params: Optional[Dict] = None) -> Dict:
        """⚠️ 風控與 Trading Rules 完成前不可直接呼叫。"""

    @abstractmethod
    def cancel_order(self, order_id: str, symbol: str, market_type=None) -> Dict:
        ...

    @abstractmethod
    def get_order(self, order_id: str, symbol: str, market_type=None) -> Dict:
        """
        查詢單一訂單狀態。

        這是 OrderState.UNKNOWN 時的唯一正確反應 ——
        先查清楚交易所到底收到什麼,再決定,絕不盲目重送。
        """

    # ---------------- 能力宣告 ----------------

    def supports(self, capability: str) -> bool:
        """
        查詢這個 adapter 是否支援某項能力,例如 'funding_rate'、'websocket'、
        'standard_futures'。呼叫端據此決定要不要顯示或使用某功能,
        而不是呼叫了才發現拋例外。
        """
        return capability in self.capabilities()

    def capabilities(self) -> frozenset:
        return frozenset()
