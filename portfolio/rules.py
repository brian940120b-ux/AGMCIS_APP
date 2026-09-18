"""
新系統 — 第一批假說(預先登記)· 2026-09-08

═══ 為什麼只有六個,而且參數是教科書值 ═══
舊系統測了 1391 個配置,實際通過 15 案而雜訊預期 52 案 ——
**實際低於雜訊**,意思是搜尋本身在製造假陽性而不是找到優勢。
新系統從第一天就把搜尋空間壓到最小:

  · 六個配置,不是一千個
  · 參數用**教科書標準值**(50/100/200 日均線;20%/30% 回撤),
    不是我掃出來挑的。挑參數就是過擬合的定義。
  · 之後要加新假說,必須先說出「對手是誰、他為什麼被迫付錢」

═══ 這批假說的經濟依據 ═══
不是型態,是**曝險與風險控管**。舊系統最大的可測損失來源是「不交易」
(基準 +34.20% vs 城邦 -2.99%,差 37pp),而它從來沒有測過
「持有」這件事本身。

時序動量(價格在均線之上才持有)是金融學裡被複製最多次的異常之一,
橫跨資產類別與百年資料。它在這裡的意義不是「預測」,
而是**在崩盤時離場** —— 日線資料顯示這批幣期間最大跌幅 70~95%,
沒有風險規則的買入持有,真人拿不住。

═══ 之後不得修改的部分 ═══
參數值、假說數量、以及「新假說必須先回答對手是誰」這條門檻。
"""
from __future__ import annotations


def _sma(idx: dict, sym: str, dates: list, i: int, n: int) -> float | None:
    vals = []
    for j in range(max(0, i - n + 1), i + 1):
        b = idx.get(sym, {}).get(dates[j])
        if b:
            vals.append(b.c)
    return sum(vals) / len(vals) if len(vals) >= n * 0.8 else None


def _forward_exit(inner, outer):
    """把 inner 的出場線接到 outer 上(包裝器用)。"""
    fn = getattr(inner, "exit_level", None)
    if fn is not None:
        outer.exit_level = fn


def exit_level_of(fn, sym: str, dates: list, idx: dict,
                  i: int) -> tuple[float | None, str]:
    """這個策略今天要在**哪個價格**把 sym 放掉。

    ═══ 為什麼不直接讀 plan() 裡的 `mas` ═══
    `mas` 是用 `cfg.vol_lookback` 算的,而 vol_lookback 現在是 50,
    策略也是 50 日均線 —— **兩個 50 只是剛好相等**。
    把 mas 當成出場線,哪天策略換成 100 日均線,出場價會安安靜靜地
    繼續報 50 日均線,而畫面上看不出任何異狀。

    所以出場線一律**跟策略要**,不跟任何剛好相等的東西要。

    ═══ 回傳 ═══
    `(價格, 說明)`。算不出來時價格是 None,而說明講的是**為什麼**
    沒有 —— 「這個策略沒有定義出場價」和「資料不夠算」是兩回事,
    不可以都印成一個空白。
    """
    get = getattr(fn, "exit_level", None)
    if get is None:
        return None, "這個策略沒有逐幣的出場價"
    try:
        price, how = get(sym, dates, idx, i)
    except Exception as e:                           # noqa: BLE001
        return None, f"出場價算不出來:{type(e).__name__}: {e}"
    if price is None:
        return None, f"{how} —— 但資料不夠算出那條線"
    return float(price), how


def hold_all(symbols: list[str]):
    """等權買入持有 —— 這是基準,不是策略。"""
    w = 1.0 / len(symbols)

    def f(i, dates, idx):
        live = [s for s in symbols if idx.get(s, {}).get(dates[i])]
        return {s: 1.0 / len(live) for s in live} if live else {}
    return f


