"""
內建策略。

每一個都**同時支援做多與做空** —— 這是與舊版 strategies/*.py 最大的差別。
舊版的 sell_signal 是「平多」不是「做空」,所以系統宣稱支援 Long/Short
但策略層根本產不出空單。

⚠️ 所有門檻(RSI 30/70、ADX 25、量能 1.2 倍等)都是**業界慣例值,不是回測結果**。
   Phase 7 的回測與 Phase 8 的 Walk Forward 之後才會知道哪些值真的有效。
   在那之前不要把這些數字當成已驗證的參數。
"""
from agmcis.analysis.regime import Regime
from agmcis.core.enums import Direction
from agmcis.strategy.base import Strategy, StrategyVerdict


class TrendFollowing(Strategy):
    """
    順勢。EMA 排列 + MACD 動能 + ADX 趨勢強度同向才出手。
    盤整市不出手 —— 那是這個策略最容易虧錢的環境。
    """
    name = "trend_following"
    suitable_regimes = frozenset({
        Regime.STRONG_BULL.value, Regime.BULL.value,
        Regime.BEAR.value, Regime.STRONG_BEAR.value,
    })

    def evaluate(self, indicators, regime, candles=None):
        if not self.suits(regime):
            return self.wait(f"順勢策略不在 {regime.regime.value} 出手")

        ema20, ema50 = indicators.ema20, indicators.ema50
        macd, macd_signal = indicators.macd, indicators.macd_signal

        if None in (ema20, ema50, macd, macd_signal):
            return self.wait("缺少必要指標")

        bullish = ema20 > ema50 and macd > macd_signal
        bearish = ema20 < ema50 and macd < macd_signal

        if not bullish and not bearish:
            return self.wait("EMA 與 MACD 不同向")

        direction = Direction.LONG if bullish else Direction.SHORT
        reasons = [
            f"EMA20 {'>' if bullish else '<'} EMA50",
            f"MACD {'高於' if bullish else '低於'} 訊號線",
        ]

        confidence = 55.0

        if indicators.has_trend_strength:
            confidence += 20
            reasons.append(f"ADX {indicators.adx:.1f} 趨勢強")

        if indicators.volume_ratio and indicators.volume_ratio > 1.2:
            confidence += 10
            reasons.append(f"量能 {indicators.volume_ratio:.2f} 倍於均量")

        # RSI 到極端區代表追高殺低,扣分
        if indicators.rsi is not None:
            if bullish and indicators.rsi > 75:
                confidence -= 15
                reasons.append(f"RSI {indicators.rsi:.1f} 過熱,追高風險")
            elif bearish and indicators.rsi < 25:
                confidence -= 15
                reasons.append(f"RSI {indicators.rsi:.1f} 超賣,殺低風險")

        stop_loss, take_profit = self.atr_levels(indicators, direction)

        return StrategyVerdict(
            strategy=self.name, direction=direction,
            confidence=max(0.0, min(100.0, confidence)),
            reasons=reasons, stop_loss=stop_loss, take_profit=take_profit,
        )


class Breakout(Strategy):
    """
    突破。價格突破布林通道且量能配合。
    需要趨勢強度確認,否則容易是假突破。
    """
    name = "breakout"

    def evaluate(self, indicators, regime, candles=None):
        price = indicators.price
        upper, lower = indicators.bb_upper, indicators.bb_lower

        if None in (price, upper, lower):
            return self.wait("缺少布林通道")

        if price > upper:
            direction, reasons = Direction.LONG, [f"價格 {price:.6g} 突破布林上軌"]
        elif price < lower:
            direction, reasons = Direction.SHORT, [f"價格 {price:.6g} 跌破布林下軌"]
        else:
            return self.wait("價格在布林通道內")

        confidence = 50.0

        volume_ratio = indicators.volume_ratio
        if volume_ratio and volume_ratio > 1.5:
            confidence += 20
            reasons.append(f"量能 {volume_ratio:.2f} 倍,突破有量")
        elif volume_ratio and volume_ratio < 0.8:
            # 沒量的突破多半是假的
            confidence -= 20
            reasons.append(f"量能只有 {volume_ratio:.2f} 倍,突破無量")

        if indicators.has_trend_strength:
            confidence += 15
            reasons.append(f"ADX {indicators.adx:.1f} 確認趨勢")
        elif indicators.is_ranging:
            confidence -= 15
            reasons.append(f"ADX {indicators.adx:.1f} 盤整,突破容易是假的")

        stop_loss, take_profit = self.atr_levels(indicators, direction, 1.5, 3.0)

        return StrategyVerdict(
            strategy=self.name, direction=direction,
            confidence=max(0.0, min(100.0, confidence)),
            reasons=reasons, stop_loss=stop_loss, take_profit=take_profit,
        )


