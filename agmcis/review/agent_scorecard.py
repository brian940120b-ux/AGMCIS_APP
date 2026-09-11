"""
Agent 貢獻度。

十二個 Agent 裡一定有幾個是沒用的。問題是哪幾個 —— 而那個問題
不會自己浮出來,因為每個 Agent 看起來都很合理,而且共識機制會把
它們的意見混在一起。

這裡對每個 Agent 問三個問題:

  1. **它有沒有在發言?** 棄權率 95% 的 Agent 等於不存在。
  2. **它投對的時候,結果比較好嗎?** 這才是「有貢獻」的定義。
  3. **它的票跟結果有沒有關係?** 沒關係就是雜訊,而雜訊在共識裡
     會稀釋掉真正有訊號的票。

第 3 點需要一個統計檢定,不能只看「同意時的期望值比較高」。
用隨機投票的 Agent 去測,有一半的機會它同意時的期望值會比較高 ——
那完全是運氣。所以這裡算兩組平均的標準誤,差距小於兩個標準誤時
一律判定為 UNKNOWN(跟雜訊分不出來),而不是 CONTRIBUTING。

刻意**不做**的事:自動把沒貢獻的 Agent 關掉。

樣本小的時候,「沒貢獻」跟「運氣不好」分不出來。自動關掉會讓系統
在每一次小樣本的波動裡改變自己的組成,那是過擬合的另一種形式。
這裡只回報,調整由人決定。
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# 低於這個票數不下結論
MIN_VOTES = 20

# 兩組平均的差距要超過幾個標準誤才算「不是雜訊」。
# 2 個標準誤大約對應 95% 信賴區間。
BASE_MIN_SIGMA = 2.0

# ---- 多重比較校正 ----
#
# 單獨看一個 Agent 時,2 個標準誤代表「純屬巧合的機率約 5%」。
# 但我們**同時看十二個**。十二個各有 5% 的機率,
# 至少一個假陽性的機率是 1 - 0.95^12 ≈ 46%。
#
# 也就是說:一套全部由隨機投票組成的 Agent 群,有將近一半的機會
# 會產生「至少一個有貢獻的 Agent」。那個結論毫無意義,
# 但它看起來跟真的一模一樣。
#
# Bonferroni 校正:把門檻拉高,讓**整組**的假陽性率維持在 5%。
# 這是最保守的校正方式(它會讓真的有貢獻的 Agent 比較難被認出來),
# 而在這個場景保守是對的 —— 誤判一個沒用的 Agent 有貢獻,
# 會讓人把資金配置建立在雜訊上。
#
# 查表值:對應 0.05 / n 的雙尾 z 值。
_BONFERRONI_SIGMA = {
    1: 1.96, 2: 2.24, 3: 2.39, 4: 2.50, 5: 2.58,
    6: 2.64, 7: 2.69, 8: 2.73, 9: 2.77, 10: 2.81,
    12: 2.87, 15: 2.94, 20: 3.02, 30: 3.14,
}

# 超過查表範圍時用的保底值
MAX_SIGMA = 3.3


def min_sigma_for(comparison_count):
    """
    同時比較 n 個 Agent 時的門檻。

    n <= 1 時沒有多重比較問題,用基準值。
    """
    if comparison_count <= 1:
        return BASE_MIN_SIGMA

    for count in sorted(_BONFERRONI_SIGMA):
        if comparison_count <= count:
            return _BONFERRONI_SIGMA[count]

    return MAX_SIGMA

# 棄權率高於這個比例就標記為「幾乎不發言」
HIGH_ABSTAIN_RATIO = 0.9

DIRECTIONAL_VOTES = ("做多", "做空")


@dataclass
class AgentScore:
    agent: str = ""
    trades_seen: int = 0
    directional_votes: int = 0
    wait_votes: int = 0
    abstentions: int = 0

    agreed_trades: int = 0          # 投票方向與實際開倉方向一致
    agreed_wins: int = 0
    agreed_pnl: float = 0.0
    agreed_pnl_sq: float = 0.0      # 平方和,用來算標準誤

    disagreed_trades: int = 0       # 投了反方向,但共識仍然開了
    disagreed_wins: int = 0
    disagreed_pnl: float = 0.0
    disagreed_pnl_sq: float = 0.0

    notes: List[str] = field(default_factory=list)

    # 這一輪同時比較了幾個 Agent。決定門檻要拉多高。
    comparison_count: int = 1

    @property
    def abstain_ratio(self):
        return (self.abstentions / self.trades_seen) if self.trades_seen else 0.0

    @property
    def agreed_win_rate(self):
        return (self.agreed_wins / self.agreed_trades * 100) if self.agreed_trades else 0.0

    @property
    def agreed_expectancy(self):
        return (self.agreed_pnl / self.agreed_trades) if self.agreed_trades else 0.0

    @property
    def disagreed_expectancy(self):
        return (
            self.disagreed_pnl / self.disagreed_trades
            if self.disagreed_trades else 0.0
        )

    @property
    def reliable(self):
        return self.agreed_trades >= MIN_VOTES

    @property
    def edge(self):
        """
        這個 Agent 同意時的期望值,減掉它反對時的期望值。

        兩邊都要有足夠樣本才算得出來 —— 只看「它同意時賺錢」是不夠的,
        因為那可能只是反映整體績效,跟這個 Agent 沒有關係。
        """
        if self.agreed_trades < MIN_VOTES or self.disagreed_trades < MIN_VOTES:
            return None
        return self.agreed_expectancy - self.disagreed_expectancy

    @property
    def edge_stderr(self):
        """兩組平均差距的標準誤。樣本不足時回 None。"""
        if self.agreed_trades < 2 or self.disagreed_trades < 2:
            return None

        agreed_var = _variance(
            self.agreed_pnl, self.agreed_pnl_sq, self.agreed_trades,
        )
        disagreed_var = _variance(
            self.disagreed_pnl, self.disagreed_pnl_sq, self.disagreed_trades,
        )

        return (
            agreed_var / self.agreed_trades
            + disagreed_var / self.disagreed_trades
        ) ** 0.5

    @property
    def edge_sigmas(self):
        """
        差距是幾個標準誤。這個數字才是判斷依據 ——
        `edge` 只是兩個平均值相減,隨機投票也會有非零的值。
        """
        edge, stderr = self.edge, self.edge_stderr
        if edge is None or not stderr:
            return None
        return edge / stderr

    @property
    def min_sigma(self):
        return min_sigma_for(self.comparison_count)

    @property
    def verdict(self):
        if self.trades_seen == 0:
            return "NO_DATA"
        if self.abstain_ratio >= HIGH_ABSTAIN_RATIO:
            return "SILENT"
        if not self.reliable:
            return "UNKNOWN"
        if self.edge is None:
            return "UNKNOWN"

        sigmas = self.edge_sigmas
        if sigmas is None or abs(sigmas) < self.min_sigma:
            # 跟雜訊分不出來。這是「不知道」,不是「沒貢獻」。
            return "UNKNOWN"

        return "CONTRIBUTING" if sigmas > 0 else "NO_EDGE"

    def to_dict(self):
        return {
            "agent": self.agent,
            "trades_seen": self.trades_seen,
            "directional_votes": self.directional_votes,
            "wait_votes": self.wait_votes,
            "abstentions": self.abstentions,
            "abstain_ratio": round(self.abstain_ratio, 4),
            "agreed_trades": self.agreed_trades,
            "agreed_win_rate": round(self.agreed_win_rate, 2),
            "agreed_expectancy": round(self.agreed_expectancy, 4),
            "disagreed_trades": self.disagreed_trades,
            "disagreed_expectancy": round(self.disagreed_expectancy, 4),
            "edge": round(self.edge, 4) if self.edge is not None else None,
            "edge_sigmas": (
                round(self.edge_sigmas, 2) if self.edge_sigmas is not None else None
            ),
            "reliable": self.reliable,
            "min_sigma": self.min_sigma,
            "comparison_count": self.comparison_count,
            "verdict": self.verdict,
            "notes": list(self.notes),
        }


@dataclass
class ScorecardReport:
    agents: List[Dict] = field(default_factory=list)
    trades_with_votes: int = 0
    trades_without_votes: int = 0
    warnings: List[str] = field(default_factory=list)

    def to_dict(self):
        return {
            "agents": list(self.agents),
            "trades_with_votes": self.trades_with_votes,
            "trades_without_votes": self.trades_without_votes,
            "warnings": list(self.warnings),
        }


def _variance(total, total_sq, count):
    """樣本變異數。count < 2 時沒有意義,呼叫端要先擋。"""
    mean = total / count
    variance = (total_sq / count) - mean * mean
    # 浮點誤差可能算出極小的負數
    return max(0.0, variance) * count / (count - 1)


def _votes(trade):
    votes = trade.get("agent_votes")
    return votes if isinstance(votes, dict) else None


def build(trades):
    report = ScorecardReport()
    scores: Dict[str, AgentScore] = {}

    for trade in trades:
        if trade.get("status") != "CLOSED":
            continue

        pnl = trade.get("pnl_usdt")
        votes = _votes(trade)

        if pnl is None:
            continue

        if not votes:
            # 沒有投票紀錄的交易一律排除。補一個「未知」進去
            # 會讓每個 Agent 的樣本數看起來比實際多。
            report.trades_without_votes += 1
            continue

        report.trades_with_votes += 1
        pnl = float(pnl)
        actual = trade.get("signal")

        for agent, vote in votes.items():
            score = scores.setdefault(agent, AgentScore(agent=agent))
            score.trades_seen += 1

            if vote in DIRECTIONAL_VOTES:
                score.directional_votes += 1
                if vote == actual:
                    score.agreed_trades += 1
                    score.agreed_pnl += pnl
                    score.agreed_pnl_sq += pnl * pnl
                    if pnl > 0:
                        score.agreed_wins += 1
                else:
                    score.disagreed_trades += 1
                    score.disagreed_pnl += pnl
                    score.disagreed_pnl_sq += pnl * pnl
                    if pnl > 0:
                        score.disagreed_wins += 1
            elif vote == "觀望":
                score.wait_votes += 1
            else:
                score.abstentions += 1

    # 門檻要依「同時比較了幾個」調整。先設好再判定 ——
    # verdict 會用到它。
    comparable = [
        s for s in scores.values()
        if s.abstain_ratio < HIGH_ABSTAIN_RATIO and s.edge is not None
    ]
    comparison_count = max(1, len(comparable))

    for score in scores.values():
        score.comparison_count = comparison_count

    for score in scores.values():
        _annotate(score)

    report.agents = sorted(
        (s.to_dict() for s in scores.values()),
        key=lambda x: (x["edge_sigmas"] is None, -(x["edge_sigmas"] or 0)),
    )

    _add_warnings(report)
    return report


def _annotate(score):
    if score.abstain_ratio >= HIGH_ABSTAIN_RATIO:
        score.notes.append(
            f"棄權率 {score.abstain_ratio * 100:.0f}%,幾乎不發言 —— "
            f"它對共識沒有影響,但也沒有害處。"
        )

    if score.verdict == "NO_EDGE":
        score.notes.append(
            f"同意時的期望值比反對時**低** {abs(score.edge_sigmas):.1f} 個標準誤 ——"
            f"這張票不只是雜訊,方向可能是反的。"
        )

    if score.verdict == "UNKNOWN" and score.directional_votes:
        if score.edge is None:
            score.notes.append(
                f"方向票 {score.directional_votes} 張,但同意/反對兩邊都要有 "
                f"{MIN_VOTES} 筆才算得出貢獻度。目前還不知道。"
            )
        else:
            score.notes.append(
                f"同意與反對的期望值差距只有 {abs(score.edge_sigmas or 0):.1f} "
                f"個標準誤(門檻 {score.min_sigma:.2f},已依同時比較 "
                f"{score.comparison_count} 個 Agent 做多重比較校正),"
                f"跟隨機投票分不出來。這是「不知道」,不是「沒貢獻」。"
            )


def _add_warnings(report):
    if report.trades_with_votes == 0:
        report.warnings.append(
            "沒有任何帶投票紀錄的已平倉交易。Agent 貢獻度無法計算 —— "
            "歸因欄位是 Phase 15 才加的,之前的交易補不回來。"
        )
        return

    if report.trades_without_votes:
        report.warnings.append(
            f"{report.trades_without_votes} 筆交易沒有投票紀錄,已排除。"
        )

    if report.trades_with_votes < MIN_VOTES:
        report.warnings.append(
            f"只有 {report.trades_with_votes} 筆帶投票紀錄的交易"
            f"(門檻 {MIN_VOTES})。所有 Agent 的判定都會是 UNKNOWN,"
            f"那是正確的 —— 樣本不夠就是不知道。"
        )

    comparable = [a for a in report.agents if a.get("edge") is not None]
    if len(comparable) > 1:
        threshold = comparable[0]["min_sigma"]
        report.warnings.append(
            f"同時比較 {len(comparable)} 個 Agent,門檻已從 {BASE_MIN_SIGMA} "
            f"拉高到 {threshold:.2f} 個標準誤(Bonferroni 校正)。"
            f"不校正的話,一套全部由隨機投票組成的 Agent 群也有將近一半的機會"
            f"產生「至少一個有貢獻的 Agent」。"
        )

    silent = [a["agent"] for a in report.agents if a["verdict"] == "SILENT"]
    if silent:
        report.warnings.append(
            f"幾乎不發言的 Agent:{', '.join(silent)}。"
            f"它們沒有害處,但也沒有在做事。"
        )

    no_edge = [a["agent"] for a in report.agents if a["verdict"] == "NO_EDGE"]
    if no_edge:
        report.warnings.append(
            f"拿不出貢獻的 Agent:{', '.join(no_edge)}。"
            f"**這不是自動關掉它們的理由** —— 樣本小的時候"
            f"「沒貢獻」跟「運氣不好」分不出來。調整由人決定。"
        )
