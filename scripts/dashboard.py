"""
AGMCIS — 交易台面板 · 2026-09-08 全新重寫

═══ 為什麼整個重寫,而不是繼續改舊的 ═══
舊面板 414 KB、39 個功能標記,其中絕大多數服務的是已經刪除的系統:
法庭、影子、十積木、獵手榜、神經腦、訊號台。
我先前試著「把舊區塊藏起來」—— 執政官進去一看,舊設計還在。他是對的:
藏起來的東西還在,而一個交易員打開面板時,不該先看到一堆退役的儀表。

所以這一版從零寫。原則只有一條:
**畫面上的每一格,都必須是交易員現在要做決定會用到的東西。**

═══ 三頁,由上而下就是決策順序 ═══
  交易台   帳戶 → 今日訂單 → 持倉 → 權益曲線
  訊號     決策變數(每個幣離均線多遠)+ 策略監控
  系統     實盤資格契約 + 服務健康 + 對照組

═══ 不做的事 ═══
· 不在網頁請求裡跑回測、抓 K 線、算指標 —— 全部讀已落地的檔案
· 不顯示任何無法從檔案佐證的數字
· 不美化虧損,也不隱藏未過的契約
"""
from __future__ import annotations

import csv
import html
import json
import os
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from core import ratelimit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import interpreter

# 用錯直譯器的時候講人話,而不是丟一個 ModuleNotFoundError 讓人猜。
interpreter.require()

BASE = Path(__file__).resolve().parents[1]
DATA = BASE / "data"
HIST = DATA / "history"
PORT = int(os.environ.get("DASHBOARD_PORT", "8765"))


# ══════════════════════════════════════════════════════════
# 讀檔:全部容錯,讀不到就說讀不到,絕不編數字
# ══════════════════════════════════════════════════════════
def _env(key: str, default: str = "") -> str:
    """
    設定值:環境變數優先,再看 .env。

    原本這裡**只讀 .env,完全不看環境變數** —— 意思是 systemd unit 裡
    寫 `Environment=DASHBOARD_KEY=...` 對面板毫無作用,而寫的人不會
    收到任何提示。DASHBOARD_KEY 決定面板要不要驗證,一個「設了卻沒
    生效」的存取控制,比明白地沒有存取控制更糟。

    `core/config.load_env()` 的註解寫的是「已存在的環境變數不覆蓋
    —— 系統層設定優先」。這裡跟它對齊。
    """
    from_env = os.environ.get(key)
    if from_env:
        return from_env.strip()
    try:
        for line in (BASE / ".env").read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{key}=") and not line.startswith("#"):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return default


def _json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default if default is not None else {}


def _jsonl(path: Path, limit: int = 0) -> list[dict]:
    out: list[dict] = []
    try:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except OSError:
        return []
    return out[-limit:] if limit else out


_CACHE: dict[str, tuple[float, object]] = {}


def _cached(key: str, ttl: float, fn):
    import time
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    val = fn()
    _CACHE[key] = (time.time(), val)
    return val


def klines(sym: str, interval: str = "15m", limit: int = 96) -> list[dict]:
    """持倉列要看的即時 K 線。走 BingX 公開端點,不需要金鑰。

    ═══ 這條路只餵眼睛,不餵決策 ═══
    跟 live.py 同一個原則:面板要看到現在的走勢是人的需求,
    但這些 K 棒不進 rules/orders/account 任何一層,也不落地成檔案。
    決策仍然只用收盤日線(訊號)+ 隔日開盤(成交)。

    快取 20 秒:15m K 棒本來就 15 分鐘才換一根,再密集打交易所沒有意義。
    """
    def _fetch():
        import urllib.request
        url = ("https://open-api.bingx.com/openApi/swap/v3/quote/klines"
               f"?symbol={sym}&interval={interval}&limit={int(limit)}")
        try:
            with ratelimit.urlopen(url, timeout=6) as r:
                d = json.loads(r.read().decode("utf-8"))
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}"}
        if str(d.get("code")) != "0":
            return {"error": f"BingX code={d.get('code')} {d.get('msg')}"}
        rows = []
        for k in (d.get("data") or []):
            try:
                rows.append({"t": int(k["time"]), "o": float(k["open"]),
                             "h": float(k["high"]), "l": float(k["low"]),
                             "c": float(k["close"])})
            except (KeyError, TypeError, ValueError):
                continue
        rows.sort(key=lambda x: x["t"])       # 交易所回的是新→舊,轉成舊→新
        return {"bars": rows}
    return _cached(f"kl:{sym}:{interval}:{limit}", 20, _fetch)


def closes(sym: str, n: int = 60) -> list[float]:
    def _read():
        out = []
        try:
            with (HIST / f"{sym}_1d.csv").open(encoding="utf-8") as fh:
                for r in csv.DictReader(fh):
                    try:
                        out.append(float(r["close"]))
                    except (KeyError, ValueError):
                        pass
        except OSError:
            return []
        return out
    return _cached(f"c:{sym}", 120, _read)[-n:]


def age_min(path: Path) -> float | None:
    import time
    try:
        return (time.time() - path.stat().st_mtime) / 60
    except OSError:
        return None