def ma_filter(symbols: list[str], n: int):
    """收盤價在 N 日均線之上才持有,否則該幣空手。

    對手是誰:沒有對手 —— 這不是套利,是曝險管理。
    它賺的是 beta,省的是崩盤。誠實地說,它的價值在最大回撤而不是報酬。
    """
    def f(i, dates, idx):
        on = []
        for s in symbols:
            b = idx.get(s, {}).get(dates[i])
            m = _sma(idx, s, dates, i, n)
            if b and m and b.c > m:
                on.append(s)
        return {s: 1.0 / len(symbols) for s in on} if on else {}

    # 出場線 = 同一條均線。**進場與出場是同一個條件的兩面**,
    # 所以它掛在這裡,而不是別處重算一次 —— 改了 n,兩邊一起改。
    f.exit_level = lambda sym, dates, idx, i: (
        _sma(idx, sym, dates, i, n), f"跌破 {n} 日均線")
    return f


def _extreme(idx: dict, sym: str, dates: list, i: int, n: int,
             high: bool):
    """第 i 日**之前** n 天的最高/最低。

    ⚠️ **不含第 i 日自己。** 含了就是拿今天的高點去判斷今天有沒有
    突破今天的高點 —— 那永遠成立,而回測會漂亮得不像話。
    這是突破類策略最常見的前視偏誤,而它不會報錯。
    """
    if i < n:
        return None
    vals = []
    for j in range(i - n, i):
        b = idx.get(sym, {}).get(dates[j])
        if not b:
            return None                  # 中間有缺口就不算 —— 不補
        vals.append(b.h if high else b.l)
    return (max(vals) if high else min(vals)) if vals else None


def donchian_breakout(symbols: list[str], entry_n: int = 20,
                      exit_n: int = 10, confirm_bars: int = 1):
    """突破 N 日高點進場,跌破 M 日低點出場(Donchian / 海龜)。

    ═══ 這就是「支撐壓力 + 突破」最不任意的寫法 ═══
    2026-09-18 執政官問:「不是有就是說利用支撐或壓力、突破假突破、
    回測去判斷嗎?」

    「支撐壓力」如果讓人用眼睛畫,每個人畫出來的線都不一樣,
    那種東西**回測不了**,因為它不是一條規則,是一個習慣。

    N 日高點 / N 日低點是它的機械版:**過去 N 天沒有人願意用更高的
    價格買** —— 那就是壓力。價格穿過去,代表那群賣方被清掉了。

    ═══ 假突破怎麼濾 ═══
    一、**用收盤價,不用盤中最高價。** 插針穿過去再收回來,不算突破。
        這一條**不花任何參數**,而它擋掉的假突破最多。
    二、`confirm_bars` > 1:連續幾天站穩才算。這一條**要花一個參數**,
        所以預設是 1(不確認)—— 每多一個參數就多一個過擬合的入口,
        而它該由研究迴路帶著多重比較校正去證明值得,不是我先設好。

    ═══ 跟 50 日均線是什麼關係 ═══
    **它們是同一件事的兩種寫法。** 均線是平滑版:價格穿過一條慢慢移動
    的線;Donchian 是離散版:價格穿過一個明確的歷史極值。

    所以這不是「加一個新想法」,是「同一個想法換一種量法」——
    而哪一種量得比較準,**回測說了算,不是誰講得比較有道理**。
    它要走 `portfolio/research.py` 的每一道閘:訓練段挑、驗證段驗、
    回撤契約、多重比較校正。

    ═══ 已知的弱點,先說在前面 ═══
    · 突破類策略在**震盪盤**會被反覆掃 —— 它靠少數幾波大行情回本,
      而那意味著勝率低、單筆虧損頻繁。心理上比均線難拿住。
    · entry_n / exit_n / confirm_bars 是**三個參數**,而 50 日均線
      只有一個,還是教科書值。參數多的一方要贏得更明顯才算數。
    """
    if entry_n < 2 or exit_n < 2 or confirm_bars < 1:
        raise ValueError("entry_n / exit_n 至少 2,confirm_bars 至少 1")

    held: dict = {}
    #: 已經穿過壓力、但還沒站滿 confirm_bars 天的:{幣: (被穿的那條線, 天數)}
    pending: dict = {}

    def f(i, dates, idx):
        # 每次從頭跑的時候狀態要清掉 —— 否則上一次回測的持倉會漏進來,
        # 而那個持倉**沒有任何一筆訊號支持它**。
        if i == 0:
            held.clear()
            pending.clear()

        on = []
        for s in symbols:
            b = idx.get(s, {}).get(dates[i])
            if not b:
                held.pop(s, None)
                pending.pop(s, None)
                continue

            # ── 已持有:看要不要出場 ──────────────────
            if held.get(s):
                floor = _extreme(idx, s, dates, i, exit_n, high=False)
                # 出場也用收盤 —— 兩邊用同一把尺
                if floor is not None and b.c < floor:
                    held[s] = False
                else:
                    on.append(s)
                continue

            # ── 正在確認中:比的是**當初被穿的那條線** ────
            #
            # ⚠️ 這裡是我寫錯兩次的地方。
            #
            # 第一版拿每一天各自的壓力去比 —— 那條件變成「連續 K 天
            #   都創 N 日新高」,罕見得多,不是「站穩」。
            # 第二版改成比「今天的壓力」,更糟:**突破那一天的高點,
            #   隔天就進到視窗裡變成新的壓力**,所以線會自己往上跳,
            #   站穩永遠站不上去 —— 確認功能等於永遠不進場。
            #
            # 正確的是把線**凍住**在突破當下:那條線被穿過去之後
            # 有沒有被收回來,才是「站穩」的意思。
            if s in pending:
                level, days = pending[s]
                if b.c > level:
                    days += 1
                    if days >= confirm_bars:
                        pending.pop(s)
                        held[s] = True
                        on.append(s)
                    else:
                        pending[s] = (level, days)
                else:
                    pending.pop(s)          # 被收回來了,重新等
                continue

            # ── 還沒穿:看今天有沒有穿過去 ────────────
            roof = _extreme(idx, s, dates, i, entry_n, high=True)
            if roof is None or b.c <= roof:
                continue

            if confirm_bars <= 1:
                held[s] = True
                on.append(s)
            else:
                pending[s] = (roof, 1)

        return {s: 1.0 / len(symbols) for s in on} if on else {}

    f.exit_level = lambda sym, dates, idx, i: (
        _extreme(idx, sym, dates, i, exit_n, high=False),
        f"跌破 {exit_n} 日最低")
    return f


