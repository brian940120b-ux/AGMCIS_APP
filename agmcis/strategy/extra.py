"""
第三十八節要求的其餘策略:RSI、MACD、VWAP、Volatility、Market Structure。

這五個策略與 builtin.py 那四個的差別不只是指標不同 —— 它們在找**不同
種類的機會**,所以在不同市況出手:

    rsi_reversion       超賣 / 超買反轉      盤整市
    macd_cross          動能轉折            趨勢市
    vwap_reversion      偏離成交量均價       盤整市
    volatility_breakout 波動壓縮後的擴張     任何市況
    market_structure    HH/HL 結構 + 掃蕩    趨勢市

## 為什麼不把它們全部放進預設投票

**放進去了,但預設狀態是 PAPER。** 第七十三節的生命週期是為這件事
存在的:一個沒有經過 OOS 與 Walk Forward 驗證的策略可以在模擬盤跑,
不能碰真錢。九個策略一起投票、而且全部沒驗證過,不會比四個更好 ——
只會讓「哪一個在賺錢」更難分辨。

## RSI 策略的一個陷阱

「RSI 30 以下買」在趨勢市是災難:強勢下跌可以讓 RSI 在 20 附近待上
好幾天,每一根都是「超賣」。所以 rsi_reversion 只在盤整市出手,
而且要求價格同時碰到布林通道 —— 兩個獨立的超賣訊號比一個可靠。
"""
import logging

from agmcis.analysis.regime import Regime
from agmcis.analysis.structure import DOWNTREND, UPTREND
from agmcis.core.enums import Direction
from agmcis.strategy.base import Strategy, StrategyVerdict

logger = logging.getLogger("agmcis.strategy.extra")

RANGING = frozenset({Regime.RANGE.value})
TRENDING = frozenset({
    Regime.STRONG_BULL.value, Regime.BULL.value,
    Regime.BEAR.value, Regime.STRONG_BEAR.value,
})


class RsiReversion(Strategy):
    """
    RSI 反轉。**只在盤整市出手。**

    「RSI 30 以下買」在趨勢市是災難:強勢下跌可以讓 RSI 在 20 附近
    待上好幾天,每一根都是「超賣」,而每一次進場都是接刀。

    而且要求布林通道同時確認 —— 兩個獨立的超賣訊號比一個可靠。
    """
    name = "rsi_reversion"
    suitable_regimes = RANGING

    OVERSOLD = 30.0
    OVERBOUGHT = 70.0

    def evaluate(self, indicators, regime, candles=None):
        if not self.suits(regime):
            return self.wait(f"RSI 反轉不在 {regime.regime.value} 出手")

        rsi = indicators.rsi
        price = indicators.price

        if rsi is None or price is None:
            return self.wait("缺少 RSI 或價格")

        oversold = rsi <= self.OVERSOLD
        overbought = rsi >= self.OVERBOUGHT

        if not oversold and not overbought:
            return self.wait(f"RSI {rsi:.1f} 在中性區")

        direction = Direction.LONG if oversold else Direction.SHORT
        reasons = [f"RSI {rsi:.1f} {'超賣' if oversold else '超買'}"]
        confidence = 50.0

        # 布林通道確認
        band = indicators.bb_lower if oversold else indicators.bb_upper
        if band is not None:
            touched = price <= band if oversold else price >= band
            if touched:
                confidence += 20
                reasons.append(
                    f"價格{'跌破' if oversold else '突破'}布林{'下' if oversold else '上'}軌"
                )
            else:
                # RSI 說超賣但價格還在通道內 —— 訊號不夠強,但不是反對。
                confidence -= 10
                reasons.append("布林通道尚未確認")

        # 反轉策略最怕趨勢還在延伸。ADX 偏高就減分。
        if indicators.adx is not None and indicators.adx > 25:
            confidence -= 20
            reasons.append(f"ADX {indicators.adx:.1f} 偏高,反轉風險升高")

        stop_loss, take_profit = self.atr_levels(
            indicators, direction, stop_mult=1.5, target_mult=2.0,
        )

        return StrategyVerdict(
            strategy=self.name, direction=direction,
            confidence=max(0.0, min(100.0, confidence)),
            reasons=reasons, stop_loss=stop_loss, take_profit=take_profit,
        )