# ══════════════════════════════════════════════════════════
# 樣式(2026-09-09 重新設計)
#
# 方向:不是通用的深色 SaaS 面板,是一個「紙上」交易終端。
# 琥珀色是唯一的強調色,而且不是裝飾——它借用交易終端的傳統配色
# (琥珀/黑,早期報價機的配色),同時每次看到它都在提醒:
# 這是模擬帳戶,不是真錢。漲跌用綠/紅(不可觸碰的慣例),
# 介面本身(頁籤、徽章、即時光點)一律用琥珀,兩套顏色語意不混用。
#
# 數字一律等寬對齊,卡片有分層陰影做出實體感,分頁改成底線式
# 而不是填色藥丸——更像儀表板上的頁籤,不是通用 App 導覽。
# ══════════════════════════════════════════════════════════
CSS = """
*{box-sizing:border-box;margin:0;padding:0}
:root{
 --bg:#08090d;--bg2:#0c0e14;--card:#11141b;--el:#171b23;--el2:#1c212b;
 --line:#232833;--line2:#2c323f;
 --ink:#eceff4;--ink2:#c7cdda;--dim:#7c8494;--dim2:#565f70;
 --up:#33d19d;--up-bg:rgba(51,209,157,.12);--up-bd:rgba(51,209,157,.3);
 --down:#f0654f;--down-bg:rgba(240,101,79,.12);--down-bd:rgba(240,101,79,.3);
 --amb:#e0a339;--amb-bg:rgba(224,163,57,.12);--amb-bd:rgba(224,163,57,.32);
 --mono:'SF Mono',ui-monospace,Menlo,Consolas,monospace;
 --sans:-apple-system,BlinkMacSystemFont,'PingFang TC','Noto Sans TC',sans-serif;
}
html{background:var(--bg)}
body{background:
 radial-gradient(1200px 420px at 50% -180px,rgba(224,163,57,.05),transparent 60%),
 var(--bg);
 color:var(--ink);font:14px/1.55 var(--sans);
 -webkit-text-size-adjust:100%;padding-bottom:env(safe-area-inset-bottom)}
a{color:inherit;text-decoration:none}
::selection{background:var(--amb-bg);color:var(--ink)}
.wrap{max-width:960px;margin:0 auto;padding:14px 12px 44px}

header{display:flex;align-items:center;gap:10px;padding:8px 2px 16px;
 flex-wrap:wrap}
header h1{font:800 16px/1 var(--sans);letter-spacing:.16em;color:var(--ink)}
header .tag{font:600 10.5px/1 var(--mono);color:var(--dim);padding:5px 9px;
 border:1px solid var(--line);border-radius:6px;background:var(--el);
 letter-spacing:.03em}
header .paper{color:var(--amb);border-color:var(--amb-bd);background:var(--amb-bg)}

nav{display:flex;gap:4px;margin:0 0 16px;overflow-x:auto;
 -webkit-overflow-scrolling:touch;border-bottom:1px solid var(--line)}
nav a{flex:0 0 auto;font:600 12.5px/1 var(--sans);padding:11px 15px 10px;
 color:var(--dim);border-bottom:2px solid transparent;white-space:nowrap;
 letter-spacing:.01em;margin-bottom:-1px;transition:color .15s}
nav a.on{color:var(--ink);border-bottom-color:var(--amb)}

.card{background:linear-gradient(180deg,var(--card),var(--bg2));
 border:1px solid var(--line);border-radius:14px;padding:16px;
 margin-bottom:12px;
 box-shadow:0 1px 0 rgba(255,255,255,.025) inset,
            0 10px 24px -14px rgba(0,0,0,.6)}
.card h2{font:700 11px/1 var(--sans);letter-spacing:.13em;color:var(--dim);
 margin-bottom:13px;text-transform:uppercase;display:flex;align-items:center}

.note{font-size:12px;color:var(--dim);line-height:1.75;margin-top:10px}
.note b{color:var(--ink2);font-weight:600}

.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(96px,1fr));
 gap:8px}
.kv{background:var(--el);border:1px solid var(--line);border-radius:9px;
 padding:10px 11px;transition:border-color .15s}
.kv .l{font:500 10px/1 var(--sans);color:var(--dim2);margin-bottom:6px;
 white-space:nowrap;letter-spacing:.04em;text-transform:uppercase}
.kv .v{font:650 16px/1.2 var(--mono);font-variant-numeric:tabular-nums}

.big{font:700 32px/1.08 var(--mono);font-variant-numeric:tabular-nums;
 letter-spacing:-.01em;margin:2px 0 5px}
.sub{font:600 12.5px/1.4 var(--mono);color:var(--dim);margin-bottom:14px}
.up{color:var(--up)}.down{color:var(--down)}.dim{color:var(--dim)}.amb{color:var(--amb)}

table{width:100%;border-collapse:collapse;font:12.5px/1.5 var(--mono);
 font-variant-numeric:tabular-nums}
th{text-align:left;font:600 10px/1 var(--sans);color:var(--dim2);
 padding:0 8px 9px;letter-spacing:.05em;text-transform:uppercase;
 white-space:nowrap}
td{padding:11px 8px;border-top:1px solid var(--line);white-space:nowrap;
 vertical-align:top}
tbody tr:first-child td{border-top:none}
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch;margin:0 -4px;
 padding:0 4px}

.sym{font-weight:700;letter-spacing:.02em;color:var(--ink)}
.pill{display:inline-block;padding:3px 9px;border-radius:6px;
 font:700 10px/1.4 var(--sans);letter-spacing:.02em}
.p-buy{background:var(--up-bg);color:var(--up);border:1px solid var(--up-bd)}
.p-sell{background:var(--down-bg);color:var(--down);border:1px solid var(--down-bd)}
.p-hold{background:var(--amb-bg);color:var(--amb);border:1px solid var(--amb-bd)}
.p-off{background:var(--el);color:var(--dim);border:1px solid var(--line)}

.why{font-size:11px;color:var(--dim);white-space:normal;line-height:1.5;
 margin-top:3px}
.bar{height:4px;border-radius:3px;background:var(--el2);overflow:hidden;
 margin-top:6px}
.bar i{display:block;height:100%;border-radius:3px}

/* 持倉列展開的 K 線。預設收起 —— 面板首屏該先講帳戶狀態,
   圖是「想看的時候才看」,不該把數字擠到螢幕外。 */
.prow{cursor:pointer}
.prow .chev{color:var(--amb);font-size:10px;margin-left:4px;
 white-space:nowrap}
.prow.open .chev{color:var(--ink)}
.krow{display:none}
.krow.on{display:table-row}
.krow td{padding:0 4px 12px}
.kwrap{background:var(--el);border:1px solid var(--line);border-radius:10px;
 padding:10px}
.kbar{display:flex;gap:5px;align-items:center;flex-wrap:wrap;margin-bottom:8px}
.kiv{font:11px var(--mono);color:var(--dim);background:var(--el2);
 border:1px solid var(--line);border-radius:6px;padding:3px 9px;
 cursor:pointer}
.kiv.on{color:var(--ink);border-color:var(--amb-bd);background:var(--amb-bg)}
.kinfo{font:11px var(--mono);color:var(--dim);margin-left:auto}
.kchart{min-height:150px;display:flex;align-items:center;
 justify-content:center}
.kchart svg{width:100%;height:auto;display:block}

.flag{border-radius:10px;padding:11px 13px;font-size:12px;line-height:1.75;
 margin-top:10px;border:1px solid var(--line);background:var(--el)}
.flag.warn{border-color:var(--down-bd);background:var(--down-bg)}
.flag.ok{border-color:var(--up-bd);background:var(--up-bg)}
.flag b{font-weight:700;color:var(--ink)}

code{font:11.5px var(--mono);background:var(--el2);padding:2px 6px;
 border-radius:5px;border:1px solid var(--line)}

.live-dot{display:inline-block;width:7px;height:7px;border-radius:50%;
 background:var(--up);margin:0 6px 0 5px;vertical-align:middle;
 box-shadow:0 0 0 3px var(--up-bg);animation:pulse 2s ease-in-out infinite}
.live-dot.off{background:var(--down);box-shadow:0 0 0 3px var(--down-bg);
 animation:none}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.35}}
.live-t{font:600 10px/1 var(--mono);color:var(--dim);letter-spacing:.04em}

.flash{animation:fl .6s ease-out}
@keyframes fl{0%{background:var(--amb-bg)}100%{background:transparent}}

.tabpane{display:none}.tabpane.on{display:block;
 animation:rise .25s ease-out}
@keyframes rise{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:none}}

footer{color:var(--dim2);font-size:10.5px;text-align:center;padding:20px 0 6px;
 letter-spacing:.03em}
"""


def kv(label: str, value: str, cls: str = "") -> str:
    return (f'<div class="kv"><div class="l">{html.escape(label)}</div>'
            f'<div class="v {cls}">{value}</div></div>')


def tone(v: float) -> str:
    return "up" if v > 0 else ("down" if v < 0 else "")


# ══════════════════════════════════════════════════════════
# 區塊
# ══════════════════════════════════════════════════════════
def block_account() -> str:
    """帳戶。數字帶 id,由 /api/live 每 10 秒就地更新(不重載整頁)。

    ═══ 記帳權益 vs 即時權益 ═══
    記帳是每日一次(訊號用收盤、成交在隔日開盤)—— 那是策略的節奏。
    即時是每 10 秒 —— 那只給人看,**不進任何決策、不寫任何帳本**。
    讓即時價格進到決策裡,策略就從日線變成盯盤,
    而回測的 Calmar 1.33 是在日線規則下算出來的。
    """
    a = _json(DATA / "portfolio_account.json")
    if not a:
        return ('<div class="card"><h2>帳戶</h2><p class="note">'
                '尚未記帳 —— 每日 00:30 UTC 自動執行。</p></div>')
    eq = float(a.get("equity", 0) or 0)
    ret = float(a.get("return_pct", 0) or 0)
    # BingX 的「已實現盈虧」是**扣完手續費與資金費的淨額**
    # (原文:Realized PnL = 已平倉損益 − 交易手續費 − 資金費)。
    # 面板顯示淨額以與交易所一致;毛額與各項成本在下方註解列出。
    rp_gross = float(a.get("realized_pnl", 0) or 0)
    rp = float(a.get("realized_pnl_net", rp_gross) or 0)
    up = float(a.get("unrealized_pnl", 0) or 0)
    dd = float(a.get("drawdown_pct", 0) or 0)
    cells = [
        f'<div class="kv"><div class="l">可用保證金</div>'
        f'<div class="v" id="k-cash">'
        f'{float(a.get("available_margin", a.get("cash", 0))):,.0f}</div></div>',
        f'<div class="kv"><div class="l">已用保證金</div>'
        f'<div class="v" id="k-um">{float(a.get("used_margin", 0)):,.0f}</div>'
        '</div>',
        f'<div class="kv"><div class="l">已實現</div>'
        f'<div class="v {tone(rp)}" id="k-rp">{rp:+,.2f}</div></div>',
        f'<div class="kv"><div class="l">未實現</div>'
        f'<div class="v {tone(up)}" id="k-up">{up:+,.2f}</div></div>',
        f'<div class="kv"><div class="l">曝險</div>'
        f'<div class="v" id="k-ex">{float(a.get("exposure", 0)):.0%}</div></div>',
        f'<div class="kv"><div class="l">回撤</div>'
        f'<div class="v {"up" if dd <= 15 else "down"}" id="k-dd">'
        f'{dd:.2f}%</div></div>',
        kv("成交", f"{a.get('orders_filled', 0)} 單"),
        kv("記帳", f"{a.get('days', 0)} 天"),
        kv("累計成本",
           f"{float(a.get('fee_paid', 0)) + float(a.get('funding_paid', 0)):,.2f}"),
    ]
    return ('<div class="card"><h2>帳戶 '
            '<span class="live-dot" id="live-dot"></span>'
            '<span class="live-t" id="live-t">即時</span></h2>'
            f'<div class="big {tone(ret)}" id="k-eq">{eq:,.2f}'
            '<span style="font-size:14px;color:var(--dim)"> USDT</span></div>'
            f'<div class="sub {tone(ret)}" id="k-ret">{ret:+.2f}% · 起始 '
            f'{float(a.get("start_equity", 10000)):,.0f}</div>'
            f'<div class="grid">{"".join(cells)}</div>'
            f'<p class="note">策略 <b>{html.escape(str(a.get("strategy", "—")))}'
            # 2026-09-10 改寫:原本寫「槓桿上限 20×(實際使用約 0.5×)」——
            # 那個括號讓人以為安全,但 0.5× 是**帳戶曝險**,而每一倉的槓桿
            # 是 20×、強平距離只有 4.5%。09-10 UNI 就是這樣被強平的,
            # 而查下去六個倉全在懸崖邊(BNB 只剩 0.14%)。
            # 面板必須直接顯示**逐倉強平距離**,那才是會殺死部位的數字。
            f'</b> · 逐倉槓桿 <b>{float(a.get("leverage", 1)):.0f}×</b>'
            f'(強平距離 {(1 / max(float(a.get("leverage", 1)), 1e-9) - float(a.get("maint_margin_rate", 0.005))) * 100:.1f}%'
            f' —— 單幣逆向走這麼多,那一倉就歸零)· '
            f'帳戶曝險 {float(a.get("exposure", 0)):.0%}(由波動公式決定,'
            f'與強平距離無關)· 逐倉 · '
            f'維持保證金率 {float(a.get("maint_margin_rate", 0.005)):.2%}<br>'
            f'錢包餘額 {float(a.get("balance", 0)):,.2f} · 手續費 '
            f'{float(a.get("fee_paid", 0)):,.2f} · 資金費 '
            f'{float(a.get("funding_paid", 0)):,.2f} USDT<br>'
            f'「已實現」為交易所定義的<b>淨額</b>(平倉損益 '
            f'{rp_gross:+,.2f} − 手續費 − 資金費)· 資金費逐幣各收自己的'
            f'費率,每 8 小時結算一次(00/08/16 UTC)<br>'
            '<b>即時盈虧每 10 秒更新</b>,只給人看 —— '
            '記帳仍是每日一次(訊號用收盤、成交在隔日開盤),'
            '即時價格不進任何決策。</p></div>')


