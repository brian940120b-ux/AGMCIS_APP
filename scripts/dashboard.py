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

# ⚠️ **這一行必須在任何 core / portfolio / exchange 的 import 之前。**
#
# `python scripts/dashboard.py` 的 sys.path[0] 是 `scripts/`,不是
# 根目錄 —— 沒有這一行,`from core import ...` 一定 ModuleNotFoundError。
#
# 2026-09-13:`from core import ratelimit` 被排到了這一行**上面**,
# 於是面板從那次 commit 起就再也起不來,systemd 重啟了 93 次。
# 而 331 條測試全綠 —— 因為 pytest 會自己把根目錄放進 sys.path,
# systemd 不會。**測試跑得起來,不代表服務跑得起來。**
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import interpreter, ratelimit

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

/* 版本戳。刻意放在標頭,因為它要回答的問題是「我看到的是新的嗎」
   —— 那個問題發生在看畫面的第一秒,不是捲到頁尾的時候。 */
.tag.build{font-size:10px;opacity:.72;letter-spacing:0}

/* 指令單。刻意做得像一張紙 —— 它是要照著按的東西,不是一份報表。
   單欄:在 iPhone 上要能一眼看完一張,不用左右捲。 */
.tickets{display:grid;gap:12px;margin-top:12px}
.ticket{border:1px solid var(--line);border-radius:12px;padding:12px 13px;
 background:var(--el)}
.ticket .t-head{display:flex;align-items:center;gap:10px;
 padding-bottom:9px;margin-bottom:4px;border-bottom:1px solid var(--line)}
.ticket .t-head .sym{font:700 17px var(--mono);letter-spacing:.4px}
.ticket table{width:100%;border-collapse:collapse}
.ticket td{padding:6px 0;vertical-align:top;font-size:13px}
.ticket td:first-child{color:var(--dim);width:88px;white-space:nowrap}
.ticket td:last-child{font-family:var(--mono);text-align:right}
.ticket .why{text-align:right}
.ticket button.cp{background:none;border:none;color:inherit;
 font:inherit;padding:0;cursor:pointer;text-align:right;
 display:inline-flex;align-items:center;gap:7px}
.ticket button.cp .cpi{font-size:10px;color:var(--dim);
 border:1px solid var(--line);border-radius:5px;padding:1px 5px}
.ticket button.cp.done .cpi{color:var(--up);
 border-color:var(--up-bd)}
.tlinks{display:flex;gap:8px;flex-wrap:wrap;margin-top:11px;
 padding-top:10px;border-top:1px solid var(--line)}
.tl{flex:1 1 auto;text-align:center;font-size:12px;
 padding:9px 10px;border-radius:9px;border:1px solid var(--line);
 background:var(--el2);color:var(--ink);text-decoration:none}

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
def _build():
    """這個行程跑的是哪一版。

    2026-09-13:執政官 pull 完看面板說「完全沒變化」,而面板每次請求
    都重新 render、`Cache-Control: no-store`,沒有任何快取 ——
    所以答案幾乎一定是「服務還在跑舊的程式碼」。

    **但「幾乎一定」不是「一定」**,而那張截圖分不出四種情況:
    沒 pull 到 / pull 了沒重啟 / 重啟失敗 / 真的換了但我改的有 bug。

    分不出來的時候兩邊都會開始用猜的,而那是一個來回好幾輪的死結。
    所以把版本印在畫面上 —— **commit 加上行程啟動時間**:
    版本號對了但啟動時間很舊,就是重啟沒生效。
    """
    from core.build import current
    return current(__file__)


def _ticket_card(t) -> str:
    """一張指令單的 HTML。

    鏡像單與對齊單共用這一個 —— 兩邊各寫一份的話,遲早有一邊
    少印停損。而那是這張卡上唯一不能少的東西。
    """
    warn = "".join(f'<div class="why">⚠️ {html.escape(w)}</div>'
                   for w in t.warnings)
    liq = (f'{t.est_liq_price:,.6g}' if t.est_liq_price is not None else '—')
    margin = (f'{t.est_margin:,.2f}' if t.est_margin is not None else '—')
    return (
            '<div class="ticket">'
        f'<div class="t-head"><span class="sym">'
        f'{html.escape(t.symbol)}</span>'
        f'<span class="pill {"p-buy" if t.action == "OPEN_LONG" else "p-sell"}">'
        f'{html.escape(t.tap)}</span></div>'
        '<table><tbody>'
        + "".join(
            f'<tr><td>{html.escape(label)}</td><td>'
            f'<button class="cp" data-v="{html.escape(value)}">'
            f'<b>{html.escape(value)}</b>{html.escape(unit)}'
            '<span class="cpi">複製</span></button>'
            + (f'<div class="why">{html.escape(note)}</div>'
               if note else '')
            + '</td></tr>'
            for label, value, note, unit in (
                ("數量", t.fields()[0][1], t.fields()[0][2], ""),
                ("槓桿", t.fields()[1][1], t.fields()[1][2], "×"),
                ("保證金", t.margin_mode, "", ""),
                ("停損", t.fields()[2][1], t.fields()[2][2], ""),
            ))
        +
        f'<tr><td>預估佔用</td><td>{margin} USDT</td></tr>'
        f'<tr><td>預估強平</td><td>{liq}'
        '<div class="why">我方算的,交易所這個產品不回</div></td></tr>'
        f'<tr><td>有效價格</td><td>{t.price_low:,.6g} ~ '
        f'{t.price_high:,.6g}<div class="why">跑出去就作廢,重算</div>'
        '</td></tr>'
        f'<tr><td>有效到</td><td>{html.escape(t.valid_until_utc)}</td></tr>'
        f'<tr><td>單號</td><td class="why">{html.escape(t.ticket_id)}</td>'
        '</tr>'
        '</tbody></table>'
        + '<div class="tlinks">' + "".join(
            f'<a class="tl" href="{html.escape(url)}">'
            f'{html.escape(label)}</a>'
            for label, url in t.links())
        + '</div>'
        + warn + '</div>')