class Momentum(Strategy):
    """動能。MACD 柱狀圖與 RSI 同向,且不在極端區。"""
    name = "momentum"

    def evaluate(self, indicators, regime, candles=None):
        hist, rsi = indicators.macd_hist, indicators.rsi

        if hist is None or rsi is None:
            return self.wait("缺少 MACD 柱狀圖或 RSI")

        if hist > 0 and 50 <= rsi <= 70:
            direction = Direction.LONG
            reasons = [f"MACD 柱狀圖轉正", f"RSI {rsi:.1f} 在多方動能區"]
        elif hist < 0 and 30 <= rsi <= 50:
            direction = Direction.SHORT
            reasons = [f"MACD 柱狀圖轉負", f"RSI {rsi:.1f} 在空方動能區"]
        else:
            return self.wait(f"動能不明確(MACD hist {hist:.6g}, RSI {rsi:.1f})")

        confidence = 55.0

        if indicators.volume_ratio and indicators.volume_ratio > 1.0:
            confidence += 10
            reasons.append("量能高於均量")

        if regime.favours(direction.value):
            confidence += 15
            reasons.append(f"市況 {regime.regime.value} 與方向一致")

        stop_loss, take_profit = self.atr_levels(indicators, direction, 2.0, 3.0)

        return StrategyVerdict(
            strategy=self.name, direction=direction,
            confidence=max(0.0, min(100.0, confidence)),
            reasons=reasons, stop_loss=stop_loss, take_profit=take_profit,
        )


class MeanReversion(Strategy):
    """
    均值回歸。RSI 極端 + 價格離布林軌很遠。

    **只在盤整市出手。** 單邊趨勢市裡「超賣」可以一路更超賣,
    在那種環境下做均值回歸是最典型的虧錢方式。
    """
    name = "mean_reversion"
    suitable_regimes = frozenset({Regime.RANGE.value})

    def evaluate(self, indicators, regime, candles=None):
        if not self.suits(regime):
            return self.wait(
                f"均值回歸只在盤整市出手,目前 {regime.regime.value}"
            )

        rsi, price = indicators.rsi, indicators.price
        upper, lower = indicators.bb_upper, indicators.bb_lower

        if None in (rsi, price, upper, lower):
            return self.wait("缺少必要指標")

        if rsi < 30 and price <= lower:
            direction = Direction.LONG
            reasons = [f"RSI {rsi:.1f} 超賣", "價格觸及布林下軌"]
        elif rsi > 70 and price >= upper:
            direction = Direction.SHORT
            reasons = [f"RSI {rsi:.1f} 超買", "價格觸及布林上軌"]
        else:
            return self.wait(f"RSI {rsi:.1f} 未到極端區")

        confidence = 55.0

        if indicators.is_ranging:
            confidence += 15
            reasons.append(f"ADX {indicators.adx:.1f} 確認盤整")

        stop_loss, take_profit = self.atr_levels(indicators, direction, 1.5, 2.0)

        return StrategyVerdict(
            strategy=self.name, direction=direction,
            confidence=max(0.0, min(100.0, confidence)),
            reasons=reasons, stop_loss=stop_loss, take_profit=take_profit,
        )


BUILTIN_STRATEGIES = [TrendFollowing, Breakout, Momentum, MeanReversion]

# 第三十八節要求的其餘策略。它們在 registry 裡與上面四個平等,
# 但生命週期狀態預設是 PAPER —— 沒有經過 OOS 與 Walk Forward 驗證的
# 策略可以在模擬盤跑,不能碰真錢(第七十三節)。
from agmcis.strategy.extra import EXTRA_STRATEGIES   # noqa: E402

ALL_STRATEGIES = BUILTIN_STRATEGIES + EXTRA_STRATEGIES