def dd_breaker(symbols: list[str], pct: float):
    """買入持有,但自峰值回撤超過 pct% 就全數空手,回到峰值 95% 才回場。

    這是純風險規則,不含任何預測。
    """
    state = {"peak": 0.0, "out": False}

    def f(i, dates, idx):
        live = [s for s in symbols if idx.get(s, {}).get(dates[i])]
        if not live:
            return {}
        basket = sum(idx[s][dates[i]].c for s in live) / len(live)
        state["peak"] = max(state["peak"], basket)
        if state["peak"] <= 0:
            return {}
        dd = (state["peak"] - basket) / state["peak"] * 100
        if dd >= pct:
            state["out"] = True
        elif basket >= state["peak"] * 0.95:
            state["out"] = False
        if state["out"]:
            return {}
        return {s: 1.0 / len(live) for s in live}
    return f


def registry(symbols: list[str]) -> dict:
    """第一批假說。新增任何一項都必須先回答「對手是誰」。"""
    return {
        "買入持有(基準)": hold_all(symbols),
        "50日均線之上才持有": ma_filter(symbols, 50),
        "100日均線之上才持有": ma_filter(symbols, 100),
        "200日均線之上才持有": ma_filter(symbols, 200),
        "回撤20%熔斷": dd_breaker(symbols, 20.0),
        "回撤30%熔斷": dd_breaker(symbols, 30.0),
        # 支撐壓力突破(2026-09-18 加)。兩組都是**教科書值**,
        # 不是搜出來的:海龜系統一 20/10、系統二 55/20。
        # 對手是誰:50 日均線 —— 它們量的是同一件事,
        # 而突破類多兩個參數,所以要贏得更明顯才算數。
        "突破20日高(10日低出場)": donchian_breakout(symbols, 20, 10),
        "突破55日高(20日低出場)": donchian_breakout(symbols, 55, 20),
    }