def sizing_basis() -> str:
    """指令單的數量是照什麼算出來的 —— **一行,不是一張卡。**

    2026-09-18 執政官:「我現在只要標準合約的,其他的一律我不想看到。」

    原本這裡有一整張「部位大小的依據(紙上權益)」:權益、可用保證金、
    已實現、未實現、曝險、回撤、手續費、**資金費**…… 而那些是
    **永續**的成本模型算出來的,不是 U 本位標準合約的。

    整張拿掉。但**不能整個藏起來** —— 指令單上每一個數量都是拿這個
    權益乘出來的,看不到它就等於看不到「為什麼是這個量」。
    所以留一行,擺在它被用到的地方。
    """
    a = _json(DATA / "portfolio_account.json")
    if not a:
        return ('<div class="flag">數量依據:<b>還沒有記帳</b> —— '
                '沒有權益就算不出指令單的數量。每日 00:30 UTC 自動執行。'
                '</div>')
    eq = float(a.get("equity", 0) or 0)
    exposure = float(a.get("exposure", 0) or 0)
    return ('<div class="flag">數量依據:模擬帳戶權益 '
            f'<b>{eq:,.2f} USDT</b> × 曝險 {exposure:.0%} '
            f'× 槓桿 {float(a.get("leverage", 1)):g}×。<br>'
            '模擬帳戶用的是<b>永續</b>的成本模型(資金費、費率),'
            'U 本位標準合約的成本一個字都還沒驗證過 —— '
            '所以它算出來的<b>數量</b>可以用,它算出來的<b>損益</b>'
            '不是這個產品的績效。</div>')