def block_orders() -> str:
    def _plan():
        try:
            from portfolio.paper import plan
            return plan()
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}"}
    p = _cached("plan", 180, _plan)
    if "error" in p:
        return ('<div class="card"><h2>今日訂單</h2>'
                f'<p class="note">尚未就緒:{html.escape(p["error"])}</p></div>')
    rows = []
    for o in p.get("orders", []):
        buy = o.side == "BUY"
        stop = (f'{o.stop:,.6g}<div class="why">距 {o.stop_pct:.1f}%</div>'
                if o.stop else '<span class="dim">—</span>')
        rows.append(
            f'<tr><td class="sym">{html.escape(o.symbol.replace("-USDT", ""))}'
            f'</td><td><span class="pill {"p-buy" if buy else "p-sell"}">'
            f'{"買入" if buy else "賣出"}</span></td>'
            f'<td>{o.qty:.6g}</td><td>{o.price:,.6g}</td>'
            f'<td>{o.notional:,.0f}</td><td>{stop}</td>'
            f'<td><div class="why">{html.escape(o.reason)}</div></td></tr>')
    body = ('<div class="scroll"><table><thead><tr><th>商品</th><th>方向</th>'
            '<th>數量</th><th>價格</th><th>名目</th><th>出場線</th>'
            '<th>原因</th></tr></thead><tbody>' + "".join(rows)
            + '</tbody></table></div>') if rows else (
        '<p class="note">今日無新單 —— 目標配置與現有持倉一致。'
        '這條策略本來就不常動(回測 3.3 年約每月換手一次),'
        '換手少正是它摩擦低的原因。</p>')
    done = "已執行" if p.get("already_done") else "待執行"
    return ('<div class="card"><h2>今日訂單</h2>'
            f'<p class="note">訊號日 {p["signal_day"][:10]} 收盤 → 成交日 '
            f'{p["exec_day"][:10]} 開盤 · <b>{done}</b>。'
            '出場線 = 該幣的 50 日均線,也就是策略本身的出場規則 —— '
            '<b>不是另外挑的百分比</b>,它每天跟著均線移動。</p>'
            + body + '</div>')


def block_positions() -> str:
    """持倉。欄位對齊 BingX 合約:持倉量(帶正負)、均價、標記價、
    未實現、**強平價**、保證金率。

    強平價是合約交易員第一個要看的風險數字,而現貨式記法顯示不出來。
    """
    a = _json(DATA / "portfolio_account.json")
    pos = a.get("positions") or {}
    if not pos:
        return '<div class="card"><h2>持倉</h2><p class="note">空手。</p></div>'
    # 2026-09-09 修:這裡曾經自己拿 closes()[-1](當天還沒收盤的那根
    # K 棒的最新成交價,被 load_or_download 累積式快取進 CSV 最後一列)
    # 當「標記價」重算未實現,跟帳本 mark_price(tick() 用的是最後一根
    # 已收盤的日線,見 paper.py 的 dates[len(dates)-2])兩把尺,數字會
    # 對不上——執政官在面板上看到「起始/未實現」跟別處不一致,就是這裡。
    # 改成直接讀帳本自己算好的欄位,不再有第二份實作;頁面載入後
    # /api/live 的即時串流價格還是會覆蓋這裡,不受影響。
    rows, tot = [], 0.0
    for sym, p in sorted(pos.items()):
        amt = float(p.get("position_amt") or p.get("qty") or 0)
        entry = float(p.get("avg_price") or p.get("entry") or 0)
        px = float(p.get("mark_price") or entry)
        u = float(p.get("unrealized_pnl", (px - entry) * amt))
        tot += u
        pct = ((px / entry - 1) * 100 * (1 if amt > 0 else -1)) if entry else 0
        lp = p.get("liq_price")
        liq = (f'{lp:,.6g}<div class="why">距 '
               f'{abs(px - lp) / px * 100:.1f}%</div>'
               if (lp and px) else '<span class="dim">—</span>')
        mr = float(p.get("margin_ratio") or 0) * 100
        # ROI 由帳本算好(Position.roi(),跟即時層同一個函式),不在這裡重算
        roi = float(p.get("roi_pct") or 0)
        im = float(p.get("initial_margin") or 0)
        side = "多" if amt > 0 else "空"
        sid = sym.replace("-USDT", "")
        rows.append(
            f'<tr class="prow" data-sym="{html.escape(sym)}" '
            f'data-sid="{html.escape(sid)}" data-entry="{entry}" '
            f'data-liq="{lp or 0}">'
            f'<td class="sym">{html.escape(sid)}'
            f'<div class="why">{side} · {float(p.get("leverage", 1)):.0f}× '
            '<span class="chev">▸ K線</span></div>'
            f'</td><td>{abs(amt):.6g}</td><td>{entry:,.6g}</td>'
            f'<td id="p-px-{sid}">{px:,.6g}'
            f'<div class="why" id="p-last-{sid}"></div></td>'
            f'<td id="p-val-{sid}">{abs(amt) * px:,.0f}</td>'
            f'<td id="p-im-{sid}">{im:,.2f}</td>'
            f'<td class="{tone(u)}" id="p-pnl-{sid}">{u:+,.2f}'
            f'<div class="why {tone(u)}">{pct:+.2f}%</div></td>'
            f'<td class="{tone(roi)}" id="p-roi-{sid}"><b>{roi:+.2f}%</b></td>'
            f'<td>{liq}</td>'
            f'<td class="{"up" if mr < 50 else "down"}">{mr:.2f}%</td></tr>'
            f'<tr class="krow" id="k-row-{sid}"><td colspan="10">'
            f'<div class="kwrap"><div class="kbar" data-sid="{sid}">'
            + "".join(
                f'<button class="kiv{" on" if iv == "15m" else ""}" '
                f'data-sid="{sid}" data-iv="{iv}">{iv}</button>'
                for iv in ("5m", "15m", "1h", "4h", "1d"))
            + f'<span class="kinfo" id="k-info-{sid}"></span></div>'
            f'<div id="k-chart-{sid}" class="kchart">'
            '<span class="dim">載入中…</span></div></div></td></tr>')
    return ('<div class="card"><h2>持倉 '
            '<span class="live-dot"></span><span class="live-t">即時</span>'
            '</h2>'
            '<div class="scroll"><table><thead><tr><th>商品</th><th>持倉量</th>'
            '<th>開倉均價</th><th>標記價</th><th>名目</th><th>保證金</th>'
            '<th>未實現</th><th>ROI</th><th>強平價</th><th>保證金率</th>'
            '</tr></thead><tbody>' + "".join(rows) + '</tbody></table></div>'
            f'<p class="note">未實現合計 <b class="{tone(tot)}">{tot:+,.2f} '
            'USDT</b> · 保證金率 100% 即強平。'
            '出場線是 50 日均線(策略規則),跌破隔日開盤平倉 —— '
            '那比強平早得多。</p></div>')


