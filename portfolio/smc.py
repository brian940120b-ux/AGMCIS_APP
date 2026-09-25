"""
SMC(Smart Money Concepts)的機械化定義 · 2026-09-25

═══ 這支的由來 ═══
執政官傳了一組 IG 貼文(@1336cryptoclub,帳號自己標示「AI 內容」,
文案「用 SMC 搞定一天的餐錢」),內容是一套四步驟流程:

  第一步 4H   畫出昨天高點 / 昨天低點  → 今天的流動性目標
  第二步 15M  結構轉換 CHOCH + 下方出現看漲訂單塊(Bullish OB)→ 偏多
  第三步 5M   等回踩訂單塊 + 拉漲後的失衡區(FVG),不追價
  第四步      價格回補失衡區、出現吞沒陽 K → 進場做多
  風控        停損:失衡區下方 · 止盈:4H 昨天高點
  結果那張    「結構對 / 時區對 / 進場乾淨 → 正盈出場 / 今天餐錢到手」

貼文附的是**一個範例的宣稱結果**,沒有樣本數、沒有期間、沒有一筆
賠錢的例子。宣稱不等於證據,但兩者要分清楚:**那不代表它錯,也不
代表它對** —— 它代表它是一個**還沒被檢驗的假說**,跟前 13 個一樣處理。

═══ 「進場乾淨」這四個字,是這整套東西真正的問題 ═══
照字面,「結構對 + 時區對 + 進場乾淨 → 正盈」是**無法否證**的:
「乾淨」是事後判斷的。賺了就說結構對、進場乾淨;賠了就說那筆不算
標準型態。只要「這筆算不算數」可以在看到結果**之後**才決定,
這套流程就永遠 100% 正確 —— 而那等於什麼都沒說。

下面的機械化把這個退路拿掉了:規則在**收盤那一刻**就決定這筆算不
算數,而且不管後來賺賠**一律算進統計**。所以這裡跑的是一個比貼文
本身更嚴格、也更公平的檢驗 —— 貼文有權說「那筆不乾淨」,這裡沒有。

═══ 這支為什麼必須先寫、先提交、才准跑 ═══
SMC 在社群裡是**憑眼睛畫**的方法:同一張圖,十個人標出十組不同的
訂單塊。那種東西回測不了 —— 不是因為它沒道理,是因為它不是一條規則。

要檢驗它,得先把它翻譯成一條規則,而**翻譯本身就是選擇**。如果我
先跑、看了結果再回頭調整「訂單塊該怎麼定義」,那我就是在搜參數,
而且是在最不容易被抓到的地方搜 —— 因為每一次調整聽起來都像
「我只是把它定義得更正確」。舊系統那 1391 次就是這樣長出來的。

所以規矩是:**這份翻譯先寫死、先提交,然後才准跑第一次。**
跑完不管結果好壞,定義一個字都不准改。要改就是新假說、重新計次。

═══ 第二個來源:教學影片(2026-09-25 同日補進來)═══
執政官另外給了一支交易教學影片的內容整理。它講的是下跌段裡怎麼
找底:下跌 → 形成低點 → **獵取低點** → 觀察是否有承接 → 反彈 →
CHOCH → 找做多;上漲則鏡像做空。

跟 IG 那則**大部分重疊**(都是 CHOCH / BOS / 結構),但有一項是新的:

  **獵取低點**(掃流動性)。IG 那則把前一日高低當**止盈目標**;
  影片把「先穿破前低、再收回來」當成**進場的前提**。
  這是不同的東西,所以它是一個新的機械元素 → 見 sweeps()。

影片是**雙向**的(做多與做空並列),所以用它的階梯一律雙向。

⚠️ 影片裡有兩個標記,**這裡沒有實作**:「馬腳做多」與「3+1 做空」。
   原因很簡單:提供的內容整理**沒有給它們的定義**,只說了它們
   出現在圖上的哪個位置。我不知道「3+1」的 3 和 1 各是什麼。
   猜一個出來就是我自己在發明規則,然後掛上別人的名字 ——
   那比不做還糟。要做的話,得先拿到定義。

═══ 翻譯表(2026-09-25 寫死,對照貼文逐項)═══
貼文用語            這裡的機械定義                            參數
──────────────────────────────────────────────────────────────
昨天高點 / 低點     前一個 UTC 日的最高價 / 最低價            0 個
擺動高 / 低         分形:第 i 根的高點嚴格高於前後各 k 根     k=2
                    ⚠️ 第 i 根的擺動要到第 i+k 根才**確認**
結構轉換 CHOCH      收盤價突破「最近一個已確認的反向擺動點」   0 個
                    且該突破讓趨勢狀態翻面(沒翻面的叫 BOS)
看漲訂單塊 OB       造成突破的那一段推進之前,**最後一根陰 K**  0 個
                    區間 = 該根的 (low, high)
失衡區 FVG          三根 K 的定義:bars[i-2].high < bars[i].low 0 個
                    區間 = (bars[i-2].high, bars[i].low)
吞沒陽 K            實體吞沒:c>o 且 c>=前根 o 且 o<=前根 c     0 個
獵取低點(影片)    影線跌破當時最近的已確認擺動低,收盤收回  0 個

k=2 是 Bill Williams 分形的教科書預設值。**這是全套唯一一個參數,
而且不搜** —— 理由跟 hunt.py 一樣:每多搜一個參數就多一個過擬合入口。

═══ 前視偏誤:這套方法最容易出錯的地方 ═══
擺動點是「回頭看」才知道的。第 i 根是不是擺動高,要等第 i+k 根收完
才能確定。任何在第 i 根就使用該擺動點的程式碼,回測會漂亮得不像話,
而且**不會報錯**。所以這裡每個擺動點都帶 `confirmed_at`,
消費端只准使用 `confirmed_at <= 當下` 的擺動點。donchian 的 `_extreme`
當初栽在同一個坑,那次是靠註解擋下來的,這次靠型別。
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from portfolio.sim import Bar

#: 分形的左右根數。教科書預設 2,**不搜**。
FRACTAL_K = 2

#: 獵取事件要在 CHOCH 前幾根之內才算數。這**不是策略參數**,是
#: 防呆上限 —— 沒有上限的話,三百根之前的一次插針也能算成
#: 「這次結構轉換之前先掃過流動性」,那條件就等於沒有。
#: 與 order_block 的 max_lookback 取同一個數,不另外挑。
SWEEP_WINDOW = 30


@dataclass(frozen=True)
class Swing:
    i: int                  # 擺動點所在的 K 棒索引
    price: float
    high: bool              # True=擺動高 / False=擺動低
    confirmed_at: int       # 最早可以知道它存在的索引 = i + k


@dataclass(frozen=True)
class Zone:
    lo: float
    hi: float
    i: int                  # 形成這個區間的 K 棒索引


@dataclass(frozen=True)
class Break:
    """一次結構突破。"""
    i: int                  # 突破的收盤 K 棒
    up: bool                # True=向上突破
    choch: bool             # True=CHOCH(趨勢翻面)/ False=BOS(順勢延續)
    level: float            # 被突破的擺動點價位


def swings(bars: list[Bar], k: int = FRACTAL_K) -> list[Swing]:
    """分形擺動點。

    第 i 根是擺動高 ⟺ 它的 high **嚴格**高於左右各 k 根的 high。
    嚴格不等式是刻意的:平手不算突破,也不算擺動 —— 橫盤時
    用非嚴格會標出一整排擺動點,結構訊號就變成雜訊產生器。
    """
    out: list[Swing] = []
    n = len(bars)
    for i in range(k, n - k):
        win = bars[i - k:i + k + 1]
        h, lo = bars[i].h, bars[i].l
        if all(b.h < h for j, b in enumerate(win) if j != k):
            out.append(Swing(i, h, True, i + k))
        if all(b.l > lo for j, b in enumerate(win) if j != k):
            out.append(Swing(i, lo, False, i + k))
    out.sort(key=lambda s: (s.confirmed_at, s.i))
    return out


def breaks(bars: list[Bar], k: int = FRACTAL_K) -> list[Break]:
    """逐根掃描,產出 CHOCH / BOS 事件。

    狀態機只有三個狀態:up / down / 未定。
      · 收盤突破「最近一個已確認的擺動高」→ 向上突破
      · 收盤跌破「最近一個已確認的擺動低」→ 向下突破
      · 突破方向與當前趨勢相反 = **CHOCH**(結構轉換)
      · 方向相同 = BOS(延續),貼文要的是前者

    ⚠️ 用**收盤價**判斷,不用盤中最高價。插針穿過再收回來不算突破。
    這一條不花任何參數,而它擋掉的假突破最多 —— 與 donchian 同一條理由。
    """
    sw = swings(bars, k)
    out: list[Break] = []
    trend: str | None = None
    hi_ref: Swing | None = None
    lo_ref: Swing | None = None
    p = 0
    for i, b in enumerate(bars):
        # 先吸收「到第 i 根為止已確認」的擺動點
        while p < len(sw) and sw[p].confirmed_at <= i:
            s = sw[p]
            if s.high:
                hi_ref = s
            else:
                lo_ref = s
            p += 1
        if hi_ref and b.c > hi_ref.price:
            out.append(Break(i, True, trend == "down", hi_ref.price))
            trend, hi_ref = "up", None
        elif lo_ref and b.c < lo_ref.price:
            out.append(Break(i, False, trend == "up", lo_ref.price))
            trend, lo_ref = "down", None
    return out


def sweeps(bars: list[Bar], k: int = FRACTAL_K) -> list[int]:
    """「獵取低點 / 獵取高點」—— 影線穿過前低,收盤又收回來。

    ═══ 這一條是 2026-09-25 從執政官傳來的教學影片加的 ═══
    影片講的是下跌段裡怎麼找底:價格跌到某個重要低點附近,先把
    那個低點**穿破**(掃掉停損),然後收回來,接著才看結構改變。

    IG 那則貼文沒有這一段 —— 它把前一日高低當**止盈目標**,
    不是當進場的前提。所以這是一個**新的機械元素**,而不是
    同一件事換句話說。

    ═══ 定義(零新參數)═══
    第 j 根是一次「獵低」⟺
        j 的 **最低價** 跌破當時最近一個**已確認**的擺動低,
        而 j 的 **收盤價** 又收在它上面。

    影線穿過、收盤收回 —— 這就是「掃掉停損之後沒有跟著跌」。
    用收盤與影線的分工,跟 breaks() 是同一條理由,不花任何參數。

    ⚠️ 用的是**當時**已確認的擺動點(confirmed_at <= j),不是事後
    回頭看整段才知道的那個。這條規則如果偷看未來,回測會漂亮得
    不像話,而且不會報錯。

    回傳:發生獵取的 K 棒索引清單。
    """
    sw = swings(bars, k)
    out: list[int] = []
    hi_ref: Swing | None = None
    lo_ref: Swing | None = None
    p = 0
    for j, b in enumerate(bars):
        while p < len(sw) and sw[p].confirmed_at <= j:
            s = sw[p]
            if s.high:
                hi_ref = s
            else:
                lo_ref = s
            p += 1
        if lo_ref and b.l < lo_ref.price <= b.c:
            out.append(j)
        elif hi_ref and b.h > hi_ref.price >= b.c:
            out.append(j)
    return out


def order_block(bars: list[Bar], break_i: int, up: bool,
                max_lookback: int = 30) -> Zone | None:
    """造成突破的推進段之前,最後一根反向 K 棒。

    向上突破 → 找最後一根**陰 K**(close < open);向下突破 → 陽 K。
    `max_lookback` 不是策略參數,是**防呆上限**:找不到就回 None,
    而不是一路倒帶到序列開頭撿一根幾百根前的 K 棒當訂單塊。
    """
    for j in range(break_i, max(-1, break_i - max_lookback), -1):
        b = bars[j]
        if (b.c < b.o) if up else (b.c > b.o):
            return Zone(b.l, b.h, j)
    return None


def fvg(bars: list[Bar], i: int, up: bool) -> Zone | None:
    """第 i 根形成的失衡區(Fair Value Gap),三根 K 的定義。

    看漲:bars[i-2].high < bars[i].low —— 中間那根跳過去了,
    沒有人在那個價格區間成交過。**零參數,純定義。**
    """
    if i < 2:
        return None
    a, c = bars[i - 2], bars[i]
    if up and a.h < c.l:
        return Zone(a.h, c.l, i)
    if not up and c.h < a.l:
        return Zone(c.h, a.l, i)
    return None


def engulfing(bars: list[Bar], i: int, up: bool) -> bool:
    """實體吞沒。用實體(open/close)不用影線 —— 這是通行定義。"""
    if i < 1:
        return False
    a, b = bars[i - 1], bars[i]
    if up:
        return b.c > b.o and b.c >= a.o and b.o <= a.c
    return b.c < b.o and b.c <= a.o and b.o >= a.c


def prev_period_extremes(bars: list[Bar], now: datetime,
                         period: str = "day") -> tuple[float, float] | None:
    """前一個完整週期的 (最高, 最低)。貼文的「昨天高點 / 低點」。

    period: day(前一個 UTC 日)/ week(前一個 ISO 週)。
    **只用已經收完的週期** —— 用「今天到目前為止」的高點當目標,
    是拿未來的資訊,而且它會隨著當天走勢移動。
    """
    if not bars:
        return None
    now = now.astimezone(timezone.utc)
    if period == "week":
        cur = now.isocalendar()[:2]
        key = lambda t: t.astimezone(timezone.utc).isocalendar()[:2]  # noqa: E731
    else:
        cur = now.date()
        key = lambda t: t.astimezone(timezone.utc).date()             # noqa: E731
    prev = [b for b in bars if key(b.t) < cur]
    if not prev:
        return None
    last = key(prev[-1].t)
    sel = [b for b in prev if key(b.t) == last]
    return (max(b.h for b in sel), min(b.l for b in sel))


# ═══════════════════════════════════════════════════════════════
#  貼文那四個步驟,接起來
# ═══════════════════════════════════════════════════════════════
#
# 步驟對照(再寫一次,因為這段是整支最容易偷偷改掉的地方):
#   1  目標   = 前一個完整週期的高 / 低(尚未被碰到的那一邊才算數)
#   2  結構層 = CHOCH + 訂單塊
#   3  進場層 = 回踩訂單塊 + 失衡區
#   4  觸發   = 在失衡區裡出現吞沒 K
#   風控      = 停損:失衡區的另一邊 · 止盈:步驟 1 的目標
#
# 「尚未被碰到的那一邊才算數」是貼文沒明講、但邏輯要求的:
# 目標如果早就被穿過去了,它就不再是流動性目標,而拿一個已經在
# 價格下方的高點當多單止盈,會得到負的風報比。這是我補的一條,
# **而我把它寫在這裡,不是藏在程式裡。**

def _bar_seconds(bars: list[Bar]) -> float:
    if len(bars) < 2:
        return 0.0
    gaps = [(bars[i + 1].t - bars[i].t).total_seconds()
            for i in range(min(20, len(bars) - 1))]
    gaps = [g for g in gaps if g > 0]
    return min(gaps) if gaps else 0.0


def setups(struct: list[Bar], entry: list[Bar], *, symbol: str = "",
           period: str = "day", both_sides: bool = False,
           require_sweep: bool = False, k: int = FRACTAL_K) -> list:
    """把貼文那四步走完,產出可以交給 event_sim 的訊號。

    struct  結構層 K 棒(貼文:15M)
    entry   進場層 K 棒(貼文:5M)
    period  流動性目標的週期(貼文:昨天 = day)
    both_sides  False = 只做多(貼文原文)/ True = 加上鏡像做空
    require_sweep  True = CHOCH 之前必須先有一次獵取(教學影片那一版)

    ⚠️ 兩個時間框的對齊:結構層第 bi 根要**收完**才算數,所以最早
    能動作的進場棒是第一根 `t >= struct[bi].t + 一根結構棒` 的棒。
    不這樣做就是用還沒收完的 K 棒下單 —— 回測會變好看,實單做不到。
    """
    from portfolio.event_sim import Setup

    if len(struct) < 4 * k or len(entry) < 4:
        return []
    sec = _bar_seconds(struct)
    if sec <= 0:
        return []

    def _key(t):
        t = t.astimezone(timezone.utc)
        return t.isocalendar()[:2] if period == "week" else t.date()

    out: list = []
    e_keys = [_key(b.t) for b in entry]
    # 獵取事件只算一次,不是每個 CHOCH 重掃一遍。
    swept = sweeps(struct, k) if require_sweep else []
    for br in breaks(struct, k):
        if not br.choch:
            continue                      # 貼文要的是 CHOCH,不是 BOS
        if not br.up and not both_sides:
            continue
        if require_sweep and not any(br.i - SWEEP_WINDOW < j <= br.i
                                     for j in swept):
            continue                      # 結構轉換之前沒先掃過流動性
        ob = order_block(struct, br.i, br.up)
        if ob is None:
            continue
        ready = struct[br.i].t.timestamp() + sec
        e0 = next((j for j, b in enumerate(entry)
                   if b.t.timestamp() >= ready), None)
        if e0 is None or e0 >= len(entry) - 2:
            continue

        ext = prev_period_extremes(struct, entry[e0].t, period)
        if ext is None:
            continue
        target = ext[0] if br.up else ext[1]
        day = e_keys[e0]
        # 這一段訊號活到本週期結束為止
        expire = e0
        while expire + 1 < len(entry) and e_keys[expire + 1] == day:
            expire += 1

        touched = False
        zone: Zone | None = None
        for j in range(e0, expire + 1):
            b = entry[j]
            if b.l <= ob.hi and b.h >= ob.lo:
                touched = True            # 步驟 3:回踩訂單塊
            z = fvg(entry, j, br.up)
            if z is not None:
                zone = z
            if not (touched and zone):
                continue
            if not (b.l <= zone.hi and b.h >= zone.lo):
                continue                  # 步驟 4 上半:價格回補失衡區
            if not engulfing(entry, j, br.up):
                continue                  # 步驟 4 下半:吞沒 K
            px = b.c
            sl = zone.lo if br.up else zone.hi
            if (px - sl if br.up else sl - px) <= 0:
                break
            if (target - px if br.up else px - target) <= 0:
                break                     # 目標已經被穿過去了,不再是目標
            out.append(Setup(j, br.up, sl, target, expire, symbol,
                             f"CHOCH{'多' if br.up else '空'}+OB+FVG+吞沒"))
            break                         # 一次 CHOCH 只給一次機會
    return out
