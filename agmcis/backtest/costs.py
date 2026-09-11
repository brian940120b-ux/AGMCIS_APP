"""
交易成本模型。

舊回測完全沒有成本:沒有手續費、沒有滑點、沒有 Funding、沒有點差。
對合約交易來說這不是小誤差 —— 一個一天進出好幾次的策略,
光手續費就可能把所有優勢吃光。

Master Prompt 第 106 條第 10 點明確要求所有策略都必須考慮
Fees / Slippage / Funding / Liquidity / Latency。

⚠️ 預設值是**保守的業界慣例值**,不是 BingX 的實際費率。
   正式評估策略前應該用 scripts/verify_bingx.py 取得真實費率並覆寫。
"""
from dataclasses import dataclass


@dataclass
class CostModel:
    """
    費率一律用「單邊」表示。開倉收一次、平倉收一次。

    maker_fee / taker_fee: 佔名目價值的比例(0.0005 = 0.05%)
    slippage_pct:          成交價偏離預期的比例。市價單一定有。
    spread_pct:            買賣價差。進場吃 ask、出場吃 bid,等於多付一次半價差。
    funding_rate_8h:       每 8 小時的資金費率。做多通常付、做空通常收(市場偏多時)。
    """
    maker_fee: float = 0.0002      # 0.02%
    taker_fee: float = 0.0005      # 0.05%
    slippage_pct: float = 0.0005   # 0.05%
    spread_pct: float = 0.0002     # 0.02%
    funding_rate_8h: float = 0.0001  # 0.01%
    funding_interval_hours: float = 8.0

    # 強平時交易所額外收的清算費
    liquidation_fee: float = 0.005

    def entry_price(self, price, direction_is_long, is_maker=False):
        """
        實際成交價。

        市價單一定比看到的價格差:買單吃 ask 並往上滑,賣單吃 bid 並往下滑。
        限價單假設沒有滑點,但仍然要付半個點差。
        """
        adverse = self.spread_pct / 2
        if not is_maker:
            adverse += self.slippage_pct

        return price * (1 + adverse) if direction_is_long else price * (1 - adverse)

    def exit_price(self, price, direction_is_long, is_maker=False):
        """出場的滑點方向相反:平多是賣出,平空是買入。"""
        adverse = self.spread_pct / 2
        if not is_maker:
            adverse += self.slippage_pct

        return price * (1 - adverse) if direction_is_long else price * (1 + adverse)

    def fee(self, notional, is_maker=False):
        rate = self.maker_fee if is_maker else self.taker_fee
        return abs(notional) * rate

    def funding_cost(self, notional, hours_held, direction_is_long):
        """
        持倉期間的資金費用。

        做多在多頭市場通常要付,做空通常收。這裡用正的 funding_rate 代表
        「多方付給空方」,與交易所慣例一致。
        """
        if hours_held <= 0 or self.funding_rate_8h == 0:
            return 0.0

        periods = hours_held / self.funding_interval_hours
        cost = abs(notional) * self.funding_rate_8h * periods
        return cost if direction_is_long else -cost

    def liquidation_cost(self, notional):
        return abs(notional) * self.liquidation_fee

    def round_trip_cost_pct(self):
        """
        一次完整進出的成本佔名目的比例。

        用來判斷「這個策略的平均獲利有沒有大到足以覆蓋成本」——
        如果平均每筆賺 0.1% 而成本是 0.15%,那策略再準也是虧的。
        """
        return 2 * (self.taker_fee + self.slippage_pct + self.spread_pct / 2)


# 保守預設。實際費率請以交易所為準。
DEFAULT_COSTS = CostModel()

# 完全沒有成本。**只用於驗證引擎本身的邏輯,不可用於評估策略。**
ZERO_COSTS = CostModel(
    maker_fee=0.0, taker_fee=0.0, slippage_pct=0.0,
    spread_pct=0.0, funding_rate_8h=0.0, liquidation_fee=0.0,
)