def block_curve() -> str:
    rows = _jsonl(DATA / "portfolio_equity.jsonl")
    if len(rows) < 2:
        return ('<div class="card"><h2>權益曲線</h2><p class="note">'
                f'記帳 {len(rows)} 天 —— 至少兩天才畫得出線。</p></div>')
    eq = [float(r.get("return_pct") or 0) for r in rows]
    bm = [float(r.get("benchmark_pct") or 0) for r in rows]
    lo, hi = min(min(eq), min(bm)), max(max(eq), max(bm))
    if hi - lo < 1e-9:
        lo, hi = lo - 1, hi + 1
    W, H, P = 640, 190, 10
    line_col = "#33d19d" if eq[-1] >= 0 else "#f0654f"

    def xy(v, i):
        x = P + (W - 2 * P) * (i / max(len(v) - 1, 1))
        y = H - P - (H - 2 * P) * ((v[i] - lo) / (hi - lo))
        return x, y

    def path(v):
        pts = [f"{x:.1f},{y:.1f}" for i in range(len(v))
               for x, y in [xy(v, i)]]
        return "M" + " L".join(pts)

    def area(v):
        pts = [f"{x:.1f},{y:.1f}" for i in range(len(v))
               for x, y in [xy(v, i)]]
        x0, _ = xy(v, 0)
        xn, _ = xy(v, len(v) - 1)
        return f"M{x0:.1f},{H - P} L" + " L".join(pts) + f" L{xn:.1f},{H - P} Z"

    zy = H - P - (H - 2 * P) * ((0 - lo) / (hi - lo))
    gid = "eqfill"
    return ('<div class="card"><h2>權益曲線</h2>'
            f'<svg viewBox="0 0 {W} {H}" style="width:100%;height:auto" '
            'preserveAspectRatio="none">'
            f'<defs><linearGradient id="{gid}" x1="0" y1="0" x2="0" y2="1">'
            f'<stop offset="0%" stop-color="{line_col}" stop-opacity=".22"/>'
            f'<stop offset="100%" stop-color="{line_col}" stop-opacity="0"/>'
            '</linearGradient></defs>'
            f'<line x1="{P}" y1="{zy:.1f}" x2="{W - P}" y2="{zy:.1f}" '
            'stroke="#232833" stroke-dasharray="3 4"/>'
            f'<path d="{area(eq)}" fill="url(#{gid})" stroke="none"/>'
            f'<path d="{path(bm)}" fill="none" stroke="#565f70" '
            'stroke-width="1.4" stroke-linejoin="round"/>'
            f'<path d="{path(eq)}" fill="none" stroke="{line_col}" '
            'stroke-width="2.4" stroke-linejoin="round" stroke-linecap="round"/>'
            '</svg>'
            f'<p class="note"><b class="{tone(eq[-1])}">▬</b> 組合 '
            f'{eq[-1]:+.2f}% ｜ <b class="dim">▬</b> 基準 {bm[-1]:+.2f}%'
            f'(同日開始、等權買入持有,也是永續、也付資金費)｜ '
            f'{len(rows)} 天</p></div>')


def block_signals() -> str:
    try:
        from portfolio.paper import SYMBOLS, VOL_LOOKBACK
    except Exception:
        return ''
    a = _json(DATA / "portfolio_account.json")
    pos = a.get("positions") or {}
    rows = []
    for sym in SYMBOLS:
        c = closes(sym, 80)
        if len(c) < VOL_LOOKBACK + 1:
            continue
        # 2026-09-09 修:c[-1] 是今天還沒收完的那根 K 棒(累積式快取,
        # 最新一筆會隨當天成交一直變動)。拿它當「收盤」、還混進 50 日
        # 均線去算,跟 paper.py 真正的訊號(只用 dates[len(dates)-2],
        # 見 plan() 的「訊號用最後一根完整日的收盤」)是兩把不同的尺,
        # 均線數字會跟實際策略算出來的不一樣。改成跟訊號同一套:
        # 只用最後一根**已收盤**的日線。
        px, ma = c[-2], sum(c[-(VOL_LOOKBACK + 1):-1]) / VOL_LOOKBACK
        rows.append((sym, px, ma, (px - ma) / ma * 100, sym in pos))
    rows.sort(key=lambda x: -x[3])
    out = []
    for sym, px, ma, gap, held in rows:
        col = "#33d19d" if gap >= 0 else "#f0654f"
        out.append(
            f'<tr><td class="sym">{html.escape(sym.replace("-USDT", ""))}'
            f'<div class="bar"><i style="width:'
            f'{min(abs(gap) / 30 * 100, 100):.0f}%;background:{col}"></i></div>'
            f'</td><td>{px:,.6g}</td><td class="dim">{ma:,.6g}</td>'
            f'<td class="{tone(gap)}">{gap:+.2f}%</td>'
            f'<td><span class="pill {"p-hold" if held else "p-off"}">'
            f'{"持有" if held else "空手"}</span></td></tr>')
    return ('<div class="card"><h2>決策變數</h2>'
            '<p class="note">系統的決策變數只有一個:'
            '<b>收盤價在不在 50 日均線之上</b>。在之上就持有、之下就空手,'
            '再由波動目標把整體規模調到年化 27%。'
            '距離越接近 0,下一次換手越可能發生在那個幣上。</p>'
            '<div class="scroll"><table><thead><tr><th>商品</th><th>收盤</th>'
            '<th>50日均線</th><th>距離</th><th>狀態</th></tr></thead><tbody>'
            + "".join(out) + '</tbody></table></div></div>')


def block_monitor() -> str:
    m = _json(DATA / "portfolio_monitor.json")
    if not m:
        return ''
    bt = m.get("backtest") or {}
    flags = "".join(
        f'<div class="flag {"warn" if a.get("level") == "HIGH" else ""}">'
        f'<b>[{html.escape(str(a.get("level")))}] '
        f'{html.escape(str(a.get("kind")))}</b><br>'
        f'{html.escape(str(a.get("msg")))}</div>'
        for a in (m.get("alerts") or []))
    return ('<div class="card"><h2>策略監控</h2>'
            '<p class="note">唯一該問自己的問題:'
            '<b>它還是不是回測時的那條策略?</b><br>'
            f'回測基準 — 年化 {bt.get("cagr_pct")}% · 回撤 '
            f'{bt.get("max_dd_pct")}% · Sharpe {bt.get("sharpe")} · 在場 '
            f'{bt.get("days_in_market_pct")}%<br>'
            f'<span class="dim">{html.escape(str(bt.get("window", "")))}</span>'
            f'</p><p class="note">{html.escape(str(m.get("verdict", "")))}</p>'
            + flags +
            '<div class="flag">監控層<b>不會自動調參數把績效救回來</b> —— '
            '那是把過擬合自動化。它只做一件事:量前向行為有沒有跑出歷史範圍,'
            '跑出去就講。</div></div>')


def block_correlation() -> str:
    """
    相關性集中度(第六十條)。

    這張卡回答一個「總曝險」永遠不會回答的問題:
    **這七個倉是七個賭注,還是同一個賭注的七個面?**

    上限還沒設定,而**沒有上限的期間正是最需要天天看到這個數字的
    時候** —— 執政官要拿它決定門檻。所以這張卡在沒有上限時照樣顯示,
    而且明說上限還沒設。
    """
    r = _json(DATA / "portfolio_risk.json")
    if not r:
        return ''
    c = r.get("concentration")
    if not c:
        return ''

    total = c.get("total_exposure_pct")
    equiv = c.get("equivalent_exposure_pct")
    enp = c.get("effective_positions")
    n = c.get("positions") or 0

    if equiv is None or enp is None:
        why = html.escape(str(c.get("reason") or "資料不足"))
        return ('<div class="card"><h2>相關性集中度</h2>'
                f'<div class="big dim">—</div>'
                f'<p class="note">算不出來:{why}<br>'
                '<b>沒有拿一個「假設不相關」的數字頂替。</b> '
                '在最危險的時候給最樂觀的答案,是這裡最貴的一種錯。'
                '</p></div>')

    # 帳面幾檔、實際上等於幾檔
    shrink = (1 - enp / n) * 100 if n else 0.0
    return ('<div class="card"><h2>相關性集中度</h2>'
            f'<div class="big">{equiv:.1f}<span '
            'style="font-size:16px;color:var(--dim)">% 等效單一標的</span>'
            '</div>'
            '<div class="grid">'
            + kv("帳面總曝險", f"{total:.1f}%")
            + kv("帳面檔數", f"{n} 檔")
            + kv("有效檔數", f"{enp:.1f} 檔")
            + kv("分散度損失", f"{shrink:.0f}%")
            + f'</div><p class="note">'
            f'帳面 <b>{n} 檔 / {total:.1f}%</b>,把相關性算進去之後,'
            f'這個組合等於<b>一個 {equiv:.1f}% 的單一標的</b>'
            f'(有效 {enp:.1f} 檔,'
            f'{c.get("observations")} 天共同觀測)。<br>'
            '總曝險只是加總,它不會告訴你七個倉是不是同一個賭注。'
            '加密貨幣的相關性在恐慌時往 1 靠攏 —— 而那正是風控唯一'
            '真的重要的時候。</p>'
            '<div class="flag"><b>上限尚未設定</b>,需執政官指定。'
            '在那之前這條檢查每天照跑、照記錄,但不會擋單 —— '
            '一條 Risk Limit 不該由程式自己決定(第 102 條)。</div>'
            '</div>')