def scaled(fn, scale: float):
    """把任何規則的部位等比縮小。

    ═══ 這不是新假說,是同一個假說在契約允許的風險水位上 ═══
    訊號、參數、進出時點全部不變,只是持有少一點。
    縮放倍數**由回撤契約決定**(15% ÷ 訓練段實測回撤),
    不是掃出來挑的 —— 而且只用訓練段算,驗證段完全沒有參與。

    加槓桿(scale > 1)是放大器不是優勢,一律禁止。
    """
    s = max(0.0, min(float(scale), 1.0))

    def f(i, dates, idx):
        return {k: v * s for k, v in (fn(i, dates, idx) or {}).items()}

    # 縮放只改部位大小,**不改進出時點** —— 出場線原封不動往外傳。
    # 不轉發的話,包一層就把出場價弄不見了,而外面看起來只是
    # 「這個策略沒有出場價」,不會有人發現是被包掉的。
    _forward_exit(fn, f)
    return f


def vol_target(fn, target_annual_pct: float, lookback: int = 50,
               symbols: list[str] | None = None, leverage_cap: float = 1.0):
    """波動率目標:把規模調到「七幣籃子的估計年化波動 = 目標」。

    ═══ 先講清楚它量的是什麼(2026-09-13 釐清)═══
    它算的是 **`symbols` 這個等權籃子**的已實現波動,而 paper.py 一律
    把完整的七個幣傳進來 —— **不管當天實際持有幾檔**。所以它量的是
    「市場波動」,不是「這個組合的波動」。

    這是刻意的,有兩個後果值得寫下來:

      · **相關性已經在這裡面了。** 籃子的報酬序列本身就含相關性,
        相關性升高 -> 籃子波動升高 -> 係數變小 -> 自動減碼。
        第六十條的相關性門檻因此不是在管同一件事(它管同時被清算)。
      · 它**穩定**:不會出現「持有變少 -> 量到的波動變低 -> 加更多
        槓桿」的回饋圈。代價是持倉集中時實際波動高於目標 ——
        ρ=0.8、七檔剩一檔約高 10%,而 ρ 高正是最需要它準的時候。

    ═══ 為什麼這一步和「單純縮小部位」是兩回事 ═══
    等比縮小不可能改善 Calmar —— 年化與回撤同時線性縮小,比值不變。
    實測:50 日均線縮到 55%,訓練段回撤 15.4% 但驗證段 20.6%,還是不過。

    波動率目標是**隨時間變動**的規模:高波動時少持有、低波動時多持有。
    加密資產的波動在不同時期差 3~5 倍,等權持有等於在某些時期
    承擔了好幾倍的風險 —— 而回撤正是在那些時期發生的。
    把風險拉平,才可能改善比值本身,不只是把曲線壓扁。

    ═══ leverage_cap(2026-09-08 執政官裁定「槓桿不固定 1×,系統依公式浮動」)═══
    這不是新開一條規則,是同一條波動目標公式**對稱延伸**:
    波動高於目標就減碼(原本就有),波動**低於目標就加碼**到 leverage_cap
    為止(原本封頂在 1.0,現在放開)。系統「浮動判斷」的依據還是同一個
    波動估計,不是自由發揮 —— 浮動 = 同一條公式算出來的結果。

    上限本身**不是系統判定的**,是執政官指定的常數,經三輪討論定案為 20.0
    (先問 2.0、後改 35.0、最終 20.0)。理由:交易所依幣種與倉位大小
    分層限制在 20×~150× 之間(未經 API 金鑰驗證的公開資料,不可全信),
    20 是那個範圍裡最保守的一端。

    ═══ 20× 的強平距離,必須寫清楚,不能假裝它不存在 ═══
    強平距離 = 1/槓桿 − 維持保證金率 = 1/20 − 0.005 = 4.5%。
    **只要逆向走 4.5%,那一倉就會被強平**,而加密貨幣單日振幅超過 4.5%
    不罕見。這個上限一旦真的被觸發,帳戶會比 1× 時脆弱得多。

    ═══ 但實測:這個上限現在完全不會被觸發 ═══
    縮放係數 = target_annual_pct ÷ 實際年化波動。3.3 年實測這 7 個幣的
    實際年化波動從未低於約 34%,而 target_annual_pct=27%,
    所以係數從未超過 0.78 —— **0/819 個交易日超過 1.0**,
    遑論到 20。上限現在是虛設的天花板,只有波動率結構性地降到遠低於
    27% 的世界裡才會被啟動。上線這個數字不代表現在真的在用 20× 槓桿,
    誠實地說,它現在的實際效果與封頂在 1.0 完全相同。

    ═══ 沒有新增任何自由參數 ═══
    · lookback 沿用訊號自己的 50 日,不另設一個窗口
    · target 由**回撤契約**決定,只用訓練段算,驗證段不參與
    · leverage_cap 是常數,不是搜出來的
    這是紀律不是客套:每多一個可調的數字,過擬合的空間就大一截。
    """
    syms = symbols or []

    def f(i, dates, idx):
        want = fn(i, dates, idx) or {}
        if not want:
            return {}
        # 等權籃子的已實現波動(用訊號同一個回看窗)
        rets = []
        for j in range(max(1, i - lookback + 1), i + 1):
            legs = []
            for s in (syms or want):
                b0 = idx.get(s, {}).get(dates[j - 1])
                b1 = idx.get(s, {}).get(dates[j])
                if b0 and b1 and b0.c > 0:
                    legs.append((b1.c - b0.c) / b0.c)
            if legs:
                rets.append(sum(legs) / len(legs))
        if len(rets) < lookback * 0.6:
            return want
        m = sum(rets) / len(rets)
        var = sum((r - m) ** 2 for r in rets) / len(rets)
        vol_ann = (var ** 0.5) * (365 ** 0.5) * 100
        if vol_ann <= 1e-9:
            return want
        scale = min(leverage_cap, target_annual_pct / vol_ann)
        return {k: v * scale for k, v in want.items()}

    _forward_exit(fn, f)
    return f