def block_tickets() -> str:
    """指令單 —— **這一塊是整個面板現在最重要的東西。**

    2026-09-13:執政官選定 U 本位標準合約,而那個產品**沒有下單 API**
    (GET+POST 都問過,對照組證實)。所以整條鏈只有最後一吋是人做的,
    而那一吋就在這裡:系統把數量、槓桿、停損算好,人照著按。

    ⚠️ 這一塊不是「今日訂單」的另一種寫法。「今日訂單」是**紙上帳本**
    自己成交的那些;指令單是**要你真的去 App 按**的那些。兩個混在
    一起,遲早會有人以為紙上動了實際就動了。
    """
    def _compute():
        try:
            from exchange.bingx.trade import BACKSTOP_PCT
            from portfolio.paper import LEVERAGE_CAP, plan
            from portfolio.ticket import make_tickets
            p = plan()
            if p.get("error"):
                return {"error": p["error"]}
            if BACKSTOP_PCT is None:
                return {"error": "BACKSTOP_PCT 還沒有人決定(§102)"}
            made, refused = make_tickets(p, BACKSTOP_PCT, LEVERAGE_CAP)

            # ── 對齊單 ────────────────────────────────────
            # 上面那批是**鏡像**:模擬今天要換手什麼,就推什麼。
            # 但模擬幾天前就開好了倉,而真實帳戶可能是空的 ——
            # 鏡像只鏡像「從現在開始的變動」,所以真實帳戶永遠追不上。
            #
            # 「今天沒有要按的」在那個狀態下是真話,**也是誤導**。
            catch, catch_refused, catch_notes = [], [], []
            try:
                from exchange.bingx.standard import BingXStandardUSDT
                from portfolio.account import Account
                from portfolio.ticket import catch_up

                held = {sym: pos.position_amt
                        for sym, pos in Account.load().positions.items()
                        if abs(pos.position_amt) > 1e-12}
                catch, catch_refused, catch_notes = catch_up(
                    held, BingXStandardUSDT().rich_positions(),
                    p.get("prices") or {}, BACKSTOP_PCT, LEVERAGE_CAP,
                    strategy=str(getattr(p.get("cfg"), "strategy", "")),
                    signal_day=str(p.get("signal_day") or ""))
            except Exception as e:                   # noqa: BLE001
                # 對齊算不出來**不該讓整塊消失** —— 上面那批鏡像單
                # 是獨立的,它們照樣要印出來。
                catch_notes = [f"對齊單算不出來:{type(e).__name__}: {e} —— "
                               "**不代表兩邊一致**,只代表沒問到"]

            return {"made": made, "refused": refused, "plan": p,
                    "catch": catch, "catch_refused": catch_refused,
                    "catch_notes": catch_notes}
        except Exception as e:                       # noqa: BLE001
            return {"error": f"{type(e).__name__}: {e}"}

    got = _cached("tickets", 180, _compute)
    head = ('<div class="card"><h2>指令單 —— 要你自己按的</h2>'
            '<p class="note">U 本位標準合約<b>沒有下單 API</b>'
            '(2026-09-13 GET+POST 各問一次,對照組證實)。'
            '訊號、部位大小、風控、強平距離全部自動,'
            '<b>只有送單這一吋是手動的</b>。</p>'
            '<div class="flag">點數字就複製,點下面的連結開 BingX。<br>'
            '⚠️ <b>「連結把參數都填好、你只要按開單」做不到</b> —— '
            '不是我偷懶,是任何交易所都沒有這種連結。一條連結能決定'
            '一筆交易的方向、數量、槓桿,那是資安漏洞不是功能。<br>'
            'BingX 也沒有公開任何 deeplink 規格(2026-09-13 查過官方 API '
            '文件與支援中心)。所以下面三條連結是**候選**,'
            '<b>哪一條真的會開起 App 只有你點得出來</b> —— '
            '點一次告訴我哪條對,我把另外兩條拿掉。</div>'
            + sizing_basis())

    if got.get("error"):
        return (head + '<p class="note">算不出來:'
                f'{html.escape(str(got["error"]))}</p></div>')

    made = got.get("made") or []
    refused = got.get("refused") or []

    catch = got.get("catch") or []
    catch_refused = got.get("catch_refused") or []
    catch_notes = got.get("catch_notes") or []

    if not made and not refused and not catch and not catch_notes:
        return (head + '<p class="note">今天沒有要按的。目標配置與'
                '現有持倉一致,而且真實帳戶跟模擬對得上。</p></div>')

    cards = [_ticket_card(t) for t in made]

    body = '<div class="tickets">' + "".join(cards) + '</div>' if cards else ''

    if catch or catch_notes or catch_refused:
        body += ('<h3 class="sub">對齊單 —— 讓真實帳戶追上模擬</h3>'
                 '<div class="flag">上面那批是<b>鏡像</b>:模擬今天換手'
                 '什麼就推什麼。但模擬幾天前就開好了倉,而真實帳戶'
                 '可能是空的 —— 鏡像只鏡像「從現在開始的變動」。<br>'
                 '這一批是<b>一次性</b>的:按完之後就交給日常的鏡像。'
                 '</div>')
        if catch:
            body += ('<div class="tickets">'
                     + "".join(_ticket_card(t) for t in catch)
                     + '</div>')
        for note in catch_notes:
            body += f'<div class="flag">{html.escape(note)}</div>'
        for sym, why in catch_refused:
            body += (f'<div class="flag"><b>{html.escape(sym)}</b><br>'
                     f'{html.escape(why)}</div>')

    if refused:
        body += ('<h3 class="sub">開不出來的</h3>' + "".join(
            f'<div class="flag"><b>{html.escape(sym)}</b><br>'
            f'{html.escape(why)}</div>' for sym, why in refused))

    from portfolio.costs import standard_cost_caveat
    caveat = standard_cost_caveat()
    tail = (f'<div class="flag">⚠️ {html.escape(caveat)}</div>'
            if caveat else '')
    tail += gate_correlation()
    tail += ('<div class="flag">按完之後在 VPS 上跑 '
             '<code>scripts/ticket.py --verify</code> —— 它會拿交易所實際的'
             '持倉回頭比對數量、方向、槓桿、保證金模式。'
             '<b>手動下單的系統不知道自己送了什麼</b>,那一步是唯一的檢查。'
             '</div>')
    return head + body + tail + '</div>'