def block_events() -> str:
    """
    事件日曆(第五十一條)。

    這張卡在**沒有日曆的時候照樣顯示** —— 而且顯示的是
    「沒有在看」,不是「今天沒事」。把卡片藏起來會讓人以為
    這件事有人在管。
    """
    from portfolio import events

    try:
        st = events.status()
    except Exception as e:
        return ('<div class="card"><h2>事件日曆</h2>'
                f'<p class="note">讀取失敗:{html.escape(str(e))}</p></div>')

    if not st["loaded"] or st["stale"]:
        why = html.escape(str(st.get("reason") or ""))
        return ('<div class="card"><h2>事件日曆</h2>'
                '<div class="big dim">未載入</div>'
                f'<p class="note">{why}</p>'
                '<div class="flag"><b>「沒有載入日曆」不等於「今天沒有'
                '事件」。</b>前者是我不知道,後者是一個確定的判斷 —— '
                '而這裡不知道。<br>'
                '日期要從發布單位拿(聯準會 / BLS / BEA),'
                '格式見 <code>docs/events.example.json</code>。'
                '憑記憶寫下的日期會讓人以為有在看,而內容是錯的 —— '
                '一份錯的日曆比沒有日曆危險。</div></div>')

    def row(e):
        return (f'<tr><td class="sym">{html.escape(e["date"])}</td>'
                f'<td>{html.escape(e["kind"])}</td>'
                f'<td><div class="why">{html.escape(e["note"])}</div></td>'
                '</tr>')

    today = st["today"]
    head = (f'<div class="big warn">今天:'
            + "、".join(html.escape(e["kind"]) for e in today) + '</div>'
            if today else '<div class="big dim">今天無事件</div>')

    rows = "".join(row(e) for e in st["upcoming"])
    table = ('<div class="scroll"><table><tbody>' + rows + '</tbody></table>'
             '</div>') if rows else '<p class="note">未來 14 天內沒有事件。</p>'

    return ('<div class="card"><h2>事件日曆</h2>' + head + table +
            '<div class="flag">這條策略回測 3.3 年約<b>每月換手一次</b>,'
            '持有的倉會原封不動地穿過事件 —— 躲不掉。<br>'
            '所以這裡只做<b>看得見</b>,<b>不改變任何交易決策</b>。'
            '要擋單就會改變進場日期,而那等於換一條策略,'
            'Calmar 1.33 要重新驗證。</div></div>')


def block_contract() -> str:
    c = _json(DATA / "portfolio_contract.json")
    if not c:
        return ''
    rows = "".join(
        f'<tr><td class="{"up" if x.get("passed") else "down"}">'
        f'{"✓" if x.get("passed") else "✗"}</td>'
        f'<td class="sym">{html.escape(str(x.get("name")))}</td>'
        f'<td><div class="why">{html.escape(str(x.get("detail")))}</div></td>'
        '</tr>' for x in (c.get("criteria") or []))
    p, t = c.get("passed", 0), c.get("total", 8)
    return ('<div class="card"><h2>實盤資格契約</h2>'
            f'<div class="big {"up" if p >= t else ""}">{p}<span '
            'style="font-size:16px;color:var(--dim)">/' f'{t}</span></div>'
            '<div class="scroll"><table><tbody>' + rows + '</tbody></table></div>'
            f'<p class="note">{html.escape(str(c.get("verdict", "")))}</p>'
            '<div class="flag">門檻在乾淨樣本為零時寫下,<b>不得事後放寬</b>。'
            '八條全過也只是「有資格談」—— 實盤仍需人工簽署,永不自動化。'
            '</div></div>')


def block_system() -> str:
    import subprocess
    units = [("agmcis-portfolio", "組合記帳", "每日 00:30 · 唯一策略"),
             ("agmcis-sentinel", "巡檢", "每 10 分鐘"),
             ("agmcis-gauge", "費率快照", "成本模型的實測來源"),
             ("agmcis-dash", "面板", "本頁"),
             ("agmcis-archivist", "備份", "每日")]
    cards = []
    for unit, name, desc in units:
        st = "?"
        for suffix in (".timer", ".service"):
            try:
                r = subprocess.run(["systemctl", "is-active", unit + suffix],
                                   capture_output=True, text=True, timeout=4)
                st = r.stdout.strip()
                if st == "active":
                    break
            except Exception:
                pass
        ok = st == "active"
        cards.append(
            f'<div class="kv"><div class="l">{name}</div>'
            f'<div class="v {"up" if ok else "down"}" style="font-size:12.5px">'
            f'{"運轉" if ok else html.escape(st)}</div>'
            f'<div class="why">{desc}</div></div>')

    sen = _json(DATA / "sentinel_state.json")
    checks = [(k, v) for k, v in sen.items()
              if isinstance(v, dict) and "ok" in v]
    bad = [k for k, v in checks if not v.get("ok")]
    fresh = []
    try:
        from portfolio.paper import SYMBOLS
        for s in SYMBOLS:
            a = age_min(HIST / f"{s}_1d.csv")
            if a is not None:
                fresh.append(a)
    except Exception:
        pass
    fh = f"{max(fresh) / 60:.1f} 小時" if fresh else "無資料"
    return ('<div class="card"><h2>系統</h2>'
            f'<div class="grid">{"".join(cards)}</div>'
            f'<p class="note">巡檢 {len(checks)} 項 · '
            + (f'<b class="down">{len(bad)} 項異常:'
               f'{html.escape(", ".join(bad))}</b>' if bad
               else '<b class="up">全部正常</b>')
            + f' · 日線資料最舊 {fh}</p>'
            '<div class="flag ok">執行場所 <b>PAPER(模擬金)</b>。實盤三道鎖:'
            '① <code>LIVE_ENABLED=False</code> 是原始碼常數,不是設定選項 —— '
            '要開必須改碼、commit、部署;② 實盤資格契約八條;'
            '③ 人工簽署,永不自動化。</div></div>')


def block_wild() -> str:
    """測試組對照 —— 2026-09-10 取代已退役的放養組。

    原本這一格讀 benchmark.json 的 wild 欄位,但寫它的那支在 09-08 重建
    時就被刪了 —— 資料從此凍在那一天,而畫面上完全看不出它是死的。
    憲法第九條:「面板說了 52 天假話。」現在改讀測試組自己的權益曲線,
    每天都會動;讀不到就明說讀不到,不顯示過期數字。
    """
    from portfolio.trial import compare
    try:
        c = _cached("trial", 120, compare)
    except Exception as e:
        return ('<div class="card"><h2>測試組</h2><p class="note">'
                f'讀取失敗:{html.escape(str(e))}</p></div>')
    m, t = c.get("main"), c.get("trial")
    if not t:
        # 還畫不出「比較」,但帳戶本身是有東西的 —— 顯示它,
        # 而不是只寫一句「樣本不足」讓人以為系統沒在跑。
        a = _json(DATA / "trial_account.json")
        if not a:
            return ('<div class="card"><h2>測試組</h2><p class="note">'
                    '測試組尚未開始記帳。</p></div>')
        pos = a.get("positions") or {}
        names = ", ".join(sorted(x.replace("-USDT", "") for x in pos))
        return ('<div class="card"><h2>測試組 · 動態交易池</h2><div class="grid">'
                + kv("權益", f"{float(a.get('equity', 0)):,.2f}")
                + kv("報酬", f"{float(a.get('return_pct', 0)):+.2f}%",
                     tone(float(a.get("return_pct", 0))))
                + kv("持倉", f"{len(pos)} 檔")
                + kv("記帳", f"{a.get('days', 0)} 天")
                + '</div>'
                f'<p class="note">交易池:{html.escape(names)}</p>'
                '<div class="flag">與主城的對照要<b>至少兩天</b>才畫得出來'
                '(需要算報酬與回撤)。測試組每日 00:35 記帳,'
                '主城 00:30 —— 明天就會出現對照數字。</div></div>')
    rows = "".join(
        f'<tr><td class="{"up" if x["passed"] else "down"}">'
        f'{"✓" if x["passed"] else "✗"}</td>'
        f'<td class="sym">{html.escape(x["name"])}</td>'
        f'<td><div class="why">{html.escape(x["detail"])}</div></td></tr>'
        for x in (c.get("criteria") or []))
    grid = (kv("測試組報酬", f"{t['total_pct']:+.2f}%", tone(t["total_pct"]))
            + kv("主城報酬", f"{m['total_pct']:+.2f}%", tone(m["total_pct"]))
            if m else kv("測試組報酬", f"{t['total_pct']:+.2f}%",
                         tone(t["total_pct"])))
    grid += (kv("測試組回撤", f"{t['max_dd_pct']:.2f}%")
             + kv("測試組 Calmar", f"{t['calmar']:.2f}")
             + kv("前向天數", f"{t['days']}"))
    return ('<div class="card"><h2>測試組 · 動態交易池</h2>'
            f'<div class="grid">{grid}</div>'
            '<div class="scroll"><table><tbody>' + rows + '</tbody></table></div>'
            f'<p class="note">{html.escape(str(c.get("verdict", "")))}</p>'
            '<div class="flag">測試組是<b>平行的紙上帳戶</b>,跑動態交易池,'
            '用同一段真實價格與主城對照。它存在的理由:回測分不出'
            '「動態池比較差」與「固定 7 幣被後見之明美化」——'
            '<b>只有前向能回答</b>。<br>它<b>只累積證據,永不自動切換設定</b>;'
            '三條判準全過也只是「有資格談」,換不換由執政官裁決。</div></div>')