class MacdCross(Strategy):
    """
    MACD 轉折。柱狀體剛剛翻正 / 翻負,而且方向與 EMA 排列一致。

    只看「MACD 在訊號線上方」會在整段趨勢裡一直發出訊號。
    這裡看的是**柱狀體的正負與絕對值**:剛翻過去且還很小,
    代表轉折剛發生而不是已經走了一半。
    """
    name = "macd_cross"
    suitable_regimes = TRENDING

    # 柱狀體佔價格的比例。超過這個就算「已經走了一段」。
    FRESH_HIST_PCT = 0.3

    def evaluate(self, indicators, regime, candles=None):
        if not self.suits(regime):
            return self.wait(f"MACD 轉折不在 {regime.regime.value} 出手")

        hist = indicators.macd_hist
        ema20, ema50 = indicators.ema20, indicators.ema50
        price = indicators.price

        if None in (hist, ema20, ema50, price) or price <= 0:
            return self.wait("缺少 MACD 柱狀體或 EMA")

        if hist == 0:
            return self.wait("MACD 柱狀體為 0,方向不明")

        bullish = hist > 0
        direction = Direction.LONG if bullish else Direction.SHORT

        # EMA 排列必須同向 —— MACD 在盤整裡會反覆穿越
        ema_bullish = ema20 > ema50
        if ema_bullish != bullish:
            return self.wait("MACD 柱狀體與 EMA 排列不同向")

        hist_pct = abs(hist) / price * 100
        if hist_pct > self.FRESH_HIST_PCT:
            return self.wait(
                f"MACD 柱狀體已達價格的 {hist_pct:.2f}%,轉折已經走過一段"
            )

        confidence = 55.0
        reasons = [
            f"MACD 柱狀體{'轉正' if bullish else '轉負'}({hist_pct:.3f}% of 價格,還很新)",
            f"EMA20 {'>' if bullish else '<'} EMA50",
        ]

        if indicators.has_trend_strength:
            confidence += 15
            reasons.append(f"ADX {indicators.adx:.1f} 趨勢強")

        stop_loss, take_profit = self.atr_levels(indicators, direction)

        return StrategyVerdict(
            strategy=self.name, direction=direction,
            confidence=max(0.0, min(100.0, confidence)),
            reasons=reasons, stop_loss=stop_loss, take_profit=take_profit,
        )


class VwapReversion(Strategy):
    """
    VWAP 回歸。價格顯著偏離成交量加權均價時,賭它回來。

    用 VWAP 而不是 SMA:VWAP 是「大部分成交量發生的價格」,
    偏離它代表現在的價格沒有量的支撐。SMA 只是價格的平均,
    一段沒有人交易的無量上漲會把 SMA 拉上去,但那個價位沒有意義。

    **需要原始 K 棒。** 拿不到就 WAIT —— 用指標湊一個近似的 VWAP
    會產生一個名字叫 VWAP 但其實不是 VWAP 的訊號。
    """
    name = "vwap_reversion"
    suitable_regimes = RANGING
    needs_candles = True

    # 偏離多少才算顯著(佔 VWAP 的百分比)
    MIN_DEVIATION_PCT = 1.0

    def evaluate(self, indicators, regime, candles=None):
        if not self.suits(regime):
            return self.wait(f"VWAP 回歸不在 {regime.regime.value} 出手")

        if not candles:
            return self.wait("沒有 K 棒,算不出 VWAP")

        from agmcis.analysis.structure import vwap

        level = vwap(candles)
        price = indicators.price

        if level is None:
            return self.wait("算不出 VWAP(通常是沒有成交量資料)")
        if price is None or level <= 0:
            return self.wait("缺少價格")

        deviation_pct = (price - level) / level * 100

        if abs(deviation_pct) < self.MIN_DEVIATION_PCT:
            return self.wait(
                f"價格偏離 VWAP 只有 {deviation_pct:+.2f}%,不值得出手"
            )

        # 價格在 VWAP 上方 -> 賭它跌回來
        direction = Direction.SHORT if deviation_pct > 0 else Direction.LONG
        confidence = 50.0 + min(25.0, abs(deviation_pct) * 5)

        reasons = [
            f"價格偏離 VWAP {deviation_pct:+.2f}%(VWAP {level:.8g})",
        ]

        if indicators.adx is not None and indicators.adx > 25:
            confidence -= 20
            reasons.append(f"ADX {indicators.adx:.1f} 偏高,回歸可能不會發生")

        stop_loss, take_profit = self.atr_levels(
            indicators, direction, stop_mult=1.5, target_mult=2.0,
        )

        return StrategyVerdict(
            strategy=self.name, direction=direction,
            confidence=max(0.0, min(100.0, confidence)),
            reasons=reasons, stop_loss=stop_loss, take_profit=take_profit,
        )