def gate_correlation() -> str:
    """相關性集中度(§60)—— **指令單的閘門,所以印在指令單裡。**

    2026-09-13:執政官要求面板只留 U 本位標準合約相關的東西,
    這一塊本來被我整個刪掉了 —— 而 `tests/test_correlation.py`
    當場擋下來,它的理由寫得比我的刪除好:

        「量到卻沒人看得到的數字,等於沒量。
          尤其這一條的門檻**還沒設定** —— 執政官要拿面板上的
          數字決定門檻。如果它只存在於 log,那個決定永遠不會發生。」

    所以搬,不刪。它不再佔一整張卡,而是變成指令單上的一行 ——
    位置更對:它本來就是在解釋**今天這些單為什麼是這個大小**。

    這張卡回答一個「總曝險」永遠不會回答的問題:
    **這幾個倉是幾個賭注,還是同一個賭注的幾個面?**
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
        return ('<div class="flag">風控閘 · 相關性集中度:'
                f'<b>算不出來</b> —— {why}<br>'
                '<b>沒有拿一個「假設不相關」的數字頂替。</b> '
                '在最危險的時候給最樂觀的答案,是這裡最貴的一種錯。'
                '</div>')

    return ('<div class="flag">風控閘 · 相關性集中度 —— '
            f'帳面 <b>{n} 檔 / {total:.1f}%</b>,把相關性算進去之後,'
            f'這個組合等於<b>一個 {equiv:.1f}% 的單一標的</b>'
            f'(有效 {enp:.1f} 檔,{c.get("observations")} 天共同觀測)。<br>'
            '總曝險只是加總,它不會告訴你這幾個倉是不是同一個賭注 —— '
            '加密貨幣的相關性在恐慌時往 1 靠攏,而那正是風控唯一真的'
            '重要的時候。<br>'
            '<b>上限尚未設定</b>,需執政官指定(§102)。在那之前這條'
            '每天照跑、照記錄,但不會擋單。</div>')


def block_exchange() -> str:
    """**交易所那邊真正的帳** —— U 本位標準合約。

    這一塊之前不在面板上,而它是這個產品最該看的東西:
    紙上帳本是一個模擬,`/openApi/contract/v1` 才是真相。

    ⚠️ 這個產品的回應**結構性地**少三格 —— liquidationPrice、
    markPrice、equity。強平價由我方算(線性合約,公式與 account.py
    同一條),而**那個估計偏樂觀**:MMR 取定值、未計維持保證金分層,
    實際強平會更近。看到數字就要看到它從哪來。
    """
    def _fetch():
        try:
            from core.config import load_env
            from exchange.bingx.private import Credentials, host, is_live
            from exchange.bingx.standard import BingXStandardUSDT
            load_env()
            creds = Credentials.from_env()
            ad = BingXStandardUSDT()
            return {"balance": ad.balance(), "positions": ad.rich_positions(),
                    "who": creds.masked(), "host": host(),
                    "live": is_live()}
        except Exception as e:                   # noqa: BLE001
            return {"error": f"{type(e).__name__}: {e}"}

    got = _cached("exchange", 60, _fetch)
    head = '<div class="card"><h2>交易所帳戶 —— U 本位標準合約</h2>'

    if got.get("error"):
        return (head + '<p class="note">問不到:'
                f'{html.escape(str(got["error"]))}</p>'
                '<div class="flag">問不到**不代表沒有倉** —— '
                '在弄清楚為什麼之前,不要把它當成「帳上是空的」。</div>'
                '</div>')

    where = ('<b>實盤</b>' if got.get("live") else 'Demo(VST)')
    out = (head + f'<p class="note">{where} · {html.escape(got["host"])}'
           f' · 金鑰 {html.escape(str(got["who"]))}</p>')

    rows = got.get("balance") or []
    if isinstance(rows, dict):
        rows = [rows]
    funded = [r for r in rows if isinstance(r, dict)
              and float(r.get("balance") or 0) > 0]
    if funded:
        cells = []
        for r in funded[:4]:
            cells.append(kv(str(r.get("asset") or "?"),
                            f'{float(r.get("balance") or 0):,.2f}'))
        out += f'<div class="grid">{"".join(cells)}</div>'
    else:
        out += '<p class="note">沒有任何幣種有餘額。</p>'

    positions = got.get("positions") or []
    if not positions:
        out += ('<p class="note">交易所端 <b>0 筆持倉</b>。</p>'
                '<div class="flag">所以<b>持倉欄位到今天還是沒有被驗證過</b> '
                '—— 補強平價那套邏輯還沒跑過真實樣本。'
                '第一個標準合約倉開出來的那一刻,這一塊會是第一個說話的。'
                '</div>')
    else:
        trs = []
        for pos in positions:
            dist = pos.liq_distance_pct()
            danger = dist is not None and dist < 20.0
            dist_txt = (f'{dist:.1f}%' if dist is not None
                        else '<span class="down">算不出來</span>')
            liq = (f'{pos.liq_price:,.6g}' if pos.liq_price is not None
                   else '—')
            src = ('交易所' if pos.liq_source == "exchange"
                   else '我方算·偏樂觀')
            trs.append(
                f'<tr><td class="sym">{html.escape(pos.symbol)}</td>'
                f'<td class="{"up" if pos.is_long else "down"}">'
                f'{"多" if pos.is_long else "空"}</td>'
                f'<td>{pos.qty:.8g}</td><td>{pos.entry:,.6g}</td>'
                f'<td>{pos.leverage:g}×</td>'
                f'<td>{liq}<div class="why">{src}</div></td>'
                f'<td class="{"down" if danger else ""}">{dist_txt}</td>'
                '</tr>')
        out += ('<div class="scroll"><table><thead><tr><th>商品</th>'
                '<th>方向</th><th>持倉量</th><th>開倉均價</th><th>槓桿</th>'
                '<th>強平價</th><th>距離</th></tr></thead><tbody>'
                + "".join(trs) + '</tbody></table></div>')
        if any((p.liq_distance_pct() or 99) < 20.0 for p in positions):
            out += ('<div class="flag">❗ 有部位離強平不到 20%(§19),'
                    '<b>而這個距離是偏樂觀的 —— 實際更近</b>。</div>')

    return out + '</div>'


def block_screen() -> str:
    """幣種篩選 —— **為什麼這個幣在裡面 / 不在裡面。**

    2026-09-13 執政官:「就像我傳的幣種單一樣能夠篩選幣種,畢竟是做合約。」

    這一塊的重點不是「哪幾個幣」,是**每一關的答案都看得見**。
    「為什麼這個幣不在裡面」必須是一個回答得出來的問題 ——
    而在合約上,答錯的代價是一張永遠不會成交的單,
    或一個流動性薄到強平時沒人接的倉。
    """
    def _screen():
        try:
            from exchange.bingx.standard import BingXStandardUSDT
            from portfolio import specs
            from portfolio.paper import SYMBOLS
            from portfolio.screen import evaluate
            from portfolio.universe import (MIN_DAILY_BARS,
                                            MIN_QUOTE_VOLUME_USDT)
            from portfolio.sim import load_daily

            volumes, bars, status = {}, {}, {}
            for sym in SYMBOLS:
                rows = load_daily(sym)
                bars[sym] = len(rows) or None
                if rows:
                    last = rows[-1]
                    volumes[sym] = last.v * last.c if last.v else None
                try:
                    status[sym] = specs.tradable(sym)
                except Exception:                # noqa: BLE001
                    pass                          # 查不到 -> 不知道

            known = BingXStandardUSDT().recognised(SYMBOLS)

            # 訊號也一起算 —— 原本它自成一張「決策變數」卡,
            # 於是手機上有**兩張幣種表**:一張說「能不能做」,
            # 一張說「現在該不該做」。同一批幣、兩個地方看,
            # 而它們排序還不一樣。併成一張。
            from portfolio.paper import VOL_LOOKBACK
            account = _json(DATA / "portfolio_account.json") or {}
            held = account.get("positions") or {}
            signal = {}
            for sym in SYMBOLS:
                c = closes(sym, 80)
                if len(c) < VOL_LOOKBACK + 1:
                    continue
                # c[-1] 是今天還沒收完的那根。訊號只能用**已收盤**的 ——
                # paper.plan() 用 dates[-2],這裡必須是同一把尺。
                px = c[-2]
                ma = sum(c[-(VOL_LOOKBACK + 1):-1]) / VOL_LOOKBACK
                signal[sym] = (px, ma, (px - ma) / ma * 100, sym in held)

            return {"rows": evaluate(SYMBOLS, volumes, bars, known,
                                     MIN_QUOTE_VOLUME_USDT, MIN_DAILY_BARS,
                                     status),
                    "signal": signal}
        except Exception as e:                   # noqa: BLE001
            return {"error": f"{type(e).__name__}: {e}"}

    got = _cached("screen", 300, _screen)
    head = ('<div class="card"><h2>幣種 —— 能不能做,以及現在該不該做</h2>'
            '<p class="note">系統的決策變數只有一個:'
            '<b>收盤價在不在 50 日均線之上</b>。在之上就持有、之下就空手,'
            '再由波動目標把整體規模調到年化 27%。'
            '「離均線」越接近 0,下一次換手越可能發生在那個幣上。</p>')
    if got.get("error"):
        return (head + '<p class="note">算不出來:'
                f'{html.escape(str(got["error"]))}</p></div>')

    from portfolio.screen import BLOCK, GATES, PASS, summary
    rows = got["rows"]
    tally = summary(rows)

    mark = {PASS: '<span class="up">✓</span>',
            BLOCK: '<span class="down">✗</span>'}
    signal = got.get("signal") or {}
    trs = []
    for c in rows:
        cells = "".join(f'<td>{mark.get(c.gates.get(g), "<span class=dim>?</span>")}</td>'
                        for g in GATES)
        sig = signal.get(c.symbol)
        if sig is None:
            gap_cell = '<td class="dim">?</td><td class="dim">—</td>'
        else:
            _, _, gap, held = sig
            gap_cell = (
                f'<td class="{tone(gap)}">{gap:+.2f}%</td>'
                f'<td><span class="pill {"p-hold" if held else "p-off"}">'
                f'{"持有" if held else "空手"}</span></td>')
        trs.append(
            f'<tr><td class="sym">{html.escape(c.app_symbol)}</td>'
            f'{cells}{gap_cell}'
            f'<td class="{"up" if c.tradable else "down"}">'
            f'{html.escape(c.verdict)}</td></tr>')

    heads = "".join(f'<th>{html.escape(g)}</th>' for g in GATES)
    return (head
            + '<div class="grid">'
            + kv("可以做", f'{tally["tradable"]}')
            + kv("擋下", f'{tally["blocked"]}')
            + kv("**不知道**", f'{tally["unknown"]}')
            + '</div>'
            + '<div class="scroll"><table><thead><tr><th>商品</th>'
            f'{heads}<th>離均線</th><th>訊號</th><th>結論</th>'
            '</tr></thead><tbody>'
            + "".join(trs) + '</tbody></table></div>'
            + '<div class="flag"><b>「不知道」不算通過。</b> '
              '一個因為沒問到而沒發現問題的檢查,如果回報通過,'
              '就是在說謊 —— 而在合約上那句謊話的代價是一張不會成交的單,'
              '或一個強平時沒人接的倉。<br>'
              '篩選**只用結構性條件**(流動性、歷史長度、交易所狀態),'
              '不用「漲最多的前 N 個」那種預測性條件 —— '
              '2026-09-08 實測過一次「按離均線距離取前一半」,'
              '<b>輸給等權持有全部</b>。<br>'
              '⚠️ 「標準合約認得」是 allOrders 問出來的,那是'
              '<b>必要條件不是充分條件</b>。完整名單只能在 App 上翻。'
              '</div></div>')


def block_proposals() -> str:
    """研究迴路的提案 —— **系統想改什麼,以及它憑什麼。**

    2026-09-13 執政官要系統「不斷經過多次模擬之後發現該怎麼調整」。
    這一塊是那件事的出口,而它的形狀刻意是「提案」不是「改動」:

    模擬只有一條歷史。系統試 32 種變化,一定會找到在那條歷史上最
    漂亮的一組 —— 而它漂亮的原因是**記住了過去**,不是理解了市場。
    所以每個提案都要帶著「試過幾次」與「校正後的 p 值」,
    而且**執政官點頭才生效**。
    """
    def _load():
        try:
            from portfolio.research import load
            return {"items": load()}
        except Exception as e:                   # noqa: BLE001
            return {"error": f"{type(e).__name__}: {e}"}

    got = _cached("proposals", 120, _load)
    head = '<div class="card"><h2>研究提案 —— 系統想改什麼</h2>'
    if got.get("error"):
        return (head + '<p class="note">讀不到:'
                f'{html.escape(str(got["error"]))}</p></div>')

    items = got.get("items") or []
    waiting = [p for p in items if p.status == "pending"]
    decided = [p for p in items if p.status != "pending"]

    intro = ('<p class="note">系統會不停地試,但<b>試出來的結果不會自己'
             '生效</b>。每個提案都帶著<b>試過幾次</b>與<b>多重比較校正後'
             '的 p 值</b> —— 試 32 次,其中一次看起來贏幾乎是必然的,'
             '沒有那兩個數字,「找到更好的了」這句話沒有意義。</p>')

    if not items:
        return (head + intro
                + '<p class="note">目前沒有提案。在 VPS 上跑 '
                  '<code>scripts/research.py</code> 產生。</p>'
                + '<div class="flag">沒有提案<b>是好消息不是壞消息</b> ——'
                  '它代表現任參數在整個網格裡站得住。</div></div>')

    cards = []
    for pr in waiting:
        c = pr.challenger
        rows = "".join(
            f'<tr><td class="{"up" if ok else "down"}">'
            f'{"✓" if ok else "✗"}</td>'
            f'<td class="sym">{html.escape(name)}</td>'
            f'<td><div class="why">{html.escape(detail)}</div></td></tr>'
            for name, ok, detail in pr.checks)
        cards.append(
            '<div class="ticket">'
            f'<div class="t-head"><span class="sym">'
            f'{html.escape(str(c.get("ma")))} 日均線 · '
            f'{html.escape(str(c.get("vol_target_pct")))}% · '
            f'{html.escape(str(c.get("leverage_cap")))}×</span></div>'
            '<table><tbody>'
            f'<tr><td>驗證段 Calmar</td><td>{pr.test.get("calmar")}'
            f'<div class="why">現任 {pr.incumbent_test.get("calmar")}'
            f'(優勢 {pr.calmar_edge})</div></td></tr>'
            f'<tr><td>試過</td><td>{pr.trials} 種</td></tr>'
            f'<tr><td>校正後 p</td><td>{pr.p_corrected}'
            f'<div class="why">原始 {pr.p_raw} × {pr.trials}</div></td></tr>'
            f'<tr><td>編號</td><td class="why">'
            f'{html.escape(pr.proposal_id)}</td></tr>'
            '</tbody></table>'
            '<div class="scroll"><table><tbody>' + rows + '</tbody></table>'
            '</div>'
            '<div class="why">裁決:<code>scripts/decide.py '
            f'{html.escape(pr.proposal_id)} --accept</code></div>'
            '</div>')

    body = (f'<div class="tickets">{"".join(cards)}</div>' if cards else
            '<p class="note">沒有待裁決的提案。</p>')

    if decided:
        body += ('<h3 class="sub">裁決過的</h3><div class="scroll">'
                 '<table><tbody>' + "".join(
                     f'<tr><td class="{"up" if p.status == "accepted" else "dim"}">'
                     f'{"核可" if p.status == "accepted" else "駁回"}</td>'
                     f'<td class="why">{html.escape(p.proposal_id)}</td>'
                     f'<td class="why">{html.escape(str(p.decided_on or ""))}'
                     f' {html.escape(str(p.note or ""))}</td></tr>'
                     for p in decided[:8]) + '</tbody></table></div>')

    return (head + intro + body
            + '<div class="flag"><b>核可 ≠ 生效。</b> 裁決只記錄'
              '「執政官說可以」;真的改常數是另一個動作,由人執行 ——'
              '改風控參數不該是一個腳本的副作用(§102)。<br>'
              '兩步分開,事後才看得出「誰決定的」而不只是「參數是這個」。'
              '</div></div>')


def block_gaps() -> str:
    """還沒關掉的洞 —— **把它放在面板上,不是放在某份文件裡。**

    2026-09-13 測試組死了 73.7 小時沒人管,而巡檢每 10 分鐘就報一次。
    那次的教訓不是「要多報一點」,是**沒有被看見的警告等於不存在**。
    """
    from exchange.bingx.trade import BACKSTOP_DECISION, BACKSTOP_PCT
    from portfolio.costs import STANDARD_COSTS_VERIFIED

    rows = []

    def gap(ok: bool, name: str, detail: str) -> None:
        rows.append(
            f'<tr><td class="{"up" if ok else "down"}">'
            f'{"✓" if ok else "✗"}</td>'
            f'<td class="sym">{html.escape(name)}</td>'
            f'<td><div class="why">{detail}</div></td></tr>')

    gap(BACKSTOP_PCT is not None, "後備停損",
        (f'<b>{BACKSTOP_PCT}%</b> —— '
         f'{html.escape(str(BACKSTOP_DECISION["decided_by"]))} '
         f'{html.escape(str(BACKSTOP_DECISION["decided_on"]))} 裁定(§102)。'
         '20% 是分水嶺:從那裡開始被掃出去的部位沒有一段最後是賺的;'
         '選 25% 是因為這是<b>災難後備</b>,20% 每二十次進場就碰一次,太常。'
         if BACKSTOP_PCT is not None else
         '<b>還沒有人決定</b>。指令單開不出來 —— '
         '一張沒有停損的單不該存在(§19)。'))

    gap(False, "交易池",
        'U 本位標準合約<b>沒有 contracts 端點</b>,程式問不到有哪些幣'
        '可以交易。<code>allOrders</code> 認得 SOL / BNB / AAVE / UNI,'
        '但那是<b>必要條件,不是充分條件</b>(它可能拿全交易所的代號表'
        '驗證)。<b>只能在 App 上把那份清單翻到底。</b>'
        '若真的缺,交易池要改,而回測的 Calmar 1.33 就不是這個池子的'
        '數字了,要重跑(§6)。')

    gap(STANDARD_COSTS_VERIFIED, "成本",
        '手續費、資金費、滑點<b>一個字都沒驗證過</b>。'
        'costs.py 的每個數字都是量<b>永續</b>量出來的 —— '
        '不要因為「都是 BingX、都是 U 本位」就沿用,'
        '那正是舊系統十一次「兩把尺」的形狀。'
        '<code>allOrders</code> 的 cumQuote 與 executedQty 比對得出實際'
        '成交價,有幾筆標準合約成交就量得出來。')

    live = _json(DATA / "portfolio_contract.json") or {}
    passed, total = live.get("passed", 0), live.get("total", 8)
    gap(passed >= total, "實盤資格",
        f'<b>{passed}/{total} 條</b>通過。'
        '這是「什麼時候可以開始用真錢」的閘門 —— '
        f'{html.escape(str(live.get("verdict") or "尚未評估"))}<br>'
        '門檻在乾淨樣本為零時寫下,<b>不得事後放寬</b>。'
        '八條全過也只是「有資格談」。')

    gap(False, "持倉欄位",
        '這個產品的持倉回應<b>沒有 liquidationPrice 也沒有 markPrice</b>'
        '(官方欄位表確認),餘額<b>沒有 equity</b>。'
        '強平價由我方算(線性合約,公式與 account.py 同一條)—— '
        '但那個估計<b>偏樂觀</b>:MMR 取定值、未計維持保證金分層,'
        '實際強平會更近。而且目前 0 筆標準合約持倉,'
        '<b>這套補值邏輯到今天還沒有跑過真實樣本</b>。')

    return ('<div class="card"><h2>還沒關掉的洞</h2>'
            '<div class="scroll"><table><tbody>' + "".join(rows)
            + '</tbody></table></div>'
            '<div class="flag">2026-09-13:測試組死了 73.7 小時沒人管,'
            '而巡檢每 10 分鐘就報一次。那次的教訓不是「要多報一點」,是'
            '<b>沒有被看見的警告等於不存在</b> —— 所以這一塊在面板上,'
            '不在某份文件裡。</div></div>')


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
            '<div class="flag ok">執行場所 <b>PAPER(模擬金)</b>。<br>'
            'U 本位標準合約<b>沒有下單 API</b>(2026-09-13 GET+POST 各問'
            '一次,對照組證實)—— 所以送單是手動的,'
            '<b>這不是設定,是這個產品的性質</b>。<br>'
            '而任何**可能**送出訂單的程式路徑仍然被三道鎖擋著:'
            '① <code>LIVE_ENABLED=False</code> 是原始碼常數,不是設定選項 —— '
            '要開必須改碼、commit、部署;② 實盤資格契約八條;'
            '③ 人工簽署,永不自動化。</div></div>')


# ══════════════════════════════════════════════════════════
# 頁面
# ══════════════════════════════════════════════════════════
def render() -> str:
    now = datetime.now(timezone.utc)
    # 2026-09-13 執政官:「我想專注在 U 本位標準合約,其他不要,
    # 所以只想要相關的面板就好。」
    #
    # 砍掉的:權益曲線、相關性、事件日曆、策略監控、紙上今日訂單、
    # 紙上持倉。**它們背後的邏輯沒有被關掉** —— 相關性與事件日曆
    # 仍然是部位大小的閘門,只是不再佔畫面。砍的是顯示,不是風控。
    #
    # 順序照「要不要動手」排:
    #   指令單(要按)→ 交易所實際(真相)→ 訊號(為什麼)
    #   → 部位大小的依據(數量從哪來)
    # 2026-09-18:只留 U 本位標準合約的東西。
    # 紙上帳本縮成指令單裡的一行(它決定數量,但它是永續的成本模型);
    # 實盤資格縮成「還沒關掉的洞」的一行。
    desk = block_tickets() + block_exchange() + block_screen()
    system = block_gaps() + block_proposals() + block_system()
    return f"""<!doctype html><html lang="zh-Hant"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="theme-color" content="#08090d">