# ══════════════════════════════════════════════════════════
# 測試組獨立頁(/trial,舊捷徑 /wild 也導到這裡)
#
# 2026-09-10:舊面板有 /wild 路由(wild_page()),執政官的桌面捷徑
# 「AGMCIS·WILD」指向它。09-08 重建面板時那條路由沒有被重建 ——
# 於是 /wild 落到主頁,兩個捷徑開起來一模一樣,而畫面上完全看不出
# 「這個入口已經不存在了」。憲法第九條的同一類錯:
# **在職就是在職,沒有退路可言。**
# 放養組已退役,那個入口正好交給測試組。
# ══════════════════════════════════════════════════════════
def trial_account_block() -> str:
    a = _json(DATA / "trial_account.json")
    if not a:
        return ('<div class="card"><h2>測試組帳戶</h2><p class="note">'
                '尚未記帳 —— 每日 00:35 UTC 自動執行。</p></div>')
    eq = float(a.get("equity", 0) or 0)
    ret = float(a.get("return_pct", 0) or 0)
    rp = float(a.get("realized_pnl_net", a.get("realized_pnl", 0)) or 0)
    up = float(a.get("unrealized_pnl", 0) or 0)
    dd = float(a.get("drawdown_pct", 0) or 0)
    lev = float(a.get("leverage", 1) or 1)
    mmr = float(a.get("maint_margin_rate", 0.005) or 0.005)
    cells = [
        kv("可用保證金", f"{float(a.get('available_margin', 0)):,.0f}"),
        kv("已用保證金", f"{float(a.get('used_margin', 0)):,.0f}"),
        kv("已實現", f"{rp:+,.2f}", tone(rp)),
        kv("未實現", f"{up:+,.2f}", tone(up)),
        kv("曝險", f"{float(a.get('exposure', 0)):.0%}"),
        kv("回撤", f"{dd:.2f}%", "up" if dd <= 15 else "down"),
        kv("成交", f"{a.get('orders_filled', 0)} 單"),
        kv("記帳", f"{a.get('days', 0)} 天"),
        kv("持倉", f"{len(a.get('positions') or {})} 檔"),
    ]
    return ('<div class="card"><h2>測試組帳戶</h2>'
            f'<div class="big {tone(ret)}">{eq:,.2f}'
            '<span style="font-size:14px;color:var(--dim)"> USDT</span></div>'
            f'<div class="sub {tone(ret)}">{ret:+.2f}% · 起始 '
            f'{float(a.get("start_equity", 10000)):,.0f}</div>'
            f'<div class="grid">{"".join(cells)}</div>'
            f'<p class="note">策略與主城<b>完全相同</b>'
            f'(<b>{html.escape(str(a.get("strategy", "—")))}</b>)· '
            f'逐倉槓桿 {lev:.0f}×(強平距離 {(1 / max(lev, 1e-9) - mmr) * 100:.1f}%)'
            f' · 唯一的變因是<b>交易池</b> —— 一次只改一個東西,'
            '否則贏了也不知道是誰帶來的。</p></div>')


def trial_positions_block() -> str:
    a = _json(DATA / "trial_account.json")
    pos = a.get("positions") or {}
    if not pos:
        return '<div class="card"><h2>測試組持倉</h2><p class="note">空手。</p></div>'
    rows, tot = [], 0.0
    for sym, p in sorted(pos.items()):
        amt = float(p.get("position_amt") or 0)
        entry = float(p.get("avg_price") or 0)
        mk = float(p.get("mark_price") or entry)
        u = float(p.get("unrealized_pnl", (mk - entry) * amt))
        roi = float(p.get("roi_pct") or 0)
        lp = p.get("liq_price")
        tot += u
        liq = (f'{lp:,.6g}<div class="why">距 {abs(mk - lp) / mk * 100:.1f}%</div>'
               if (lp and mk) else '<span class="dim">—</span>')
        rows.append(
            f'<tr><td class="sym">{html.escape(sym.replace("-USDT", ""))}'
            f'<div class="why">{"多" if amt > 0 else "空"} · '
            f'{float(p.get("leverage", 1)):.0f}×</div></td>'
            f'<td>{abs(amt):.6g}</td><td>{entry:,.6g}</td><td>{mk:,.6g}</td>'
            f'<td class="{tone(u)}">{u:+,.2f}</td>'
            f'<td class="{tone(roi)}"><b>{roi:+.2f}%</b></td>'
            f'<td>{liq}</td></tr>')
    return ('<div class="card"><h2>測試組持倉</h2>'
            '<div class="scroll"><table><thead><tr><th>商品</th><th>持倉量</th>'
            '<th>開倉均價</th><th>標記價</th><th>未實現</th><th>ROI</th>'
            '<th>強平價</th></tr></thead><tbody>' + "".join(rows)
            + '</tbody></table></div>'
            f'<p class="note">未實現合計 <b class="{tone(tot)}">{tot:+,.2f} '
            'USDT</b></p></div>')


def trial_page() -> str:
    now = datetime.now(timezone.utc)
    body = (trial_account_block() + block_wild() + trial_positions_block())
    return f"""<!doctype html><html lang="zh-Hant"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="AGMCIS 測試組">
<meta name="theme-color" content="#08090d">
<title>AGMCIS 測試組</title><style>{CSS}</style></head><body>
<div class="wrap">
<header><h1>AGMCIS</h1>
  <span class="tag paper">測試組 · 模擬金</span>
  <span class="tag">{now:%m-%d %H:%M} UTC</span>
</header>
<nav><a href="/?key={html.escape(_env('DASHBOARD_KEY'))}">← 回主城</a></nav>
{body}
<p class="note" style="text-align:center;margin-top:18px">
測試組每日 00:35 UTC 記帳(主城 00:30)。它<b>只累積證據,永不自動切換設定</b>。
</p>
</div></body></html>"""