class VolatilityBreakout(Strategy):
    """
    波動壓縮後的擴張。布林通道收窄到極致之後,價格往哪邊走就跟。

    這個策略不挑市況,因為壓縮本身就是市況的一部分 ——
    壓縮發生在盤整末期,而突破的方向決定接下來是什麼市況。

    與 builtin 的 Breakout 差別:那個看「價格突破通道」,
    這個看「通道本身有多窄」。一個在寬通道裡的突破與一個在
    極窄通道裡的突破,後者的續航力通常好得多。
    """
    name = "volatility_breakout"

    # 通道寬度佔價格的比例低於這個就算壓縮
    SQUEEZE_WIDTH_PCT = 2.0

    def evaluate(self, indicators, regime, candles=None):
        width = indicators.bb_width_pct
        price = indicators.price
        upper, lower = indicators.bb_upper, indicators.bb_lower

        if None in (width, price, upper, lower):
            return self.wait("缺少布林通道資料")

        if width > self.SQUEEZE_WIDTH_PCT:
            return self.wait(
                f"通道寬度 {width:.2f}% 未達壓縮標準(<{self.SQUEEZE_WIDTH_PCT}%)"
            )

        above = price > upper
        below = price < lower

        if not above and not below:
            return self.wait(
                f"通道已壓縮到 {width:.2f}% 但價格還在通道內 —— 等突破方向"
            )

        direction = Direction.LONG if above else Direction.SHORT
        confidence = 55.0
        reasons = [
            f"通道壓縮到 {width:.2f}%",
            f"價格{'突破上軌' if above else '跌破下軌'}",
        ]

        # 壓縮突破最需要量能確認:沒有量的突破常常是假的
        if indicators.volume_ratio is not None:
            if indicators.volume_ratio > 1.5:
                confidence += 20
                reasons.append(f"量能 {indicators.volume_ratio:.2f} 倍於均量")
            elif indicators.volume_ratio < 1.0:
                confidence -= 20
                reasons.append(
                    f"量能只有 {indicators.volume_ratio:.2f} 倍 —— "
                    f"沒有量的突破常常是假的"
                )

        stop_loss, take_profit = self.atr_levels(indicators, direction)

        return StrategyVerdict(
            strategy=self.name, direction=direction,
            confidence=max(0.0, min(100.0, confidence)),
            reasons=reasons, stop_loss=stop_loss, take_profit=take_profit,
        )


class MarketStructure(Strategy):
    """
    市場結構。HH/HL(或 LH/LL)+ 流動性掃蕩後的回收。

    這是唯一一個看「價格在哪裡做過什麼」而不是「指標數字多少」的策略。
    一根跌破前低的 K 棒與一根跌到同樣價位但那裡沒有前低的 K 棒,
    在 RSI 眼裡完全一樣,在這裡是兩件事。

    最強的訊號是:結構向上 + 剛掃過前低又收回來。那代表下方的停損
    被吃掉之後價格沒有跟著跌 —— 賣壓用完了。

    **需要原始 K 棒。**
    """
    name = "market_structure"
    suitable_regimes = TRENDING
    needs_candles = True

    def evaluate(self, indicators, regime, candles=None):
        if not self.suits(regime):
            return self.wait(f"市場結構策略不在 {regime.regime.value} 出手")

        if not candles:
            return self.wait("沒有 K 棒,算不出市場結構")

        from agmcis.analysis import structure as structure_module

        structure = structure_module.analyse(candles)

        if structure.trend == structure_module.UNKNOWN:
            return self.wait(f"結構判斷不出來:{structure.detail}")

        if structure.trend == structure_module.RANGE:
            return self.wait("結構上是盤整,高低點方向不一致")

        direction = (
            Direction.LONG if structure.trend == UPTREND else Direction.SHORT
        )
        confidence = 55.0
        reasons = [f"結構 {structure.trend}:{structure.detail}"]

        # 掃蕩後回收是最強的加分項
        sweep = structure_module.liquidity_sweep(candles)
        if sweep.swept:
            favourable = (
                (direction is Direction.LONG and sweep.direction == "low")
                or (direction is Direction.SHORT and sweep.direction == "high")
            )
            if favourable:
                confidence += 25
                reasons.append(f"流動性掃蕩後回收:{sweep.detail}")
            else:
                confidence -= 15
                reasons.append(f"掃蕩方向不利:{sweep.detail}")
        elif sweep.broke_out:
            # 穿過去而且站穩了 —— 結構可能正在改變,不是好的順勢點
            confidence -= 20
            reasons.append(f"穿過後站穩,結構可能正在改變:{sweep.detail}")

        # 停損放在結構之外,不是固定的 ATR 倍數 ——
        # 這是這個策略的重點:被打到代表結構破了,不是「跌了 2 個 ATR」。
        stop_loss = take_profit = None
        price = indicators.price

        if price:
            level = (
                structure.support if direction is Direction.LONG
                else structure.resistance
            )
            if level:
                # 往外推一點緩衝,避免正好在前低被掃到
                stop_loss = round(
                    level * (0.998 if direction is Direction.LONG else 1.002), 8,
                )
                risk = abs(price - stop_loss)
                if risk > 0:
                    take_profit = round(
                        price + risk * 2 * (1 if direction is Direction.LONG else -1),
                        8,
                    )
                    reasons.append(
                        f"停損放在結構{'支撐' if direction is Direction.LONG else '壓力'}"
                        f" {level:.8g} 之外"
                    )

        if stop_loss is None:
            stop_loss, take_profit = self.atr_levels(indicators, direction)
            reasons.append("結構價位取不到,退回 ATR 停損")

        return StrategyVerdict(
            strategy=self.name, direction=direction,
            confidence=max(0.0, min(100.0, confidence)),
            reasons=reasons, stop_loss=stop_loss, take_profit=take_profit,
        )


EXTRA_STRATEGIES = [
    RsiReversion, MacdCross, VwapReversion,
    VolatilityBreakout, MarketStructure,
]
