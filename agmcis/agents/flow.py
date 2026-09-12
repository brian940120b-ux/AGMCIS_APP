"""
資金流、情緒與總體事件 Agent(Master Prompt 第一 / 十一 / 三十八節)。

前面的 Agent 都在看價格與指標。這三個看的是**價格以外的東西**:

    OrderBookAgent   現在誰在掛單、掛在哪、價差多寬
    SentimentAgent   整體市場情緒(不是這一檔的新聞)
    MacroAgent       重大事件時間窗

## 為什麼把它們放在一起

因為它們有同一個弱點:**資料最容易缺,而缺的時候最容易被誤讀成中性。**

訂單簿抓不到、情緒分數沒有、事件日曆過期 —— 三種情況在一個寫得
不小心的 Agent 眼裡都會變成「0」,而 0 在這些欄位裡的意思是
「多空平衡」「市場中性」「沒有事件」。那是三個很有信心的判斷,
而我們其實什麼都不知道。

三個 Agent 一律在資料缺的時候 ABSTAIN,而 ABSTAIN 不是反對票。

## 訂單簿是不是「Order Flow」

不完全是。真正的 order flow 需要逐筆成交(誰吃了誰的單),
而 K 棒與訂單簿快照都推不出來。這裡做的是**訂單簿失衡**,
它是 order flow 的一個近似,而且有兩個已知的問題:

  1. **掛單可以撤。** 厚的買盤可能在價格接近時消失。
  2. **快照是一瞬間。** 兩次輪詢之間發生的事完全看不到。

所以 OrderBookAgent 的權重刻意壓低,而且它只在失衡非常明顯的時候
才出手 —— 一個 55/45 的訂單簿沒有任何資訊。
"""
import logging

from agmcis.agents.base import BaseAgent, Vote

logger = logging.getLogger("agmcis.agents.flow")


class OrderBookAgent(BaseAgent):
    """
    訂單簿失衡。

    權重低是刻意的:掛單可以撤,而且快照看不到兩次輪詢之間發生的事。
    """
    name = "order_book"
    weight = 0.5

    # 失衡超過這個絕對值才算有意義。0.3 = 買賣盤大約 65/35。
    STRONG_IMBALANCE = 0.30

    # 價差寬到這個程度就不該交易 —— 進出各吃一次,優勢會被吃光。
    WIDE_SPREAD_PCT = 0.20

    def _analyse(self, context):
        book = context.order_book

        if not book:
            return self.abstain("沒有訂單簿資料")

        imbalance = book.get("imbalance")
        spread_pct = book.get("spread_pct")

        reasons = []

        # 價差先看:再漂亮的失衡,在一個寬價差的市場裡也不值得進場。
        if spread_pct is not None and spread_pct >= self.WIDE_SPREAD_PCT:
            return self.opinion(
                Vote.WAIT, 60.0,
                [f"價差 {spread_pct:.3f}% 過寬,進出成本會吃掉優勢"],
            )

        if imbalance is None:
            return self.abstain("訂單簿沒有可用的失衡數字")

        if abs(imbalance) < self.STRONG_IMBALANCE:
            return self.abstain(
                f"訂單簿失衡 {imbalance:+.2f} 不顯著"
            )

        bid_pct = (1 + imbalance) / 2 * 100
        reasons.append(
            f"訂單簿 {bid_pct:.0f}% 在買方"
            f"(失衡 {imbalance:+.2f})"
        )

        if spread_pct is not None:
            reasons.append(f"價差 {spread_pct:.3f}%")

        # 失衡越極端信心越高,但上限壓在 70 —— 掛單可以撤。
        confidence = min(70.0, 40 + abs(imbalance) * 60)

        return self.opinion(
            Vote.LONG if imbalance > 0 else Vote.SHORT,
            confidence, reasons,
        )


class SentimentAgent(BaseAgent):
    """
    整體市場情緒(第一節的 Sentiment 分析)。

    與 NewsAgent 的差別很重要:NewsAgent 看的是**這一檔**的消息,
    這個 Agent 看的是**整體市場**。全市場恐慌的時候,個別標的的
    好消息不算數 —— 資金會先跑,再回頭看基本面。

    情緒的主要用途是**否決**而不是進場:極端貪婪時不追多、
    極端恐慌時不追空。所以它在中間區間一律棄權。
    """
    name = "sentiment"
    weight = 0.5

    # 情緒分數 -100 到 100。超過這個絕對值算極端。
    EXTREME = 60.0

    def _analyse(self, context):
        score = context.sentiment_score

        if score is None:
            return self.abstain("沒有市場情緒資料")

        if abs(score) < self.EXTREME:
            return self.abstain(f"市場情緒 {score:+.0f} 在中性區間")

        # 極端情緒是**反指標**:大家都站同一邊的時候,
        # 那一邊的停損就是燃料。
        #
        # 但它的信心刻意不高:情緒可以在極端區間待很久,
        # 而「太貪婪了所以要跌」是最貴的一種判斷。
        confidence = min(60.0, 35 + (abs(score) - self.EXTREME) * 0.5)

        if score > 0:
            return self.opinion(
                Vote.WAIT, confidence,
                [f"市場情緒 {score:+.0f} 極度貪婪 —— 不在這裡追多"],
            )
        return self.opinion(
            Vote.WAIT, confidence,
            [f"市場情緒 {score:+.0f} 極度恐慌 —— 不在這裡追空"],
        )


class MacroAgent(BaseAgent):
    """
    總體事件(第一節的 Macro / Economic Event、第五十一節)。

    這個 Agent 只投 WAIT,**永遠不投方向**。FOMC 前十分鐘的問題
    不是方向猜錯,是波動大到停損沒有意義 —— 而那是一個「不要進場」
    的理由,不是一個「往哪邊」的理由。

    它與 Risk Engine 的 news_risk 是同一份資料的兩種用途:
    Risk Engine 硬擋,這裡讓共識也看得到那件事。兩者刻意重複 ——
    一個只在風控層擋下來的封鎖,在 Agent 投票畫面上看不出來,
    使用者會以為系統是因為沒有訊號才不交易。
    """
    name = "macro"
    weight = 1.0

    def _analyse(self, context):
        risk = context.news_risk

        if risk is None:
            return self.abstain("沒有事件風險資料")

        if getattr(risk, "degraded", False) and not getattr(risk, "reasons", None):
            return self.abstain(
                "事件日曆不可信,無法判斷是否處於重大事件時間窗"
            )

        if getattr(risk, "blocks_entry", False):
            return self.opinion(
                Vote.WAIT, 95.0, list(risk.reasons)[:3],
            )

        multiplier = float(getattr(risk, "risk_multiplier", 1.0) or 1.0)
        if multiplier < 1.0:
            return self.opinion(
                Vote.WAIT, 50.0,
                list(risk.reasons)[:3] or [f"事件時間窗內,倉位應縮到 {multiplier:.0%}"],
            )

        return self.abstain("沒有重大事件")


FLOW_AGENTS = [OrderBookAgent, SentimentAgent, MacroAgent]