# ══════════════════════════════════════════════════════════
# 頁面
# ══════════════════════════════════════════════════════════
def render() -> str:
    now = datetime.now(timezone.utc)
    desk = block_account() + block_orders() + block_positions() + block_curve()
    signals = block_signals() + block_monitor()
    system = (block_events() + block_correlation() + block_contract()
              + block_system() + block_wild())
    return f"""<!doctype html><html lang="zh-Hant"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="theme-color" content="#08090d">
<title>AGMCIS 交易台</title><style>{CSS}</style></head><body>
<div class="wrap">
<header><h1>AGMCIS</h1>
  <span class="tag paper">PAPER · 模擬金</span>
  <span class="tag">{now:%m-%d %H:%M} UTC</span>
</header>
<nav>
  <a href="#" class="on" data-t="desk">交易台</a>
  <a href="#" data-t="signals">訊號</a>
  <a href="#" data-t="system">系統</a>
</nav>
<div class="tabpane on" id="desk">{desk}</div>
<div class="tabpane" id="signals">{signals}</div>
<div class="tabpane" id="system">{system}</div>
<footer>紙上交易,非真錢 · 每日 00:30 UTC 記帳</footer>
</div>
<script>
document.querySelectorAll('nav a').forEach(function(a){{
  a.addEventListener('click', function(e){{
    e.preventDefault();
    document.querySelectorAll('nav a').forEach(function(x){{
      x.classList.remove('on'); }});
    a.classList.add('on');
    document.querySelectorAll('.tabpane').forEach(function(p){{
      p.classList.toggle('on', p.id === a.dataset.t); }});
    window.scrollTo(0, 0);
  }});
}});
var KEY = new URLSearchParams(location.search).get('key') || '';
function fmt(n, d){{ return n.toLocaleString('en-US',
  {{minimumFractionDigits:d, maximumFractionDigits:d}}); }}
function sgn(n, d){{ return (n>=0?'+':'') + fmt(n, d); }}
function paint(el, v){{
  if(!el) return;
  el.classList.remove('up','down');
  if(v > 0) el.classList.add('up'); else if(v < 0) el.classList.add('down');
}}
function tick(){{   // SSE 不可用時的退路
  fetch('/api/live?key=' + encodeURIComponent(KEY), {{cache:'no-store'}})
    .then(function(r){{ return r.json(); }})
    .then(function(d){{
      var dot = document.getElementById('live-dot');
      var lt  = document.getElementById('live-t');
      if(d.error || (d.stale && d.stale.length)){{
        // 取不到即時價就明說,不拿舊價假裝是即時價
        if(dot) dot.classList.add('off');
        if(lt) lt.textContent = d.error ? '報價中斷' : '部分報價缺';
      }} else {{
        if(dot) dot.classList.remove('off');
        if(lt) lt.textContent = '即時 ' +
          new Date(d.t*1000).toLocaleTimeString('en-GB');
      }}
      var eq = document.getElementById('k-eq');
      if(eq && d.equity != null){{
        eq.innerHTML = fmt(d.equity,2) +
          '<span style="font-size:14px;color:var(--dim)"> USDT</span>';
        paint(eq, d.return_pct);
      }}
      var ret = document.getElementById('k-ret');
      if(ret && d.return_pct != null){{
        ret.textContent = sgn(d.return_pct,2) + '% · 起始 ' +
          fmt(d.start_equity,0);
        paint(ret, d.return_pct);
      }}
      var m = {{'k-cash':[d.available_margin,0,false],
                'k-um':[d.used_margin,0,false],
                'k-rp':[d.realized_pnl,2,true],
                'k-up':[d.unrealized_pnl,2,true]}};
      for(var id in m){{
        var el = document.getElementById(id);
        if(!el || m[id][0] == null) continue;
        el.textContent = m[id][2] ? sgn(m[id][0],m[id][1])
                                  : fmt(m[id][0],m[id][1]);
        if(m[id][2]) paint(el, m[id][0]);
      }}
      var ex = document.getElementById('k-ex');
      if(ex && d.exposure != null) ex.textContent =
        Math.round(d.exposure*100) + '%';
      var dd = document.getElementById('k-dd');
      if(dd && d.drawdown_pct != null){{
        dd.textContent = fmt(d.drawdown_pct,2) + '%';
        dd.classList.remove('up','down');
        dd.classList.add(d.drawdown_pct <= 15 ? 'up' : 'down');
      }}
      (d.positions||[]).forEach(function(p){{
        var s = p.symbol.replace('-USDT','');
        var px = document.getElementById('p-px-'+s);
        if(px){{
          var mk = (p.mark_price != null) ? p.mark_price : p.price;
          if(px.firstChild) px.firstChild.nodeValue = fmt(mk, mk>100?2:4);
        }}
        var lastEl = document.getElementById('p-last-'+s);
        if(lastEl && p.price != null)
          lastEl.textContent = '最新 ' + fmt(p.price, p.price>100?2:4)
            + (p.mark_is_real === false ? ' ⚠標記價缺' : '');
        var vl = document.getElementById('p-val-'+s);
        if(vl) vl.textContent = fmt(p.value,0);
        var im = document.getElementById('p-im-'+s);
        if(im && p.initial_margin != null)
          im.textContent = fmt(p.initial_margin,2);
        var ro = document.getElementById('p-roi-'+s);
        if(ro && p.roi_pct != null){{
          ro.innerHTML = '<b>' + sgn(p.roi_pct,2) + '%</b>';
          paint(ro, p.roi_pct);
        }}
        var pn = document.getElementById('p-pnl-'+s);
        if(pn){{
          pn.innerHTML = sgn(p.upnl,2) +
            '<div class="why">' + sgn(p.upnl_pct,2) + '%</div>';
          paint(pn, p.upnl);
        }}
      }});
      var tot = document.getElementById('p-tot');
      if(tot && d.unrealized_pnl != null){{
        tot.textContent = sgn(d.unrealized_pnl,2) + ' USDT';
        paint(tot, d.unrealized_pnl);
      }}
    }})
    .catch(function(){{
      var dot = document.getElementById('live-dot');
      if(dot) dot.classList.add('off');
      var lt = document.getElementById('live-t');
      if(lt) lt.textContent = '連線中斷';
    }});
}}
function apply(d){{
  var dot = document.getElementById('live-dot');
  var lt  = document.getElementById('live-t');
  var st  = d.stream || {{}};
  if(d.error || (d.stale && d.stale.length) || !st.connected){{
    if(dot) dot.classList.add('off');
    if(lt) lt.textContent = d.error ? '報價中斷'
      : (!st.connected ? '退路取價' : '部分報價缺');
  }} else {{
    if(dot) dot.classList.remove('off');
    if(lt) lt.textContent = '串流 ' +
      new Date(d.t*1000).toLocaleTimeString('en-GB');
  }}
  var eq = document.getElementById('k-eq');
  if(eq && d.equity != null){{
    eq.innerHTML = fmt(d.equity,2) +
      '<span style="font-size:14px;color:var(--dim)"> USDT</span>';
    paint(eq, d.return_pct);
  }}
  var ret = document.getElementById('k-ret');
  if(ret && d.return_pct != null){{
    ret.textContent = sgn(d.return_pct,2) + '% · 起始 ' + fmt(d.start_equity,0);
    paint(ret, d.return_pct);
  }}
  var m = {{'k-cash':[d.available_margin,0,false],
            'k-um':[d.used_margin,0,false],
            'k-rp':[d.realized_pnl,2,true],
            'k-up':[d.unrealized_pnl,2,true]}};
  for(var id in m){{
    var el = document.getElementById(id);
    if(!el || m[id][0] == null) continue;
    el.textContent = m[id][2] ? sgn(m[id][0],m[id][1]) : fmt(m[id][0],m[id][1]);
    if(m[id][2]) paint(el, m[id][0]);
  }}
  var ex = document.getElementById('k-ex');
  if(ex && d.exposure != null) ex.textContent = Math.round(d.exposure*100)+'%';
  var dd = document.getElementById('k-dd');
  if(dd && d.drawdown_pct != null){{
    dd.textContent = fmt(d.drawdown_pct,2) + '%';
    dd.classList.remove('up','down');
    dd.classList.add(d.drawdown_pct <= 15 ? 'up' : 'down');
  }}
  (d.positions||[]).forEach(function(p){{
    var s = p.symbol.replace('-USDT','');
    // 標記價是交易所用來算未實現/強平的價格;最新成交價另外標在下方。
    // 兩個都顯示,跟交易所 App 一樣 —— 它們分岔的時候正是插針的時候。
    var px = document.getElementById('p-px-'+s);
    if(px){{
      var mk = (p.mark_price != null) ? p.mark_price : p.price;
      var nv = fmt(mk, mk>100?2:4);
      if(px.firstChild && px.firstChild.nodeValue !== nv){{
        px.firstChild.nodeValue = nv;
        px.classList.remove('flash'); void px.offsetWidth;
        px.classList.add('flash');
      }}
    }}
    var lastEl = document.getElementById('p-last-'+s);
    if(lastEl && p.price != null){{
      lastEl.textContent = '最新 ' + fmt(p.price, p.price>100?2:4)
        + (p.mark_is_real === false ? ' ⚠標記價缺' : '');
    }}
    var vl = document.getElementById('p-val-'+s);
    if(vl) vl.textContent = fmt(p.value,0);
    var im = document.getElementById('p-im-'+s);
    if(im && p.initial_margin != null) im.textContent = fmt(p.initial_margin,2);
    var ro = document.getElementById('p-roi-'+s);
    if(ro && p.roi_pct != null){{
      ro.innerHTML = '<b>' + sgn(p.roi_pct,2) + '%</b>';
      paint(ro, p.roi_pct);
    }}
    var pn = document.getElementById('p-pnl-'+s);
    if(pn){{
      pn.innerHTML = sgn(p.upnl,2) +
        '<div class="why">' + sgn(p.upnl_pct,2) + '%</div>';
      paint(pn, p.upnl);
    }}
  }});
  var tot = document.getElementById('p-tot');
  if(tot && d.unrealized_pnl != null){{
    tot.textContent = sgn(d.unrealized_pnl,2) + ' USDT';
    paint(tot, d.unrealized_pnl);
  }}
}}
var es = null;
function connect(){{
  try{{ es = new EventSource('/api/stream?key=' + encodeURIComponent(KEY)); }}
  catch(e){{ tick(); setInterval(tick, 10000); return; }}
  es.onmessage = function(ev){{
    try{{ apply(JSON.parse(ev.data)); }}catch(e){{}}
  }};
  es.onerror = function(){{
    // 斷線先標紅,再讓 EventSource 自己重連;它不會就換輪詢
    var dot = document.getElementById('live-dot');
    if(dot) dot.classList.add('off');
    var lt = document.getElementById('live-t');
    if(lt) lt.textContent = '重新連線…';
  }};
}}
connect();

/* ══ 持倉列的即時 K 線 ══════════════════════════════════════
   點幣種那一列展開。畫的是蠟燭圖,並標出兩條跟這筆倉直接相關的線:
   開倉均價(琥珀虛線)與強平價(紅虛線)—— 看走勢的目的是知道
   「現在離我的進場點和爆倉點多遠」,不是純粹看圖形。
   資料走 /api/klines,只餵眼睛,不進任何決策。                   */
var KSTATE = {{}};   // sid -> {{iv, timer}}

function drawK(sid, bars, entry, liq){{
  var box = document.getElementById('k-chart-'+sid);
  if(!box) return;
  if(!bars || !bars.length){{
    box.innerHTML = '<span class="dim">取不到 K 線</span>'; return;
  }}
  var W=640, H=190, PL=6, PR=54, PT=8, PB=8;
  var lo=Infinity, hi=-Infinity;
  bars.forEach(function(b){{ if(b.l<lo)lo=b.l; if(b.h>hi)hi=b.h; }});
  // 進場價一定要在圖裡,否則「離進場多遠」這件事看不出來
  if(entry>0){{ lo=Math.min(lo,entry); hi=Math.max(hi,entry); }}
  var showLiq = liq>0 && liq>lo*0.75 && liq<hi*1.25;
  if(showLiq){{ lo=Math.min(lo,liq); hi=Math.max(hi,liq); }}
  if(hi-lo < 1e-9){{ hi=lo+1; lo=lo-1; }}
  var pad=(hi-lo)*0.06; lo-=pad; hi+=pad;
  function Y(v){{ return PT+(H-PT-PB)*(1-(v-lo)/(hi-lo)); }}
  var n=bars.length, iw=(W-PL-PR)/n, bw=Math.max(1, Math.min(iw*0.62, 9));
  var s='<svg viewBox="0 0 '+W+' '+H+'" preserveAspectRatio="none">';
  bars.forEach(function(b,i){{
    var x=PL+iw*(i+0.5), up=b.c>=b.o;
    var col= up ? '#33d19d' : '#f0654f';
    var yo=Y(b.o), yc=Y(b.c);
    var top=Math.min(yo,yc), hgt=Math.max(Math.abs(yc-yo), 1);
    s+='<line x1="'+x.toFixed(1)+'" y1="'+Y(b.h).toFixed(1)+
       '" x2="'+x.toFixed(1)+'" y2="'+Y(b.l).toFixed(1)+
       '" stroke="'+col+'" stroke-width="1"/>';
    s+='<rect x="'+(x-bw/2).toFixed(1)+'" y="'+top.toFixed(1)+
       '" width="'+bw.toFixed(1)+'" height="'+hgt.toFixed(1)+
       '" fill="'+col+'"/>';
  }});
  function mark(v, col, label){{
    var y=Y(v);
    if(y<PT||y>H-PB) return '';
    return '<line x1="'+PL+'" y1="'+y.toFixed(1)+'" x2="'+(W-PR)+
      '" y2="'+y.toFixed(1)+'" stroke="'+col+
      '" stroke-width="1" stroke-dasharray="4 3" opacity=".85"/>'+
      '<text x="'+(W-PR+4)+'" y="'+(y+3.5).toFixed(1)+
      '" fill="'+col+'" font-size="10" font-family="ui-monospace,monospace">'+
      label+'</text>';
  }}
  if(entry>0) s+=mark(entry, '#e0a13a', '進場');
  if(showLiq) s+=mark(liq, '#f0654f', '強平');
  var last=bars[bars.length-1].c;
  s+=mark(last, '#8b93a7', fmt(last, last>100?1:4));
  s+='</svg>';
  box.innerHTML=s;
  var info=document.getElementById('k-info-'+sid);
  if(info){{
    var first=bars[0].c, chg=(last/first-1)*100;
    info.textContent = bars.length+' 根 · '+sgn(chg,2)+'%';
    info.className='kinfo '+(chg>0?'up':(chg<0?'down':''));
  }}
}}

function loadK(sid, sym, iv, entry, liq){{
  fetch('/api/klines?symbol='+encodeURIComponent(sym)+
        '&interval='+encodeURIComponent(iv)+
        '&key='+encodeURIComponent(KEY), {{cache:'no-store'}})
    .then(function(r){{ return r.json(); }})
    .then(function(d){{
      if(d.error){{
        var box=document.getElementById('k-chart-'+sid);
        if(box) box.innerHTML='<span class="dim">'+d.error+'</span>';
        return;
      }}
      drawK(sid, d.bars, entry, liq);
    }})
    .catch(function(){{
      var box=document.getElementById('k-chart-'+sid);
      if(box) box.innerHTML='<span class="dim">K 線連線失敗</span>';
    }});
}}

document.querySelectorAll('tr.prow').forEach(function(tr){{
  tr.addEventListener('click', function(){{
    var sid=tr.dataset.sid, sym=tr.dataset.sym;
    var entry=parseFloat(tr.dataset.entry)||0;
    var liq=parseFloat(tr.dataset.liq)||0;
    var row=document.getElementById('k-row-'+sid);
    if(!row) return;
    var open=row.classList.toggle('on');
    tr.classList.toggle('open', open);
    var st=KSTATE[sid]||(KSTATE[sid]={{iv:'15m',timer:null}});
    if(open){{
      loadK(sid, sym, st.iv, entry, liq);
      // 展開時才輪詢,收起就停 —— 沒在看的圖不該一直打交易所
      st.timer=setInterval(function(){{
        loadK(sid, sym, st.iv, entry, liq); }}, 20000);
    }} else if(st.timer){{ clearInterval(st.timer); st.timer=null; }}
  }});
}});

document.querySelectorAll('button.kiv').forEach(function(b){{
  b.addEventListener('click', function(ev){{
    ev.stopPropagation();          // 不要觸發整列的收合
    var sid=b.dataset.sid, iv=b.dataset.iv;
    var tr=document.querySelector('tr.prow[data-sid="'+sid+'"]');
    if(!tr) return;
    document.querySelectorAll('button.kiv[data-sid="'+sid+'"]')
      .forEach(function(x){{ x.classList.toggle('on', x===b); }});
    var st=KSTATE[sid]||(KSTATE[sid]={{iv:iv,timer:null}});
    st.iv=iv;
    loadK(sid, tr.dataset.sym, iv,
          parseFloat(tr.dataset.entry)||0, parseFloat(tr.dataset.liq)||0);
  }});
}});

setTimeout(function(){{ location.reload(); }}, 900000);
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, body: bytes, ctype: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send_json(self, status: int, payload) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        from urllib.parse import parse_qs, urlparse
        key = _env("DASHBOARD_KEY")
        u = urlparse(self.path)
        q = parse_qs(u.query)
        authorised = (not key) or q.get("key", [""])[0] == key

        # ── /health(第六十六條)—— 在金鑰檢查**之前** ────────────
        # 監控探針帶不了 DASHBOARD_KEY。一個要金鑰才能問的健康檢查,
        # 在最需要它的時候(沒有人在旁邊)剛好用不了。
        #
        # 不帶金鑰只會拿到每條檢查的名字與過不過 —— 那正是探針需要的
        # 全部。數字與細節要帶金鑰。
        #
        # 任何一條不過就回 503。**一個永遠回 200 的 /health 比沒有
        # /health 危險** —— 它會讓上面每一層監控都變綠燈,
        # 而綠燈的理由是它什麼都沒在看(教訓第 5 條)。
        if u.path.rstrip("/") == "/health":
            from core import health
            try:
                status, payload = health.report(detailed=authorised)
            except Exception as e:
                # 連 report 本身都爆炸 —— 那也是一種不健康,
                # 而且要說得出是什麼爆炸,不是丟一個 500 讓人猜。
                status, payload = 503, {
                    "status": "unhealthy",
                    "checks": [{"name": "健康檢查本身", "ok": False,
                                "detail": f"{type(e).__name__}: {e}"}]}
            self._send_json(status, payload)
            return

        if not authorised:
            self.send_response(403)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        # ── SSE:交易所逐筆推播 → 這裡 → 瀏覽器,全程無輪詢 ──────
        # 每 1 秒推一次快照(交易所實測約 10 筆/秒,再快人眼也讀不了,
        # 而且會讓數字抖到看不清 —— 1 秒是「即時」與「可讀」的平衡點)。
        if u.path.startswith("/api/stream"):
            import time as _t
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            try:
                from portfolio.live import snapshot
                from portfolio import stream as _stream
                _stream.start()
                while True:
                    try:
                        payload = snapshot()
                    except Exception as e:
                        payload = {"error": f"{type(e).__name__}: {e}"}
                    line = ("data: "
                            + json.dumps(payload, ensure_ascii=False)
                            + "\n\n")
                    self.wfile.write(line.encode("utf-8"))
                    self.wfile.flush()
                    _t.sleep(1.0)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass          # 瀏覽器關掉分頁,正常
            return

        # ── 測試組獨立頁(/trial;/wild 是執政官既有的桌面捷徑)──
        if u.path.startswith("/trial") or u.path.startswith("/wild"):
            try:
                body = trial_page().encode("utf-8")
            except Exception as e:
                body = (f"<pre>測試組頁渲染失敗:{html.escape(type(e).__name__)}"
                        f": {html.escape(str(e))}</pre>").encode("utf-8")
            self._send(body, "text/html; charset=utf-8")
            return

        # ── K 線(只餵眼睛,不進決策)────────────────────────
        if u.path.startswith("/api/klines"):
            sym = q.get("symbol", [""])[0]
            iv = q.get("interval", ["15m"])[0]
            allowed = {"1m", "5m", "15m", "30m", "1h", "4h", "1d"}
            from portfolio.paper import SYMBOLS as _SY
            if sym not in _SY or iv not in allowed:
                # 白名單:只准查本系統實際持有的標的與已知週期,
                # 不讓面板變成任意轉發交易所請求的代理
                payload = {"error": f"不受理的參數 symbol={sym} interval={iv}"}
            else:
                payload = klines(sym, iv)
            self._send(json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")
            return

        # ── 一次性快照(SSE 不可用時的退路)──────────────────
        if u.path.startswith("/api/live"):
            try:
                from portfolio.live import snapshot
                payload = snapshot()
            except Exception as e:
                payload = {"error": f"{type(e).__name__}: {e}"}
            self._send(json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")
            return

        try:
            body = render().encode("utf-8")
        except Exception as e:
            body = (f"<pre>面板渲染失敗:{html.escape(type(e).__name__)}: "
                    f"{html.escape(str(e))}</pre>").encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass


def main() -> int:
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    srv.daemon_threads = True
    print(f"AGMCIS 交易台 · http://0.0.0.0:{PORT}")
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