def ma_long_short(symbols: list[str], n: int = 50):
    """站上 N 日均線做多,跌破做空。合約版。

    ═══ 沒有新增任何自由參數 ═══
    同一條均線、同一個回看期。差別只有一個:
    原版跌破就**空手**(曝險 0,什麼都不賺),
    這版跌破就**做空**。

    ═══ 為什麼值得測(結構性理由,不是「勝率比較高」)═══
    一、合約本來就能做空,而現在系統只用了一半
    二、舊帳本 22.9 萬筆交易裡,SHORT 的期望值優於 LONG
        (pullback -0.044 vs -0.186;breakout +0.047 vs -0.089)
    三、而且那個帳本**低估了空單** —— 舊回測引擎的資金費沒有方向符號,
        空單被當成要付,實際上費率為正時空單是收錢的。
        實測背景 +5.69%/年、10/10 個幣為正,等於每年多扣空單 5.69%,
        **而它的表現還是比多單好**。

    做空不是「更聰明」,是把原本閒置的那一半用起來。
    """
    def f(i, dates, idx):
        w = {}
        for s in symbols:
            b = idx.get(s, {}).get(dates[i])
            m = _sma(idx, s, dates, i, n)
            if not b or not m:
                continue
            w[s] = (1.0 if b.c > m else -1.0) / len(symbols)
        return w
    return f


# ══════════════════════════════════════════════════════════
# 指標策略(2026-09-08 預先登記,執政官指定)
# ══════════════════════════════════════════════════════════
# 問題:用 BingX App 上那些指標(MACD/KDJ/RSI/MA/BOLL)幫系統判斷,
#      會不會比現役的「50 日均線 + 波動目標」更好?
#
# ═══ 測試集(在跑之前定死,共 11 個)═══
#   5 個指標 × 2 種用法(單獨當訊號 / 當現役策略的濾網)+ 1 個多數決
# 參數一律用 BingX App 的預設值,一個都不搜。
#
# ═══ 驗收條件(在跑之前定死)═══
# 必須在**訓練段與驗證段都**贏過現役策略的 Calmar,
# 而且全段最大回撤不得高於現役的 17.4%。
# 只贏一段 = 樣本運氣。沒過就蓋棺,不換參數再試 ——
# 換參數再試就是舊系統那 1,391 次。
#
# ═══ 多重檢定脈絡(先寫下來,免得事後自我安慰)═══
# 11 個測試,就算全部沒有優勢,「最好的那一個」也一定會看起來不錯。
# 所以「有一個贏了」不算數 —— 要贏得夠明顯,而且兩段都要贏。


