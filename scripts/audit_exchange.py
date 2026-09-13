"""
AGMCIS — 交易所一致性徹查 · 2026-09-09

執政官要求「以後要連接交易所實際派單,所有算法都要一模一樣」。
這一支把每一條資金/開單算式拿去跟 BingX 的規格與官方範例對答案,
任何一項不過就非零退出。

**這支只讀不寫。** 它不改帳本、不下單、不碰任何狀態。

為什麼要獨立成一支而不是只寫測試:測試用的是構造出來的數字,
這支用的是**帳本裡真實的持倉**與**交易所現在回傳的規格**——
兩者都會隨時間變,而規格哪天變了、帳本哪天長出一筆不合規的倉,
要有人當場發現。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

BASE = Path(__file__).resolve().parents[1]
DATA = BASE / "data"

_fails: list[str] = []
_warns: list[str] = []


def chk(name: str, ok: bool, detail: str = "") -> bool:
    mark = "✅" if ok else "❌"
    print(f"  {mark} {name}" + (f":{detail}" if detail else ""))
    if not ok:
        _fails.append(f"{name}:{detail}")
    return ok


def warn(name: str, detail: str) -> None:
    print(f"  ⚠️  {name}:{detail}")
    _warns.append(f"{name}:{detail}")


def near(a: float, b: float, tol: float = 1e-9) -> bool:
    return abs(a - b) <= tol


# ══════════════════════════════════════════════════════════
def section_1_official_examples():
    """一、交易所官方文件的範例,逐條對答案。"""
    print("\n═══ 一、BingX 官方文件範例 ═══")
    from portfolio.account import MAINT_MARGIN_RATE, Position
    from portfolio.costs import TAKER_FEE_PCT

    # 文件範例:0.2 BTC 多單 @27,000,最新價 27,500
    p = Position(symbol="BTC-USDT", position_amt=0.2,
                 avg_price=27_000.0, leverage=10.0)
    mk = 27_500.0
    chk("未實現 = 量×(標記−均價)", near(p.unrealized(mk), 100.0),
        f"{p.unrealized(mk)}(文件寫 100)")
    chk("起始保證金 = 均價÷槓桿×量",
        near(p.initial_margin(), 27_000.0 / 10 * 0.2),
        f"{p.initial_margin()}")
    chk("ROI = (標記−均價)/均價×槓桿×100",
        near(p.roi(mk), (27_500 - 27_000) / 27_000 * 10 * 100),
        f"{p.roi(mk):.6f}%")
    mm = 27_000.0 * 0.2 * MAINT_MARGIN_RATE
    chk("強平價 = 均價 −(起始保證金−維持保證金)/量",
        near(p.liq_price(), 27_000.0 - (p.initial_margin() - mm) / 0.2),
        f"{p.liq_price()}")
    risk = (p.maint_margin(mk) + p.close_fee(mk)) / \
           (p.initial_margin() + p.unrealized(mk))
    chk("風險率 =(維持保證金+平倉費)/(倉位保證金+未實現)",
        near(p.margin_ratio(mk), risk), f"{p.margin_ratio(mk):.8f}")
    chk("平倉手續費用吃單費率",
        near(p.close_fee(mk), p.notional(mk) * TAKER_FEE_PCT / 100),
        f"{p.close_fee(mk):.6f}")

    # 空單對稱
    s = Position(symbol="BTC-USDT", position_amt=-0.2,
                 avg_price=27_000.0, leverage=10.0)
    chk("空單未實現符號相反", near(s.unrealized(mk), -100.0), f"{s.unrealized(mk)}")
    chk("空單強平價在均價之上", s.liq_price() > 27_000.0, f"{s.liq_price()}")
    chk("空單起始保證金與多單相同",
        near(s.initial_margin(), p.initial_margin()), f"{s.initial_margin()}")


def section_2_specs_live():
    """二、交易所規格快取 vs 交易所現在回傳的值。"""
    print("\n═══ 二、合約規格(快取 vs 交易所即時)═══")
    import urllib.request
    from portfolio import specs
    from portfolio.paper import SYMBOLS
    try:
        with urllib.request.urlopen(specs.ENDPOINT, timeout=15) as r:
            live = {c["symbol"]: c
                    for c in json.loads(r.read().decode())["data"]}
    except Exception as e:
        warn("交易所連線", f"抓不到即時規格,略過本節:{e}")
        return
    for s in SYMBOLS:
        c, sp = live.get(s), specs.spec(s)
        if not c:
            chk(f"{s} 交易所仍有此合約", False, "交易所已下架?")
            continue
        same = (int(c["quantityPrecision"]) == sp["quantity_precision"]
                and int(c["pricePrecision"]) == sp["price_precision"]
                and near(float(c["tradeMinQuantity"]), sp["min_qty"])
                and near(float(c["tradeMinUSDT"]), sp["min_notional"])
                and near(float(c["takerFeeRate"]), sp["taker_fee"])
                and near(float(c["makerFeeRate"]), sp["maker_fee"]))
        chk(f"{s} 規格與交易所一致", same,
            "" if same else "快取過期,跑 portfolio/specs.py 更新")


def section_3_ledger_compliance():
    """三、帳本裡每一筆真實持倉,交易所收不收。"""
    print("\n═══ 三、帳本持倉的交易所合規性 ═══")
    from portfolio import specs
    f = DATA / "portfolio_account.json"
    if not f.exists():
        warn("帳本", "尚未記帳,略過本節")
        return
    d = json.loads(f.read_text(encoding="utf-8"))
    pos = d.get("positions") or {}
    if not pos:
        print("  (目前空手)")
        return
    for s, p in sorted(pos.items()):
        sp = specs.spec(s)
        amt = abs(float(p["position_amt"]))
        qp = sp["quantity_precision"]
        chk(f"{s} 數量精度({qp} 位)",
            near(amt, round(amt, qp), 1e-12), f"{amt}")
        chk(f"{s} 不低於最小下單量", amt >= sp["min_qty"],
            f"{amt} ≥ {sp['min_qty']}")
        chk(f"{s} 可交易狀態", specs.tradable(s))


def section_4_identities():
    """四、帳戶恆等式(用帳本裡存的標記價,不重新取價)。"""
    print("\n═══ 四、帳戶恆等式 ═══")
    f = DATA / "portfolio_account.json"
    if not f.exists():
        warn("帳本", "尚未記帳,略過本節")
        return
    d = json.loads(f.read_text(encoding="utf-8"))
    pos = d.get("positions") or {}
    tol = 1e-4

    chk("equity = balance + 未實現",
        near(d["balance"] + d["unrealized_pnl"], d["equity"], tol))
    chk("equity = 已用保證金 + 可用保證金",
        near(d["used_margin"] + d["available_margin"], d["equity"], tol))
    chk("帳戶未實現 = Σ逐倉未實現",
        near(sum(p["unrealized_pnl"] for p in pos.values()),
             d["unrealized_pnl"], tol))
    chk("已用保證金 = Σ逐倉起始保證金",
        near(sum(p["initial_margin"] for p in pos.values()),
             d["used_margin"], tol))
    chk("已實現淨額 = 毛額 − 手續費 − 資金費",
        near(d["realized_pnl"] - d["fee_paid"] - d["funding_paid"],
             d["realized_pnl_net"], tol))
    chk("曝險 = Σ名目 / equity",
        near(sum(p["notional"] for p in pos.values()) / d["equity"],
             d["exposure"], 1e-6) if d["equity"] else True)
    dd = max(0.0, (d["peak_equity"] - d["equity"]) / d["peak_equity"] * 100) \
        if d["peak_equity"] > 0 else 0.0
    chk("回撤 = max(0,(峰值−equity)/峰值)",
        near(dd, d["drawdown_pct"], tol), f"{d['drawdown_pct']:.6f}%")

    for s, p in sorted(pos.items()):
        exp_im = abs(p["position_amt"]) * p["avg_price"] / p["leverage"]
        chk(f"{s} 保證金 = 均價×量÷槓桿",
            near(exp_im, p["initial_margin"], tol))
        exp_u = (p["mark_price"] - p["avg_price"]) * p["position_amt"]
        chk(f"{s} 未實現 = (標記−均價)×量",
            near(exp_u, p["unrealized_pnl"], tol))
        if p["initial_margin"] > 0:
            chk(f"{s} ROI = 未實現 ÷ 起始保證金",
                near(p["unrealized_pnl"] / p["initial_margin"] * 100,
                     p["roi_pct"], 1e-4))


def section_5_no_two_rulers():
    """五、同一個數字有沒有第二份實作(帳本 vs 即時層)。"""
    print("\n═══ 五、帳本層與即時層是否共用同一套公式 ═══")
    from portfolio.account import Account
    f = DATA / "portfolio_account.json"
    if not f.exists():
        warn("帳本", "尚未記帳,略過本節")
        return
    disk = json.loads(f.read_text(encoding="utf-8"))
    marks = {s: float(p["mark_price"]) for s, p in disk["positions"].items()}
    a = Account.load()

    # 即時層在同一組標記價下,必須算出跟帳本完全一樣的數字
    from portfolio.account import Position
    for s, p in a.positions.items():
        mk = marks[s]
        dp = disk["positions"][s]
        chk(f"{s} 即時 vs 帳本:未實現",
            near(p.unrealized(mk), dp["unrealized_pnl"], 1e-4))
        chk(f"{s} 即時 vs 帳本:ROI",
            near(p.roi(mk), dp["roi_pct"], 1e-4))
        chk(f"{s} 即時 vs 帳本:強平價",
            near(p.liq_price(), dp["liq_price"], 1e-6))
    chk("即時 vs 帳本:equity", near(a.equity(marks), disk["equity"], 1e-4))
    chk("即時 vs 帳本:已用保證金",
        near(a.used_margin(marks), disk["used_margin"], 1e-4))


def section_6_order_path():
    """六、開單路徑:產出的每一張單交易所收不收。"""
    print("\n═══ 六、開單路徑 ═══")
    from portfolio import specs
    from portfolio.orders import build_orders
    from portfolio.paper import SYMBOLS
    prices = {"BTC-USDT": 78_000.0, "ETH-USDT": 2_480.0, "SOL-USDT": 103.3,
              "BNB-USDT": 752.0, "XRP-USDT": 1.4165, "AAVE-USDT": 128.7,
              "UNI-USDT": 6.747}
    target = {s: 1.0 / len(SYMBOLS) for s in SYMBOLS}
    os_ = build_orders(target, {}, 10_000.0, prices)
    chk("等權開倉會產生訂單", len(os_) == len(SYMBOLS), f"{len(os_)} 張")
    for o in os_:
        sp = specs.spec(o.symbol)
        chk(f"{o.symbol} 訂單數量精度",
            near(o.qty, round(o.qty, sp["quantity_precision"]), 1e-12),
            f"{o.qty}")
        chk(f"{o.symbol} 訂單價格精度",
            near(o.price, round(o.price, sp["price_precision"]), 1e-12),
            f"{o.price}")
        chk(f"{o.symbol} 名目 = 量×價",
            near(o.notional, o.qty * o.price, 1e-6))
        chk(f"{o.symbol} 不低於交易所最小名目",
            o.notional >= sp["min_notional"])

    # 全平不留殘倉
    held_qty = {o.symbol: o.qty for o in os_}
    held_w = {o.symbol: o.notional / 10_000.0 for o in os_}
    closes = build_orders({}, held_w, 10_000.0, prices, None, held_qty)
    chk("全平倉:每一檔都出單", len(closes) == len(os_), f"{len(closes)} 張")
    for c in closes:
        chk(f"{c.symbol} 全平數量 = 實際持有量",
            near(c.qty, held_qty[c.symbol], 1e-12),
            f"{c.qty} vs 持有 {held_qty[c.symbol]}")


def section_7_execution():
    """七、成交模型:滑點進價格、手續費用交易所費率。"""
    print("\n═══ 七、成交與手續費 ═══")
    from portfolio import execution, orders, specs
    from portfolio.costs import SLIP_FLOOR_PCT, TAKER_FEE_PCT

    o = orders.Order(symbol="ETH-USDT", side="BUY", qty=1.0, price=2000.0,
                     notional=2000.0, reason="audit")
    r = execution.PaperExecutor().submit([o])[0]
    chk("買單成交價高於參考價(滑點對自己不利)", r["price"] > 2000.0,
        f"{r['price']}")
    chk("滑點幅度 = costs.SLIP_FLOOR_PCT",
        near(r["price"], 2000.0 * (1 + SLIP_FLOOR_PCT / 100), 1e-6))
    chk("名目用實際成交價重算", near(r["notional"], 1.0 * r["price"], 1e-9))

    s = orders.Order(symbol="ETH-USDT", side="SELL", qty=1.0, price=2000.0,
                     notional=2000.0, reason="audit")
    rs = execution.PaperExecutor().submit([s])[0]
    chk("賣單成交價低於參考價", rs["price"] < 2000.0, f"{rs['price']}")

    for sym in ("BTC-USDT", "ETH-USDT", "XRP-USDT"):
        chk(f"{sym} 手續費率取自交易所",
            near(specs.taker_fee_pct(sym), TAKER_FEE_PCT, 1e-12),
            f"{specs.taker_fee_pct(sym)}%")
    chk("手續費不得混入滑點",
        not near(TAKER_FEE_PCT, TAKER_FEE_PCT + SLIP_FLOOR_PCT))

    # ── 部分成交管路 ──────────────────────────────────────
    chk("成交回報含 filled_qty", "filled_qty" in r,
        f"{r.get('filled_qty')}")
    chk("紙上全額成交", near(float(r["filled_qty"]), 1.0))

    class _Half(execution.PaperExecutor):
        def _place(self, order):
            d = super()._place(order)
            d["filled_qty"] = abs(order.qty) / 2
            return d

    ph = _Half().submit([orders.Order(
        symbol="ETH-USDT", side="BUY", qty=1.0, price=2000.0,
        notional=2000.0, reason="audit")])[0]
    chk("部分成交標記為 PARTIAL", ph["status"] == "PARTIAL", ph["status"])
    chk("部分成交保留原下單量供對帳", near(float(ph["qty"]), 1.0))

    class _Zero(execution.PaperExecutor):
        def _place(self, order):
            d = super()._place(order)
            d["filled_qty"] = 0.0
            return d

    pz = _Zero().submit([orders.Order(
        symbol="ETH-USDT", side="BUY", qty=1.0, price=2000.0,
        notional=2000.0, reason="audit")])[0]
    chk("零成交標記為 REJECTED(不留幽靈部位)",
        pz["status"] == "REJECTED", pz["status"])


def section_8_funding():
    """八、資金費:逐幣費率、方向、8 小時制。"""
    print("\n═══ 八、資金費 ═══")
    from portfolio.account import Account
    from portfolio.costs import measured_funding_8h_pct
    from portfolio.paper import SYMBOLS

    rates = {s: measured_funding_8h_pct(s) for s in SYMBOLS}
    chk("逐幣費率不是同一個數字", len(set(rates.values())) > 1,
        f"{len(set(rates.values()))} 種不同費率")

    long_ = Account(); long_.fill("X", "BUY", 1.0, 100.0, 0.0, "t")
    short = Account(); short.fill("X", "SELL", 1.0, 100.0, 0.0, "t")
    fl = long_.charge_funding({"X": 100.0}, {"X": 0.0002})
    fs = short.charge_funding({"X": 100.0}, {"X": 0.0002})
    chk("正費率:多單付錢", fl > 0, f"{fl:+.6f}")
    chk("正費率:空單收錢", fs < 0, f"{fs:+.6f}")
    chk("多空金額對稱", near(fl, -fs, 1e-12))
    chk("資金費 = 名目 × 費率", near(fl, 100.0 * 0.0002, 1e-12))

    # ── 用交易所實際結算值,不是估計值 ────────────────────
    import time as _t
    from portfolio import specs
    now_ms = int(_t.time() * 1000)
    day = 24 * 3600 * 1000
    chk("應結算次數符合 8 小時制",
        specs.expected_settlements(now_ms - day, now_ms) == 3,
        f"{specs.expected_settlements(now_ms - day, now_ms)} 次/日")

    # ── 2026-09-09 修:原本檢查「到現在為止有 3 筆」,那是誤報製造機 ──
    # 快取由 daily.py 每天 00:30 更新一次,而交易所在 00/08/16 UTC 結算。
    # 所以每天早上 8 點之後,快取必然少掉最新一筆,這條檢查必然變紅 ——
    # 連續紅 16 小時,而系統其實完全正常。
    # 憲法第八條:「一條天天誤報的檢查等於沒有檢查」——它會讓人習慣忽略紅燈。
    # 正確的問法是:**快取寫入的那一刻,它是完整的嗎?**
    # 用快取自己的 updated 時間當右界,而不是用「現在」。
    cache = json.loads(specs.FUNDING_CACHE.read_text(encoding="utf-8"))
    cache_ms = int(float(cache.get("updated") or 0) * 1000)
    age_h = (now_ms - cache_ms) / 3600000
    chk("資金費快取存在且有時間戳", cache_ms > 0, f"{age_h:.1f} 小時前更新")
    if age_h > 26:
        chk("資金費快取新鮮度(上限 26 小時,對齊每日記帳週期)",
            False, f"{age_h:.1f} 小時未更新")
    for s in SYMBOLS:
        # 以快取寫入時刻為右界:那一刻該有的結算,快取裡必須都有
        st = specs.funding_settlements(s, cache_ms - day, cache_ms)
        want = specs.expected_settlements(cache_ms - day, cache_ms)
        chk(f"{s} 快取寫入時資料完整({want} 筆)", len(st) >= want,
            f"{len(st)}/{want} 筆")
        chk(f"{s} 費率在交易所上下限內(±0.3%)",
            all(abs(r["rate"]) <= 0.003 for r in st))
    # 快取過期必須出聲,不得安靜回 0(那會靜靜少收資金費)
    fut = now_ms + 2 * day
    try:
        specs.funding_rate_sum("BTC-USDT", fut - day, fut)
        chk("快取過期時拒絕記帳", False, "竟然回傳了數字而不是拋例外")
    except specs.SpecMissing:
        chk("快取過期時拒絕記帳(不安靜回 0)", True)


def section_8b_mark_price():
    """八之二、標記價機制:風險數字必須用標記價,不是最新成交價。"""
    print("\n═══ 八之二、標記價機制 ═══")
    from portfolio import live
    from portfolio.paper import SYMBOLS
    mk = live.mark_prices(SYMBOLS)
    chk("能取得交易所標記價", len(mk) == len(SYMBOLS),
        f"{len(mk)}/{len(SYMBOLS)}")
    if not mk:
        return
    last = live.prices(SYMBOLS)
    for s in SYMBOLS:
        if s in mk and s in last and last[s]:
            d = abs(mk[s] - last[s]) / last[s] * 100
            if d > 1.0:
                warn(f"{s} 標記價與最新價差 {d:.3f}%",
                     "行情劇烈或報價異常,值得看一眼")
    snap = live.snapshot()
    chk("快照回報 mark_stale 欄位", "mark_stale" in snap)
    chk("標記價無缺漏", not snap.get("mark_stale"),
        f"{snap.get('mark_stale')}")
    for r in snap.get("positions", []):
        chk(f"{r['symbol']} 標記價為交易所真值(非退回最新價)",
            r.get("mark_is_real") is True)
        chk(f"{r['symbol']} 未實現用標記價算",
            near(r["upnl"],
                 (r["mark_price"] - r["avg_price"]) * r["position_amt"], 1e-6))


def section_9_known_gaps():
    """九、已知、還沒對齊的落差 —— 明列出來,不假裝不存在。"""
    print("\n═══ 九、已知落差(誠實揭露)═══")
    from portfolio.account import MAINT_MARGIN_RATE
    from portfolio.orders import MIN_ORDER_USDT
    from portfolio import specs
    warn("維持保證金率",
         f"我們固定 {MAINT_MARGIN_RATE:.2%},BingX 實際分層"
         "(文件範例出現過 0.4%)。分層表在需金鑰的私有端點,查不到。"
         "取偏高一側=較早示警,方向保守。")
    warn("最小下單金額",
         f"我們自訂 {MIN_ORDER_USDT} USDT,交易所硬下限 "
         f"{specs.min_notional('BTC-USDT')} USDT。"
         "我們的比較嚴,是策略選擇不是錯誤。")
    warn("回測資金費",
         "回測用 costs 的抽樣中位數 —— 交易所的實際結算歷史只回溯 333 天,"
         "而回測跨 3.3 年,拿不到那麼久以前的真實費率。前向記帳已改用"
         "實際結算值。這是資料可得性限制,不是選擇。")
    warn("回測摩擦成本",
         "回測在權重空間運作、不產生個別成交,用合併的 round_trip_pct;"
         "前向已拆成手續費+滑點。經濟效果等價,但結構不同。")
    warn("部分成交",
         "管路已完成(filled_qty/PARTIAL/零成交=REJECTED,帳本記實際成交量)。"
         "紙上一律全額成交,依據是實測掛單簿:我們的單只佔前 20 檔深度的"
         "0.009%~0.057%。真正的成交率要接上交易所才驗得到。")
    warn("實盤下單路徑",
         "LiveExecutor._place 尚未實作。實作時必須回傳交易所的 avgPrice "
         "與 executedQty(不得用下單價/下單量回填),並處理逐幣設定槓桿、"
         "未成交掛單、錯誤碼可否重試。要求已寫在 execution.py 註解。")
    warn("逐幣槓桿上限",
         "BingX 逐幣真實上限在需金鑰的私有端點,未寫入程式碼。"
         "目前上限 20× 為執政官指定常數。")


def main() -> int:
    print("═" * 58)
    print("  AGMCIS 交易所一致性徹查")
    print("═" * 58)
    for fn in (section_1_official_examples, section_2_specs_live,
               section_3_ledger_compliance, section_4_identities,
               section_5_no_two_rulers, section_6_order_path,
               section_7_execution, section_8_funding, section_8b_mark_price,
               section_9_known_gaps):
        try:
            fn()
        except Exception as e:
            chk(fn.__name__, False, f"{type(e).__name__}: {e}")

    print("\n" + "═" * 58)
    if _fails:
        print(f"  ❌ {len(_fails)} 項不通過:")
        for f in _fails:
            print(f"     · {f}")
    else:
        print("  ✅ 全部通過")
    print(f"  ⚠️  {len(_warns)} 項已知落差(見第九節)")
    print("═" * 58)
    return 1 if _fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