<title>AGMCIS 交易台</title><style>{CSS}</style></head><body>
<div class="wrap">
<header><h1>AGMCIS</h1>
  <span class="tag paper">PAPER · 模擬金</span>
  <span class="tag">U 本位標準合約 · 手動送單</span>
  <span class="tag">{now:%m-%d %H:%M} UTC</span>
  <span class="tag build">{html.escape(_build().describe())}</span>
</header>
<nav>
  <a href="#" class="on" data-t="desk">交易台</a>
  <a href="#" data-t="system">系統</a>
</nav>
<div class="tabpane on" id="desk">{desk}</div>
<div class="tabpane" id="system">{system}</div>
<footer>版本 {html.escape(_build().describe())} —— 這一行在,
就代表你看到的是這個行程真的跑的那一版<br>紙上帳本非真錢 · 每日 00:30 UTC 記帳 · U 本位標準合約沒有下單 API,實際送單由人在 App 完成</footer>
</div>
<script>
// 點數字就複製。手機上少按幾下,而**不會少一次確認** ——
// 貼上之後那個數字還是在你眼前。
document.querySelectorAll('button.cp').forEach(function(b){{
  b.addEventListener('click', function(){{
    var v = b.dataset.v || '';
    function done(){{
      b.classList.add('done');
      var i = b.querySelector('.cpi');
      if(i){{ i.textContent = '已複製'; }}
      setTimeout(function(){{
        b.classList.remove('done');
        if(i){{ i.textContent = '複製'; }}
      }}, 1600);
    }}
    if(navigator.clipboard && navigator.clipboard.writeText){{
      navigator.clipboard.writeText(v).then(done, fallback);
    }} else {{ fallback(); }}
    function fallback(){{
      // http 或舊瀏覽器沒有 clipboard API —— 退回選取,
      // 不要靜靜什麼都沒發生
      var ta = document.createElement('textarea');
      ta.value = v; ta.style.position='fixed'; ta.style.opacity='0';
      document.body.appendChild(ta); ta.select();
      try {{ document.execCommand('copy'); done(); }}
      catch(e) {{
        var i = b.querySelector('.cpi');
        if(i){{ i.textContent = '複製不了,長按數字'; }}
      }}
      document.body.removeChild(ta);
    }}
  }});
}});

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
            # 版本戳一律附上,連不健康的時候也是 —— 排查的第一個問題
            # 永遠是「這台跑的是哪一版」,而那時候通常沒有金鑰在手上。
            if isinstance(payload, dict):
                payload["build"] = _build().to_dict()
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
    build = _build()
    try:
        srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    except OSError as e:
        # ⚠️ 2026-09-13:這是整輪「面板換不掉」最可能的原因,而它
        #    原本只會噴一行 OSError。
        #
        #    埠被別人占著 -> 新的行程當場死掉 -> **而舊的還在服務**。
        #    systemd 的 Type=simple 在 fork 出來那一刻就報 active,
        #    所以 `systemctl restart` 看起來成功、`is-active` 說
        #    active、瀏覽器也照常有畫面 —— 只是那個畫面是舊的。
        #
        #    一個「看起來成功的失敗」是最貴的一種。所以講清楚。
        print(f"✗ 綁不上 {PORT} 埠:{e}", file=sys.stderr)
        print("", file=sys.stderr)
        print("  這幾乎一定表示**已經有另一個行程占著這個埠**。", file=sys.stderr)
        print("  重點:舊的那個會繼續服務,所以外面看起來一切正常,",
              file=sys.stderr)
        print("  而你看到的畫面永遠是舊版的。", file=sys.stderr)
        print("", file=sys.stderr)
        print("  查是誰:  .venv/bin/python scripts/why_old.py",
              file=sys.stderr)
        print(f"  或:      ss -lptn 'sport = :{PORT}'", file=sys.stderr)
        return 2

    srv.daemon_threads = True
    # 版本印在啟動日誌裡 —— journalctl 第一行就看得到這個行程是哪一版。
    print(f"AGMCIS 交易台 · http://0.0.0.0:{PORT} · {build.describe()}")
    sys.stdout.flush()
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