def _series(idx: dict, symbols: list[str], dates: list, i: int, sym: str):
    """取某幣到第 i 日為止的 OHLC 序列(含當日收盤)。"""
    hs, ls, cs = [], [], []
    for j in range(0, i + 1):
        b = idx.get(sym, {}).get(dates[j])
        if b:
            hs.append(b.h); ls.append(b.l); cs.append(b.c)
    return hs, ls, cs


def indicator_rule(symbols: list[str], kind: str, mode: str = "standalone",
                   base_fn=None):
    """指標策略。

    kind: macd / kdj / rsi / ma / boll / vote
    mode: standalone(單獨當訊號)/ filter(當現役策略的濾網)
    """
    from portfolio import indicators as ind

    def _bull(sym, dates, idx, i) -> bool | None:
        hs, ls, cs = _series(idx, symbols, dates, i, sym)
        if len(cs) < 65:
            return None
        if kind == "macd":
            return ind.sig_macd(cs, len(cs) - 1)
        if kind == "kdj":
            return ind.sig_kdj(hs, ls, cs, len(cs) - 1)
        if kind == "rsi":
            return ind.sig_rsi(cs, len(cs) - 1)
        if kind == "ma":
            return ind.sig_ma(cs, len(cs) - 1)
        if kind == "boll":
            return ind.sig_boll(cs, len(cs) - 1)
        if kind == "vote":
            votes = [ind.sig_macd(cs, len(cs) - 1),
                     ind.sig_kdj(hs, ls, cs, len(cs) - 1),
                     ind.sig_rsi(cs, len(cs) - 1),
                     ind.sig_ma(cs, len(cs) - 1),
                     ind.sig_boll(cs, len(cs) - 1)]
            votes = [v for v in votes if v is not None]
            return (sum(votes) > len(votes) / 2) if votes else None
        return None

    def f(i, dates, idx):
        if mode == "filter" and base_fn is not None:
            w = base_fn(i, dates, idx) or {}
            return {s: v for s, v in w.items()
                    if _bull(s, dates, idx, i) is True}
        on = [s for s in symbols if _bull(s, dates, idx, i) is True]
        return {s: 1.0 / len(symbols) for s in on} if on else {}
    return f


def top_half_by_momentum_strength(symbols: list[str], n: int = 50):
    """站上均線的幣裡,只留動能最強的前一半(離均線距離最遠者)。

    ═══ 為什麼是「前一半」不是某個固定名次 ═══
    社群上常見的排序系統會挑「前三名」之類的固定數字,那個數字
    是拍腦袋定的,沒有寫下來的理由,而且容易事後調整讓結果好看。
    「前一半、無條件進位」不需要另外挑一個數字 —— 跟當天有幾個
    幣符合條件無關,永遠是同一條規則,不會因為挑選標的數量而跑掉。

    ═══ 訊號依據,不是新東西 ═══
    離 50 日均線的距離(百分比),已用 7,911 個觀測驗證過有真實
    預測力(五分位單調遞增,極端組 t=3.23)。這裡不是新增訊號,
    是把「持有全部符合條件的幣」改成「持有其中訊號最強的一半」——
    橫截面動量(cross-sectional momentum),與現役的時序動量互補,
    學術上有獨立文獻支持,不是型態學。

    ═══ 沒有新增可調參數 ═══
    分割比例固定 1/2,距離指標沿用已驗證的 50 日均線距離,
    回看窗口 n 沿用訊號本身的 50 日 —— 不搜尋、不挑數字。

    ═══ 驗收條件(在跑之前寫死)═══
    訓練段與驗證段都要贏過現役策略(持有全部符合條件的幣)的 Calmar,
    且全段最大回撤不得高於現役的 17.4%。只贏一段 = 樣本運氣,
    沒過就蓋棺,不換分割比例再試。
    """
    def f(i, dates, idx):
        cand = []
        for s in symbols:
            b = idx.get(s, {}).get(dates[i])
            m = _sma(idx, s, dates, i, n)
            if b and m and b.c > m:
                cand.append((s, (b.c - m) / m))
        if not cand:
            return {}
        cand.sort(key=lambda x: -x[1])
        keep = cand[:max(1, -(-len(cand) // 2))]      # 前一半,無條件進位
        return {s: 1.0 / len(keep) for s, _ in keep}
    return f
