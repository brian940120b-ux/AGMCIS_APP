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
import time
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
/* 過期的指令單 —— 撤掉,但不消失 */
.ticket.gone{opacity:.5}
.ticket.gone .cp{pointer-events:none;text-decoration:line-through;
 opacity:.6}
.ticket.gone .tlinks,.ticket.gone .why-box{display:none}
.pill.p-dead{background:var(--down-bg);border-color:var(--down-bd);
 color:var(--down)}
.tdead{margin:10px 0 2px}
.tleft{font-weight:700}
.tleft.soon{color:var(--down)}
/* ══ 現在正不正常 ══════════════════════════════════════════
   2026-09-20 執政官:「我只想看得到數據,不想要一堆文字,
   然後確認現在是否正常。」一個大字 + 幾個燈,其餘往下看。 */
.status{border:1px solid var(--line);border-radius:14px;
 padding:13px 14px;margin-bottom:12px;background:var(--card)}
.status.s-ok{border-color:var(--up-bd);background:var(--up-bg)}
.status.s-warn{border-color:var(--down-bd)}
.status.s-bad{border-color:var(--down-bd);background:var(--down-bg)}
.s-word{font-size:26px;font-weight:800;letter-spacing:2px;
 line-height:1.1;margin-bottom:9px}
.s-ok .s-word{color:var(--up)}
.s-warn .s-word,.s-bad .s-word{color:var(--down)}
.chips{display:flex;flex-wrap:wrap;gap:6px}
.chip{font-size:11px;padding:3px 8px;border-radius:999px;
 border:1px solid var(--line);color:var(--dim);white-space:nowrap}
.chip b{font-family:var(--mono);margin-left:5px;font-weight:600}
.chip.c-ok b{color:var(--up)}
.chip.c-bad{border-color:var(--down-bd)}
.chip.c-bad b{color:var(--down)}
/* 說明摺起來 —— 平常一行,要看才點開 */
.ex{margin:8px 0 2px}
.ex>summary{cursor:pointer;color:var(--dim);font-size:11px;
 letter-spacing:.5px;list-style:none;padding:2px 0}
.ex>summary::-webkit-details-marker{display:none}
.ex>summary::before{content:"▸ ";font-size:10px}
.ex[open]>summary::before{content:"▾ "}
.ex .note{margin-top:4px}
/* 區塊小標 —— 一張卡裡分「此刻」與「這套好不好」兩段 */
.sect-h{color:var(--dim);font-size:11px;letter-spacing:.6px;
 margin:14px 0 8px;padding-top:11px;border-top:1px solid var(--line)}
/* 數字換掉的時候閃一下 —— 沒有這個,「即時」在畫面上看不出來 */
@keyframes tickflash{from{background:var(--up-bg)}to{background:transparent}}
.tick{animation:tickflash .7s ease-out;border-radius:5px}
/* 模擬持倉表 —— 手機上五欄,所以字要小、數字要對齊 */
.pos{width:100%;border-collapse:collapse;margin-top:10px;
 font-size:12.5px}
.pos th{color:var(--dim);font-weight:600;text-align:right;
 padding:5px 0 7px;font-size:11px;letter-spacing:.3px;
 border-bottom:1px solid var(--line)}
.pos th:first-child{text-align:left}
.pos td{padding:8px 0;text-align:right;font-family:var(--mono);
 border-bottom:1px solid var(--line)}
.pos td:first-child{text-align:left;font-family:inherit}
.pos tr:last-child td{border-bottom:none}
.pos .sym{font-weight:600}
.pos .why{font-family:inherit}
.ticket td.sect{color:var(--dim);font-size:10.5px;
 letter-spacing:.5px;padding-top:9px;width:auto;
 border-top:1px solid var(--line)}
/* 為什麼是這一單。一張說不出理由的單不該被按下去。 */
.why-box{margin-top:11px;padding-top:10px;
 border-top:1px solid var(--line)}
.why-h{font-size:11px;color:var(--dim);margin-bottom:6px;
 letter-spacing:.5px}
.why-row{display:flex;gap:10px;font-size:12px;
 line-height:1.7;padding:2px 0}
.why-row span:first-child{color:var(--dim);flex:0 0 76px}
.why-row span:last-child{flex:1;text-align:right}
.ticket button.cp{background:none;border:none;color:inherit;
 font:inherit;padding:0;cursor:pointer;text-align:right;
 display:inline-flex;align-items:center;gap:7px}
.ticket button.cp .cpi{font-size:10px;color:var(--dim);
 border:1px solid var(--line);border-radius:5px;padding:1px 5px}
.ticket button.cp.done .cpi{color:var(--up);
 border-color:var(--up-bd)}
.tlinks{display:flex;gap:8px;flex-wrap:wrap;margin-top:11px;
 padding-top:10px;border-top:1px solid var(--line)}
.tl-n{display:block;font-size:10px;color:var(--dim);
 margin-top:3px;font-weight:400}
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


def _md_bold(text: str) -> str:
    """把 **粗體** 轉成 <b>、`<br>` 留成換行,其餘一律跳脫。

    2026-09-18:幣種卡上印出了字面的 `**不知道**` —— 星號沒有人轉,
    就這樣進了 HTML。小,但它出現在**最需要被看見的那個字**上。
    """
    parts = str(text).split("**")
    out = "".join(html.escape(x) if i % 2 == 0 else f"<b>{html.escape(x)}</b>"
                  for i, x in enumerate(parts))
    # 換行是這些說明文字裡唯一還需要的標籤。白名單一個,其餘照跳脫 ——
    # 開放整個 HTML 只為了換行,不划算。
    return out.replace("&lt;br&gt;", "<br>")


def kv(label: str, value: str, cls: str = "") -> str:
    # 標籤走 _md_bold;值維持原樣,因為有呼叫端傳的是 HTML 片段。
    return (f'<div class="kv"><div class="l">{_md_bold(label)}</div>'
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


def _ticket_card(t, now=None) -> str:
    """一張指令單的 HTML。

    鏡像單與對齊單共用這一個 —— 兩邊各寫一份的話,遲早有一邊
    少印停損。而那是這張卡上唯一不能少的東西。
    """
    warn = "".join(f'<div class="why">⚠️ {_md_bold(w)}</div>'
                   for w in t.warnings)
    liq = (f'{t.est_liq_price:,.6g}' if t.est_liq_price is not None else '—')
    margin = (f'{t.est_margin:,.2f}' if t.est_margin is not None else '—')
    # ── 過期就撤掉 ────────────────────────────────────
    # 2026-09-18 執政官:「如果訊號單有效期限過了就撤掉。」
    #
    # 撤掉 = **不能再按**,不是「消失」。一張安靜不見的單會讓畫面
    # 變成「今天沒有要按的」,而那是一句假話 —— 實際上是「有,但
    # 窗口關了」。這兩件事的處置完全不同:前者什麼都不用做,
    # 後者要等下一張重算出來。
    #
    # 伺服器這邊每 180 秒重算一次,所以這裡通常不會看到過期的;
    # 真正會過期的是**開著沒關的頁面** —— TTL 是 30 分鐘,手機擺著
    # 半小時就死了,而畫面上跟活的一模一樣。所以前端也有一份計時器
    # (simExpire),時間到就地撤掉,不等重新整理。
    # now 可注入 —— 不然這張卡只能拿真實時鐘測,而「過期」這件事
    # 正是要測的東西。生產上照樣是 None = 現在。
    dead = t.expired_at(now)
    cls = "ticket gone" if dead else "ticket"
    # 價格帶帶進 DOM —— executable()/price_in_band() 寫好了,但**面板
    # 從來沒呼叫過**(2026-09-18 查出來,這是「寫好沒接上」的第四次)。
    # 一張照自己規則早該作廢的單還能按,那條規則等於不存在。
    return (
        f'<div class="{cls}" '
        f'data-until="{html.escape(t.valid_until_utc)}" '
        f'data-sym="{html.escape(t.symbol)}" '
        f'data-lo="{t.price_low:.10g}" data-hi="{t.price_high:.10g}">'
        f'<div class="t-head"><span class="sym">'
        f'{html.escape(t.symbol)}</span>'
        + ('<span class="pill p-dead">已過期 · 不要按</span>' if dead else
           f'<span class="pill '
           f'{"p-buy" if t.action == "OPEN_LONG" else "p-sell"}">'
           f'{html.escape(t.tap)}</span>')
        + '</div>'
        + ('<div class="flag warn tdead">這張單的有效期限過了 —— '
           '<b>不要照它按</b>。數量與止損是照當時的價格算的,'
           '價格走掉之後那個數量代表的風險就不是原本那個了。<br>'
           '系統會重算;訊號還在、價格還在帶內的話,'
           '幾分鐘內會再推一張新的(單號會不一樣)。</div>'
           if dead else '')
        + '<table><tbody>'
        + '<tr><td colspan="2" class="sect">要填的(順序照 App)</td></tr>'
        + "".join(
            f'<tr><td>{html.escape(label)}</td><td>'
            f'<button class="cp" data-v="{html.escape(value)}">'
            f'<b>{html.escape(value)}</b>'
            '<span class="cpi">複製</span></button>'
            # 說明走 _md_bold,不是 html.escape —— 那些字串裡有
            # **粗體** 標記,跳脫掉就會印出字面的星號(2026-09-18
            # 在幣種卡上發生過一次,這裡是同一個錯的第二處)。
            + (f'<div class="why">{_md_bold(note)}</div>'
               if note else '')
            + '</td></tr>'
            for label, value, note in t.fields())
        # 填完之後用來核對的 —— 不是拿來填的
        + '<tr><td colspan="2" class="sect">填完之後畫面上應該是</td></tr>'
        + "".join(
            f'<tr><td>{html.escape(label)}</td><td>{html.escape(value)}'
            + (f'<div class="why">{_md_bold(note)}</div>' if note else '')
            + '</td></tr>'
            for label, value, note in t.verify_after())
        # ── 這一單打算在哪裡結束 ──────────────────────
        # 2026-09-18 執政官:「我希望還有出場價。」
        # 出場價是**策略的**出場(跌破均線 / 跌破 N 日低),止損是
        # 機器死掉時的後備。兩個放一起才看得出哪一條會先到。
        + '<tr><td colspan="2" class="sect">打算在哪裡結束</td></tr>'
        + "".join(
            f'<tr><td>{_md_bold(label)}</td><td>{html.escape(value)}'
            + (f'<div class="why">{_md_bold(note)}</div>' if note else '')
            + '</td></tr>'
            for label, value, note in t.exit_plan())
        +
        f'<tr><td>有效價格</td><td>{t.price_low:,.6g} ~ '
        f'{t.price_high:,.6g}'
        '<div class="why">交易所現價 <span class="tnow">—</span>'
        ' —— 跑出這個範圍就作廢,等下一張</div>'
        '</td></tr>'
        # 手機上讀 UTC 字串沒有意義 —— 要的是「還剩多久」。
        # 那個倒數由前端每秒更新(伺服器算的只是第一幀)。
        f'<tr><td>有效到</td><td><span class="tleft">—</span>'
        f'<div class="why">{html.escape(t.valid_until_utc)} UTC</div>'
        '</td></tr>'
        f'<tr><td>單號</td><td class="why">{html.escape(t.ticket_id)}</td>'
        '</tr>'
        '</tbody></table>'
        # ── 為什麼要按這一單 ─────────────────────────────
        # 2026-09-18 執政官:「我希望他能夠給我為什麼開單,理由是什麼。」
        # 一張說不出理由的單不該被按下去 —— 那等於把判斷外包給一個
        # 你看不見的東西,而虧錢的時候你連哪裡想錯了都查不出來。
        + '<div class="why-box"><div class="why-h">為什麼是這一單</div>'
        + "".join(
            f'<div class="why-row"><span>{html.escape(k)}</span>'
            f'<span>{v}</span></div>' for k, v in t.why())
        + '</div>'
        + '<div class="tlinks">' + "".join(
            f'<a class="tl" href="{html.escape(url)}">'
            f'{html.escape(label)}'
            + (f'<span class="tl-n">{_md_bold(note)}</span>'
               if note else '')
            + '</a>'
            for label, url, note in t.links())
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


def status_checks() -> list:
    """一眼判斷「現在正不正常」的那幾項。回 [(名稱, ok, 一句話)]。

    2026-09-20 執政官:「面板上我只想看得到數據,不想要一堆文字,
    然後幫我確認現在是否正常。」

    ⚠️ **每一項都要是「量得到」的。** 一個永遠綠燈的狀態列比沒有狀態列
    危險 —— 它讓上面每一層都變綠,而綠的理由是它什麼都沒在看。
    所以這裡不放「系統健康」這種抽象項目,只放有實際量測來源的。
    """
    out = []

    # 一、跑的版本是不是磁碟上的版本
    b = _build()
    out.append(("版本", not b.stale,
                "已是最新" if not b.stale else "pull 了但沒重啟"))

    # 二、兩組模擬有沒有在記帳
    try:
        from portfolio.paper import CONTROL, PRIMARY
        from portfolio.scorecard import score
        for label, cfg in (("系統", PRIMARY), ("對照組", CONTROL)):
            sc = score(cfg.curve_path)
            out.append((label, bool(sc.days) and sc.running,
                        f"{sc.days} 天" if sc.running else
                        ("沒記過帳" if not sc.days else "停了")))
    except Exception as e:                           # noqa: BLE001
        out.append(("模擬", False, f"問不到:{type(e).__name__}"))

    # 三、行情:串流還是退路,以及多舊
    try:
        got = sim_snapshot()
        src = str(got.get("source") or "?")
        age = got.get("age_s")
        # 「無持倉」不是故障:空手是一個合法的部位,而且策略常常空手。
        # 把它算成紅燈,狀態列就會在最正常的時候喊異常。
        ok = src.startswith("串流") or src == "無持倉"
        out.append(("行情", ok,
                    src + (f" · {age:.0f}s" if age is not None
                           and src != "無持倉" else "")))
    except Exception as e:                           # noqa: BLE001
        out.append(("行情", False, f"問不到:{type(e).__name__}"))

    # 四、日線資料最舊幾小時(記帳的原料)
    try:
        from portfolio.paper import SYMBOLS
        ages = [a for s in SYMBOLS
                if (a := age_min(HIST / f"{s}_1d.csv")) is not None]
        if ages:
            h = max(ages) / 60
            out.append(("日線", h <= 36, f"{h:.0f} 小時前"))
        else:
            out.append(("日線", False, "沒有快取"))
    except Exception as e:                           # noqa: BLE001
        out.append(("日線", False, f"問不到:{type(e).__name__}"))

    # 五、交易所帳戶問得到嗎
    #
    # ⚠️ **只讀快取,不要用 _cached 去讀。** 第一版寫成
    # `_cached("exchange", 60, lambda: {"error": "未取"})` —— 快取冷的
    # 時候那個 lambda 會被呼叫,把一個**假的錯誤**寫進共用的快取鍵,
    # 而 block_exchange 接著就會拿到它,整張卡變成「問不到」。
    # 一個狀態檢查把它檢查的東西弄壞,是最糟的一種檢查。
    hit = _CACHE.get("exchange")
    if not hit:
        out.append(("交易所", True, "還沒查"))
    else:
        err = (hit[1] or {}).get("error")
        out.append(("交易所", not err, "問得到" if not err else "問不到"))

    # 六、**警報送得出去嗎**
    #
    # 2026-09-25:PRIMARY 的記帳停了 133 小時,而檢修官每 10 分鐘就
    # 報一次「交易所一致性徹查未通過」。那個警報一次都沒有送到 ——
    # 旁邊同一份 log 寫著 `Telegram 回應 401`。
    #
    # **警鈴一直在響,而電話線是斷的。**
    #
    # 這件事有先天的循環:通知管道壞掉的時候,它不可能用自己來通知你。
    # 面板是另一條獨立的管道,所以這一格放在這裡,循環才斷得掉。
    try:
        from notify.telegram import is_configured, last_delivery
        d = last_delivery()
        if not d:
            # 沒有紀錄 ≠ 正常。有設定卻從來沒送過,那就是還沒證明它會通。
            out.append(("通知", not is_configured(),
                        "未設定(不送)" if not is_configured()
                        else "設定了但從沒送成功過"))
        else:
            hrs = (time.time() - float(d.get("at") or 0)) / 3600
            out.append(("通知", bool(d.get("ok")),
                        ("正常" if d.get("ok") else str(d.get("why") or "失敗"))
                        + f" · {hrs:.0f}h 前"))
    except Exception as e:                           # noqa: BLE001
        out.append(("通知", False, f"問不到:{type(e).__name__}"))

    return out


def block_status() -> str:
    """最上面那一條:**現在正不正常。**

    只有一個大字 + 幾個燈。要細節的人往下看,不要細節的人看一眼就走。
    """
    checks = status_checks()
    bad = [c for c in checks if not c[1]]
    word = "正常" if not bad else ("注意" if len(bad) <= 1 else "異常")
    cls = "ok" if not bad else ("warn" if len(bad) <= 1 else "bad")

    chips = "".join(
        f'<span class="chip {"c-ok" if ok else "c-bad"}">'
        f'{html.escape(name)}<b>{html.escape(detail)}</b></span>'
        for name, ok, detail in checks)
    return (f'<div class="status s-{cls}">'
            f'<div class="s-word">{word}</div>'
            f'<div class="chips">{chips}</div></div>')


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
            from portfolio.paper import LEVERAGE_CAP, PRIMARY, plan
            from portfolio.ticket import make_tickets
            # **指令單從 PRIMARY 出。** 2026-09-20 起 PRIMARY 是動態
            # 篩選那一組(成交額前 10),不再是七個寫死的幣。
            p = plan(cfg=PRIMARY)
            if p.get("error"):
                return {"error": p["error"]}
            if BACKSTOP_PCT is None:
                return {"error": "BACKSTOP_PCT 還沒有人決定(§102)"}
            # **用現價報價。** plan() 給的是成交日那根日線的開盤價
            # (回測的節奏),而人是現在在按的 —— 兩者最多差 24 小時,
            # 而本金/數量/止損全部照報價算。2026-09-18 執政官:
            # 「價格跟交易所不一樣啊。」
            try:
                _, live_marks, _, _, _ = sim_marks()
            except Exception:                        # noqa: BLE001
                live_marks = {}
            made, refused = make_tickets(p, BACKSTOP_PCT, LEVERAGE_CAP,
                                         marks=live_marks)

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

                # ⚠️ 2026-09-19:交易所的持倉查詢回空,**不代表對方是空的**。
                # 實測 allPosition 回 [] 而 App 上有 3 筆倉,餘額裡有
                # 60,703.67 被逐倉鎖住。對齊單的前提正是「我知道對方
                # 有什麼」—— 前提不成立就**不准出單**,否則它會叫人去
                # 開一個他已經持有的倉,而那是實實在在的雙倍曝險。
                from exchange.bingx.standard_usdt import positions_are_hidden
                ex_pos = BingXStandardUSDT().rich_positions()
                bal = BingXStandardUSDT().balance()
                hidden, why = positions_are_hidden(bal, ex_pos)
                if hidden:
                    raise RuntimeError(
                        "交易所持倉查詢回空,但餘額顯示有部位鎖著保證金"
                        f"({why})。**看不到對方有什麼就不能算對齊** —— "
                        "硬算會叫你去開一個你已經持有的倉。")

                held = {sym: pos.position_amt
                        for sym, pos in Account.load(
                            PRIMARY.state_path).positions.items()
                        if abs(pos.position_amt) > 1e-12}
                catch, catch_refused, catch_notes = catch_up(
                    held, ex_pos,
                    p.get("prices") or {}, BACKSTOP_PCT, LEVERAGE_CAP,
                    strategy=str(getattr(p.get("cfg"), "strategy", "")),
                    signal_day=str(p.get("signal_day") or ""),
                    exits=p.get("exits") or {})
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
            '<div class="flag">欄位順序照 <b>BingX 標準合約開單畫面</b>,'
            '點數字就複製。<b>數量、交易總額、保證金三個都給</b> —— '
            'App 讓你填哪一格就用哪一個,不用自己在手機上乘除。<br>'
            '<b>「開 App」用的是 <code>bingbon://</code></b> —— '
            'BingX 的前身是 Bingbon,而 App 的 URL scheme 沒跟著改名。'
            '我原本猜 <code>bingx://</code>,三條全打不開;'
            '執政官分享了一條真的連結才看出來。<br>'
            '⚠️ <b>連結只帶幣種,不帶產品別。</b> App 連結的參數只有 '
            'coinName / valuationCoinName / marginCoinName —— '
            '沒有任何一個欄位說這是標準合約還是永續。'
            '所以它能做的只有「把 App 開到這個幣」,'
            '<b>選哪個產品是 App 決定的</b>。<br>'
            '<b>按之前先確認分頁是「U 本位標準合約」</b> —— '
            '這張單的數量是照標準合約算的,下到永續上就是一張算錯的單,'
            '而它會成交。</div>'
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
                '<div class="flag">問不到<b>不代表沒有倉</b> —— '
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
        # ⚠️ 2026-09-19:這裡原本寫「交易所端 **0 筆持倉**」。
        #
        # 那是一句假話。同一時刻執政官的 App 上是「持倉 (3)」
        # (BTCUSDT / BNBUSDT / 第三筆,未實現 +88.35),而
        # allPosition 回的是空的。
        #
        # 而且是最危險的那一種假話:**對齊單會據此叫人去開一個他
        # 已經持有的倉。** 系統不是不知道,是它以為自己知道。
        #
        # 空清單只能講一件事:**我問到的是空的。** 至於那代表
        # 「沒有倉」還是「這個端點不給我看」,在 probe_positions_raw.py
        # 跑出結果之前,我們**不知道**。
        from exchange.bingx.standard_usdt import positions_are_hidden
        hidden, why = positions_are_hidden(got.get("balance"), positions)
        if hidden:
            # **有倉,而且算得出鎖了多少** —— 只是看不到是哪幾檔。
            out += ('<p class="note">交易所端持倉查詢回了<b>空清單</b>,'
                    '但餘額說<b>有倉</b>。</p>'
                    '<div class="flag warn">⚠️ <b>有部位,而這個查詢看不到'
                    '它們。</b><br>'
                    f'{html.escape(why)}<br>'
                    '2026-09-19 實測:<code>allPosition</code> 不帶參數、'
                    '帶 <code>BTCUSDT</code>、帶 <code>BNBUSDT</code>、'
                    '帶 <code>BTC-USDT</code> 全部回 <code>[]</code>,'
                    '而同一把金鑰的 <code>balance</code> 是通的 —— '
                    '<b>不是權限、不是簽章、不是代號格式</b>。<br>'
                    '算得出來的是<b>被鎖住多少保證金</b>;'
                    '算不出來的是<b>哪幾檔、多少量、有沒有止損</b>。<br>'
                    '所以<b>對齊單已經停掉</b> —— 看不到對方有什麼就不能'
                    '算對齊,硬算會叫你去開一個你已經持有的倉。</div>')
        else:
            out += ('<p class="note">交易所端持倉查詢回了<b>空清單</b>,'
                    '而餘額裡也沒有被鎖住的保證金。</p>'
                    '<div class="flag">兩邊一致,所以這次的空清單'
                    '<b>可以</b>當成「沒有倉」。<br>'
                    '⚠️ 但 <code>allPosition</code> 這個端點本身'
                    '2026-09-19 被證實會漏報(App 有 3 筆而它回空),'
                    '所以這個結論靠的是<b>餘額那一側的佐證</b>,'
                    '不是它自己說了算。</div>')
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


def all_prices() -> dict:
    """**一次**拿回全部幣種的現價。回 {代號: (價格, 交易所的時間戳毫秒)}。

    ═══ 為什麼是一次拿全部 ═══
    2026-09-18 執政官問「能做到即時更新嗎」。原本每一輪要替每個幣
    各打一次 K 線,7 個幣 5 秒一輪 = 84 次/分,而全系統的速率預算是
    2 次/秒 = 120 次/分 —— 光面板就吃掉七成,日常記帳、巡檢、
    交易所查詢全部要跟它搶。

    這個端點一次回 1041 個幣,一輪只要 **1 次**。5 秒一輪 = 12 次/分。

    ═══ 端點的形狀是量出來的,不是猜的 ═══
    `scripts/probe_price_feed.py` 2026-09-18 在 VPS 上實跑:
      GET /openApi/swap/v2/quote/price(不帶 symbol)
      -> code 0,data 是 1041 筆的陣列,{symbol, price, time}
      -> 我們那七個幣全部在裡面
    帶 symbol 的對照組回的是**物件**,證明「不帶 symbol = 全部」
    是這個端點真的支援的行為,不是我讀錯了什麼。

    ⚠️ 這是永續(swap)的行情,不是標準合約的。標準合約沒有公開
    行情端點,兩個產品追同一個現貨,貼得很近但不是同一個數字 ——
    **只餵眼睛,不做對帳**。
    """
    def _fetch():
        import urllib.request
        url = "https://open-api.bingx.com/openApi/swap/v2/quote/price"
        try:
            with ratelimit.urlopen(url, timeout=8) as r:
                d = json.loads(r.read().decode("utf-8"))
        except Exception as e:                       # noqa: BLE001
            return {"error": f"{type(e).__name__}: {e}"}
        if str(d.get("code")) != "0":
            return {"error": f"BingX code={d.get('code')} {d.get('msg')}"}
        rows = d.get("data")
        if not isinstance(rows, list):
            # 形狀變了就說形狀變了 —— 不要假裝拿到了價格。
            return {"error": f"data 不是陣列,是 {type(rows).__name__}"}
        out = {}
        for r in rows:
            try:
                out[r["symbol"]] = (float(r["price"]), int(r.get("time") or 0))
            except (KeyError, TypeError, ValueError):
                continue
        return {"px": out}
    return _cached("allpx", 4, _fetch)


def sim_marks() -> tuple:
    """模擬帳戶要的那幾檔的現價。
    回 (帳戶, {代號: 價格}, 問不到的, 最舊幾秒, 來源)。

    ═══ 兩層,順序不可顛倒(2026-09-18)═══
    一、**WebSocket 串流** —— 交易所逐筆推播(實測約 10 筆/秒)。
        真正的即時。portfolio/stream.py 2026-09-08 就寫好了,
        連 /api/stream 這個 SSE 端點都在,**而這張卡沒有用它**。
        又是一次「寫好了但沒接上」。
    二、REST 一次拿全部 —— 只在串流還沒連上或斷線時用。

    `portfolio.live.prices()` 已經是這兩層,而且斷線時**不會拿最後
    一次的價格假裝是即時的**(超過 20 秒沒更新就當那一檔過期)。
    一條靜止不動卻標著「即時」的價格,比沒有價格危險。
    """
    import time as _t
    from portfolio.account import Account
    from portfolio.paper import PRIMARY
    # **PRIMARY 的帳本**,不是預設的那本。少了這個參數,面板會顯示
    # 對照組的持倉,而指令單來自 PRIMARY —— 畫面上就會出現
    # 「叫你買 X,而持倉列裡沒有 X」,比完全沒切換更難查。
    a = Account.load(PRIMARY.state_path)
    # 持倉 ∪ 基準籃子。**基準籃子不能漏** —— 策略空手的時候,
    # 「不交易的話現在是賺是賠」正是最該看到的那個數字。
    want = sorted({s for s, pos in a.positions.items()
                   if abs(pos.position_amt) > 1e-12}
                  | set(a.bench_start or {}))
    if not want:
        return a, {}, [], None, "無持倉"

    # ── 一、串流 ──────────────────────────────────
    try:
        from portfolio.live import prices as stream_prices
        px = stream_prices(want) or {}
    except Exception:                                # noqa: BLE001
        px = {}
    if px and len(px) == len(want):
        # 年齡要**量**,不要寫 0.0。逐筆推播不代表每一筆都剛剛到:
        # 冷門幣可能好幾秒沒成交,那時候寫 0.0 就是在騙人 ——
        # 而這張卡上正好有一句「說得出年齡的才叫即時」。
        try:
            from portfolio.stream import ages as _ages
            got_ages = _ages(want) or {}
        except Exception:                            # noqa: BLE001
            got_ages = {}
        age = max(got_ages.values()) if got_ages else None
        return a, {s: float(v) for s, v in px.items()}, [], age, "串流"

    # ── 二、REST 退路 ─────────────────────────────
    got = all_prices()
    if got.get("error"):
        # 串流拿到一部分、REST 又掛了 —— 有多少報多少,
        # 缺的列出來。**半套的數字要說它是半套的。**
        miss = [s for s in want if s not in px]
        return (a, {s: float(v) for s, v in px.items()}, miss, None,
                f"REST 失敗({got['error']})")
    rest = got["px"]
    marks, missing, ages = dict(px), [], []
    now_ms = _t.time() * 1000
    for sym in want:
        if sym in marks:
            continue
        hit = rest.get(sym)
        if hit is None:
            missing.append(sym)
            continue
        marks[sym] = hit[0]
        if hit[1]:
            ages.append((now_ms - hit[1]) / 1000.0)
    src = "串流+REST" if px else "REST"
    return (a, {s: float(v) for s, v in marks.items()}, missing,
            (max(ages) if ages else 0.0), src)


def sim_snapshot() -> dict:
    """面板與 /api/sim 共用的那一份資料。**只有這一份。**

    2026-09-18 執政官:「數字對不上,我只要一個。」
    對不上是因為以前有兩個來源:成績單讀每日記帳的檔案(00:30 的
    收盤價),持倉卡用現在的即時價。修法不是挑一個顯示,是讓所有
    「現在」的數字**出自同一組價格**,而那就是這個函式。
    """
    def _compute():
        try:
            from portfolio.scorecard import live, score
            a, marks, _, age, src = sim_marks()
            lv = live(a, marks)
            from portfolio.paper import PRIMARY
            return {"live": lv, "card": score(PRIMARY.curve_path),
                    "age_s": age,
                    "source": src, "marks": marks}
        except Exception as e:                       # noqa: BLE001
            return {"error": f"{type(e).__name__}: {e}"}
    # 1 秒。串流是逐筆推的,快取放 15 秒等於把即時壓回 15 秒一跳。
    return _cached("sim", 1, _compute)


def sim_payload() -> dict:
    """SSE 與 /api/sim 共用的那一包。**只有這一份。**

    各組各的話,推播看到的數字與輪詢看到的會慢慢分開 ——
    而那正是執政官說的「數字對不上」。
    """
    try:
        got = sim_snapshot()
        if got.get("error"):
            return {"error": got["error"]}
        lv = got["live"]
        return {"at": lv.at, "age_s": got.get("age_s"),
                "source": got.get("source"),
                "equity": lv.equity, "return_pct": lv.return_pct,
                "excess_pct": lv.excess_pct,
                "unrealized": lv.unrealized_pnl,
                "realized": lv.realized_pnl,
                # 指令單的價格帶檢查要用同一組價格 —— 卡上顯示的現價
                # 與判斷作廢的現價必須是同一個,不然會出現「顯示在帶內
                # 卻被撤掉」這種說不清楚的狀況。
                #
                # ⚠️ 用 marks 不是 lv.legs:legs 只有**持倉中**的幣,
                # 而需要價格帶檢查的正是**還沒開的那些**。
                # 用 legs 的話,開倉單一張都檢查不到 —— 而那剛好是
                # 唯一會因為價格跑掉而害人填錯規模的一種單。
                "marks": {k.replace("-", ""): v
                          for k, v in (got.get("marks") or {}).items()},
                "rows": _sim_rows(lv)}
    except Exception as e:                           # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}


def _strategy() -> str:
    """現役策略的名字。**不要把它寫死在文字裡** —— 換了策略之後,
    寫死的那句話會變成一段看起來很權威的假話。"""
    try:
        from portfolio.paper import STRATEGY
        return str(STRATEGY)
    except Exception:                                # noqa: BLE001
        return "現役策略"


def _n(v, spec: str = ",.2f") -> str:
    return "—" if v is None else format(v, spec)


def block_sim() -> str:
    """模擬帳戶 —— 系統自己在跑的那一份。**面板上唯一的績效來源。**

    2026-09-18 執政官:「我只要一個,就是系統自行模擬的資訊,
    並且我想看到即時的盈虧數字變化。」

    所以這一張把原本的「模擬持倉」與「成績單」合成一張:

      · 上半:**此刻**的權益 / 報酬 / 贏基準 / 未實現 —— 每 15 秒
        自己更新,全部出自同一組即時價,不會互相對不上。
      · 中間:逐檔的盈虧,同一組價格。
      · 下半:這套行不行的裁決,以及只有帳本才知道的東西
        (最大回撤、走完幾次進出、記了幾天)—— **標明是到上一次
        記帳為止的**,不假裝即時。
    """
    got = sim_snapshot()
    head = ('<div class="card"><h2>模擬帳戶 —— 系統自己在跑的</h2>')
    if got.get("error"):
        return (head + '<div class="flag warn">算不出來:'
                + html.escape(got["error"]) + '</div></div>')

    lv, s = got["live"], got["card"]
    from portfolio.scorecard import BAD, BLOCKED, GOOD, UNKNOWN

    note = ('<p class="note">10,000 USDT 模擬金,<b>系統自己下單、'
            '自己記帳</b>,不需要你動手 —— 你要動手的是上面的指令單。'
            '現價走永續公開行情(標準合約沒有公開行情端點),'
            '<b>只餵眼睛,不做對帳</b>。</p>'
            # 2026-09-18 執政官:「除了做多有做空嗎?」
            # 現在的答案是沒有 —— 而這件事不寫在畫面上的話,
            # 看到「多」這個標籤的人會以為那是這一筆的方向,
            # 而不是**系統唯一做得出來的方向**。
            f'<div class="flag">現役策略(<b>{html.escape(_strategy())}</b>)'
            '<b>只做多</b>:條件成立才持有,不成立就<b>空手</b>,不做空。<br>'
            '多空版(跌破做空)程式碼 2026-09-08 就寫好了,'
            '但一直沒有接上任何地方 —— 2026-09-18 把它接進研究迴路'
            '<b>當挑戰者</b>,要贏過現任才會換。做空改變的是風險的'
            '形狀,不是只多一個方向,所以它走跟別人一樣的閘。</div>')

    # ── 此刻。id 讓 JS 每 15 秒換掉裡面的數字 ───────────
    live_cells = (
        f'<div class="kv"><div class="l">權益</div>'
        f'<div class="v" id="s-eq">{_n(lv.equity)}</div></div>'
        f'<div class="kv"><div class="l">報酬</div>'
        f'<div class="v {tone(lv.return_pct or 0)}" id="s-ret">'
        f'{_n(lv.return_pct, "+.2f")}%</div></div>'
        f'<div class="kv"><div class="l">贏基準</div>'
        f'<div class="v {tone(lv.excess_pct or 0)}" id="s-exc">'
        f'{_n(lv.excess_pct, "+.2f")}%</div></div>'
        f'<div class="kv"><div class="l">未實現</div>'
        f'<div class="v {tone(lv.unrealized_pnl)}" id="s-unr">'
        f'{lv.unrealized_pnl:+,.2f}</div></div>'
        f'<div class="kv"><div class="l">已實現</div>'
        f'<div class="v {tone(lv.realized_pnl)}" id="s-rea">'
        f'{lv.realized_pnl:+,.2f}</div></div>')
    out = [head, note,
           '<div class="sect-h">此刻 · <span id="s-at">連線中…'
           '</span></div>',
           # 5 秒一輪,一輪 1 次請求(all_prices 一次拿回全部幣種),
           # = 12 次/分,而全系統預算是 120 次/分。原本每個幣各打一次
           # K 線,5 秒一輪要 84 次/分,所以才卡在 15 秒。
           # 端點的形狀是 scripts/probe_price_feed.py 在 VPS 實跑量到的,
           # 不是我猜的。
           f'<div class="grid">{live_cells}</div>']

    # ── 逐檔 ──────────────────────────────────────
    if not lv.legs:
        from portfolio.paper import STRATEGY, SYMBOLS
        out.append('<div class="flag">模擬帳戶目前<b>空手</b> —— '
                   f'{len(SYMBOLS)} 個幣都不符合'
                   f'「{html.escape(STRATEGY)}」,'
                   '或波動目標把規模壓到零。'
                   '空手是一個部位,不是沒在跑。</div>')
    else:
        out.append('<table class="pos"><thead><tr><th>幣種</th><th>數量</th>'
                   '<th>開倉均價</th><th>現價</th><th>未實現</th></tr></thead>'
                   '<tbody id="s-legs">' + _sim_rows(lv) + '</tbody></table>')
    if lv.missing:
        out.append('<div class="flag warn">問不到現價的:<b>'
                   + html.escape("、".join(lv.missing))
                   + '</b> —— 這幾檔<b>完全沒有</b>算進上面的合計。'
                   '合計因此是偏少的,不是完整的。</div>')

    # ── 這套行不行 ────────────────────────────────
    tone_of = {GOOD: "ok", BAD: "warn", BLOCKED: "warn", UNKNOWN: ""}
    out.append('<div class="sect-h">這套到底好不好</div>')
    out.append(f'<div class="flag {tone_of.get(s.verdict, "")}">'
               f'<b>{html.escape(s.verdict)}</b><br>'
               + "<br>".join(_md_bold(b) for b in s.because) + '</div>')

    # 只有帳本才知道的 —— **標明它們不是即時的**
    hist = [kv("最大回撤", f"{s.max_dd_pct:.1f}%" if s.max_dd_pct is not None
               else "—", "down" if (s.max_dd_pct or 0) > 15.0 else ""),
            kv("走完的進出", f"{s.round_trips} 次"),
            kv("記帳天數", f"{s.days} 天"),
            kv("模擬在跑", "是" if s.running else "<b>停了</b>",
               "up" if s.running else "down")]
    out.append(f'<div class="grid">{"".join(hist)}</div>')
    out.append('<p class="note">上面這四格來自<b>每日記帳的帳本</b>,'
               f'算到 {html.escape(s.last_day or "—")} 為止 —— '
               '它們不是即時的,也不該是:回撤與進出次數本來就是'
               '一段時間累積出來的東西。</p>')

    out.append(_cohort_row())

    if s.backtest:
        train, test, test_dd, as_of = s.backtest
        f = lambda v: "—" if v is None else f"{float(v):.2f}"
        out.append(
            '<div class="flag"><b>目前唯一有統計意義的證據是回測,'
            '不是上面那幾個數字。</b><br>'
            f'現任策略 訓練段 Calmar <b>{f(train)}</b> · '
            f'驗證段 Calmar <b>{f(test)}</b>'
            + (f' · 驗證段回撤 <b>{float(test_dd):.1f}%</b>'
               if test_dd is not None else '')
            + f'(研究迴路 {html.escape(str(as_of))} 記的)<br>'
            '<b>看驗證段那個。</b> 訓練段是挑出這組參數的那一段,'
            '它一定好看 —— 挑的時候就是照著它挑的。'
            '驗證段是這組參數沒看過的資料,只有它算數。</div>')
    else:
        out.append('<div class="flag warn">還沒有回測證據 —— '
                   '<code>data/proposals.json</code> 裡沒有現任的'
                   '訓練/驗證數字。跑一次 '
                   '<code>python scripts/research.py</code>。</div>')
    return "".join(out) + '</div>'


def _cohort_row() -> str:
    """對照組(固定七幣)跟現在這套系統的對照。

    ⚠️ 2026-09-20 方向翻過來了:動態篩選那一組**變成系統本身**
    (PRIMARY),七個寫死的幣退成對照組。所以這一段印的是對照組。

    對照組存在的唯一理由:沒有它,「動態篩選比較好」就是一句**沒有
    辦法否證**的話。兩組同一份 plan()/tick()、同一段真實價格、
    同樣 10,000 起始金,唯一的差別是交易池。
    """
    def _compute():
        try:
            from portfolio.account import Account
            from portfolio.paper import CONTROL
            from portfolio.scorecard import live, score
            card = score(CONTROL.curve_path)
            a = Account.load(CONTROL.state_path)
            marks = all_prices().get("px") or {}
            lv = live(a, {k: v[0] for k, v in marks.items()
                          if k in a.positions or k in (a.bench_start or {})})
            return {"card": card, "live": lv, "pool": len(a.positions)}
        except Exception as e:                       # noqa: BLE001
            return {"error": f"{type(e).__name__}: {e}"}

    got = _cached("cohort", 60, _compute)
    head = ('<div class="sect-h">對照組 · 固定七幣</div>')
    if got.get("error"):
        return (head + '<div class="flag">算不出來:'
                + html.escape(got["error"]) + '</div>')

    card, lv = got["card"], got["live"]
    if not card.days:
        return (head + '<div class="flag">對照組還沒記過帳。</div>')

    # ── 兩組必須從**同一天**起算 ──────────────────────
    # 2026-09-20:主城 12 天 +4.24%(從 09-08),測試組 1 天 -0.03%
    # (從 09-19)。並排放著,任何人都會去比 —— 而比出來的東西沒有
    # 意義:主城那 4.24% 裡有 11 天是測試組還不存在的時候賺的。
    #
    # 不能靠重開主城來對齊(那會毀掉 12 天的前向樣本,而前向樣本正是
    # 這整套東西唯一沒有後見之明的證據)。所以兩邊都從後開始的那一組
    # 的起點重新起算。
    from portfolio.paper import CONTROL, PRIMARY
    from portfolio.scorecard import since
    start, main_pct, cohort_pct, n = since(CONTROL.curve_path,
                                           PRIMARY.curve_path)

    # 重疊天數做成一格數字,而不是只寫在下面那段字裡 —— 這是這組
    # 對照**唯一會自己長大**的東西,執政官要看得到它在動。
    cells = [kv("對照組在倉", f"{got['pool']} 檔"),
             kv("記帳天數", f"{card.days} 天"),
             kv("走完的進出", f"{card.round_trips} 次"),
             kv("兩組重疊", f"{n} 天")]
    out = [head, f'<div class="grid">{"".join(cells)}</div>']

    if main_pct is None or cohort_pct is None:
        # **不顯示一個看起來能比的數字。** 重疊不到兩天就沒有「期間」
        # 可言,那時候唯一誠實的話是「還沒得比」。
        out.append('<div class="flag">⚠️ <b>還沒得比。</b> 兩組的起算日'
                   '不同(對照組 09-08、系統 09-19),重疊'
                   f'{n} 天 —— 不足以算出同期間的報酬。<br>'
                   '在那之前<b>不會</b>並排顯示兩個報酬:主城的數字裡'
                   '有一段是測試組還不存在的時候賺的,擺在一起比,'
                   '比的是起跑時間,不是選幣。</div>')
    else:
        gap = cohort_pct - main_pct
        out.append(
            f'<div class="grid">'
            + kv("對照組 · 同期間", f"{main_pct:+.2f}%", tone(main_pct))
            + kv("系統 · 同期間", f"{cohort_pct:+.2f}%", tone(cohort_pct))
            + kv("差", f"{gap:+.2f}%", tone(gap))
            + '</div>'
            f'<p class="note">兩邊都從 <b>{html.escape(start)}</b> 起算'
            f'(重疊 {n} 天)—— 那是測試組開始記帳的日子。'
            '主城全期的報酬更高,但那裡面有一段是測試組還不存在的時候'
            '賺的,不能算進這個比較。</p>')

    out.append('<p class="note">兩組<b>唯一的差別是交易池</b>:系統每天'
               '從全市場篩出成交額前 10,對照組是七個寫死的幣。策略、'
               '波動目標、槓桿、回看期完全相同,所以差異只能來自選幣。<br>'
               '對照組存在的唯一理由是<b>讓「動態篩選比較好」這句話'
               '可以被否證</b>。<br>'
               '⚠️ <b>幾天的資料比不出高下。</b></p>')
    return "".join(out)


def _sim_rows(lv) -> str:
    """逐檔那幾列。JS 更新時原封不動換掉這一段。"""
    rows = []
    for sym, g in lv.legs.items():
        long_ = g["qty"] > 0
        cells = [
            f'<td class="sym">{html.escape(sym.replace("-", ""))}'
            f'<span class="pill {"p-buy" if long_ else "p-sell"}">'
            f'{"多" if long_ else "空"}</span></td>',
            f'<td>{abs(g["qty"]):,.6g}</td>',
            f'<td>{g["avg"]:,.6g}</td>',
        ]
        if g["mark"] is None:
            cells.append('<td colspan="2" class="why">問不到現價 —— '
                         '<b>不是 0,是不知道</b></td>')
        else:
            cells.append(f'<td>{g["mark"]:,.6g}</td>')
            roi = ('' if g["roi"] is None
                   else f'<div class="why">{g["roi"]:+.1f}% 本金</div>')
            cells.append(f'<td class="{tone(g["pnl"])}">'
                         f'{g["pnl"]:+,.2f}{roi}</td>')
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return "".join(rows)


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
    # 2026-09-19:這段原本寫死「50 日均線」與「27%」。策略換掉之後
    # 它會繼續這樣講,而那是一段看起來很權威的假話。改成讀現役設定。
    from portfolio.paper import STRATEGY, VOL_TARGET_ANNUAL_PCT
    head = ('<div class="card"><h2>幣種 —— 能不能做,以及現在該不該做</h2>'
            '<p class="note">系統的決策變數只有一個:'
            f'<b>{html.escape(STRATEGY)}</b>。條件成立就持有、不成立就空手,'
            f'再由波動目標把整體規模調到年化 {VOL_TARGET_ANNUAL_PCT:g}%。'
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
              '篩選<b>只用結構性條件</b>(流動性、歷史長度、交易所狀態),'
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

    # 2026-09-19 反查時發現的,而它是目前最大的一個洞:
    # 指令單上最重的一句話沒有任何東西在驗證。
    gap(False, "交易所持倉看不見",
        '<code>allPosition</code> 回空,而餘額顯示有部位鎖著保證金。'
        '2026-09-19 實測:不帶參數 / 帶 <code>BTCUSDT</code> / 帶 '
        '<code>BNBUSDT</code> / 帶 <code>BTC-USDT</code> 全部回 '
        '<code>[]</code>,同一把金鑰的 <code>balance</code> 卻是通的 —— '
        '<b>不是權限、不是簽章、不是代號格式</b>。<br>'
        '同一時刻 App 上是「持倉 (3)」,持倉保證金 60,703.67,'
        '而 餘額 120,776.01 − 全倉 60,072.34 = <b>60,703.67</b>,'
        '分毫不差。所以<b>倉存不存在算得出來</b>,'
        '但<b>哪幾檔、多少量、有沒有止損,看不到</b>。<br>'
        '後果:<b>對齊單已經停掉</b>(看不到對方有什麼就不能算對齊),'
        '而「這個倉有沒有止損」也因此驗不了 —— 止損那一欄'
        '本來就在同一筆持倉資料裡。<b>兩個洞是同一個。</b>')

    gap(False, "事件日曆只是記錄,不是閘門",
        '<code>paper._events_on()</code> 把成交日當天的事件寫進帳本'
        '那一行,<b>就只有這樣</b> —— 它自己的註解寫著「不影響記帳」。'
        '而面板的註解一直寫著「相關性與事件日曆仍然是部位大小的閘門」,'
        '2026-09-19 反查才發現那句話對事件日曆是假的(相關性是真的,'
        '它餵進 RiskEngine)。<br>'
        '把「有記錄」講成「有閘門」,是這個專案最常犯的那種錯。'
        '要不要讓它真的擋單(例如大事件前不進場、或減碼)是'
        '<b>策略層的決定</b>,要走研究迴路,不是改個字就算數。')

    gap(False, "止損有沒有真的設",
        '指令單說「止損一定要設 —— 這是機器死掉時唯一的保護」,'
        '而<b>系統從來沒有檢查過那件事有沒有發生</b>。<br>'
        '<code>StandardPosition</code> 這個型別裡根本沒有止損欄位,'
        '因為沒有人確認過交易所的唯讀回應給不給。'
        '<code>trade.unprotected()</code> 寫好了(docstring 自稱'
        '「實盤最重要的一條巡檢」)卻<b>沒有任何地方呼叫它</b>。<br>'
        '唯一看過的一筆真倉(2026-09-18 的 AAVE)止損就是 <code>--</code>,'
        '而面板一聲都沒吭 —— 因為它看不到。<br>'
        '2026-09-19 查到根因:止盈止損在 App 上是<b>附在持倉那一筆</b>的'
        '(「檢視止盈止損 (2)」,而「當前委託」是 0,所以它們不是掛單)。'
        '也就是說 —— <b>只要持倉查得到,止損很可能就在同一筆裡</b>。'
        '而持倉現在查不到(見上一條)。<b>兩個洞是同一個,先解持倉。</b>')

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
            '而任何<b>可能</b>送出訂單的程式路徑仍然被三道鎖擋著:'
            '① <code>LIVE_ENABLED=False</code> 是原始碼常數,不是設定選項 —— '
            '要開必須改碼、commit、部署;② 實盤資格契約八條;'
            '③ 人工簽署,永不自動化。</div></div>')


# ══════════════════════════════════════════════════════════
# 頁面
# ══════════════════════════════════════════════════════════
def _collapse_prose(page: str) -> str:
    """把說明文字摺進「說明」裡。**警語不摺。**

    2026-09-20 執政官:「面板上我只想看得到數據,不想要一堆文字。」

    他是對的 —— 那些長段落是我寫給自己看的推理過程,不是他要的東西。
    但**不能直接刪**:一個把「未驗證」講成「驗證過」的面板,乾淨而且
    在騙人。所以折衷是**摺起來**,一行「說明」點開就有,平常不佔畫面。

    ⚠️ `.flag.warn` 與 `.flag.ok` **不摺** —— 那些是「有東西不對」與
    「這件事確認過了」,它們本來就該一眼看到。摺掉警告等於關掉警告。
    """
    import re

    def wrap(m):
        body = m.group(2)
        # 太短的不值得摺(摺完反而多一行)
        plain = re.sub(r"<[^>]+>", "", body)
        if len(plain) <= 40:
            return m.group(0)
        return (f'<details class="ex"><summary>說明</summary>'
                f'<{m.group(1)} class="note">{body}</{m.group(1)}></details>')

    page = re.sub(r'<(p) class="note">(.*?)</p>', wrap, page, flags=re.S)
    # 只摺沒有 warn / ok 的 flag
    page = re.sub(r'<(div) class="flag">(.*?)</div>', wrap, page, flags=re.S)
    return page


def render() -> str:
    now = datetime.now(timezone.utc)
    # 2026-09-13 執政官:「我想專注在 U 本位標準合約,其他不要,
    # 所以只想要相關的面板就好。」
    #
    # 砍掉的:權益曲線、相關性、事件日曆、策略監控、紙上今日訂單、
    # 紙上持倉。
    #
    # ⚠️ 2026-09-19 反查更正:這裡原本寫「相關性與**事件日曆**仍然是
    # 部位大小的閘門」。相關性是(correlation.daily_returns 餵進
    # RiskEngine),**事件日曆不是** —— paper._events_on() 只把當天的
    # 事件**記進帳本那一行**,它自己的註解就寫著「不影響記帳」。
    #
    # 把「有記錄」講成「有閘門」,是這個專案最常犯的那種錯:
    # 一個看起來存在、實際無作用的東西,比沒有更糟。
    # 要不要讓事件日曆真的擋單,是**策略層的決定**,不是改個字。
    #
    # 順序照「要不要動手」排:
    #   指令單(要按)→ 交易所實際(真相)→ 訊號(為什麼)
    #   → 部位大小的依據(數量從哪來)
    # 2026-09-18:只留 U 本位標準合約的東西。
    # 紙上帳本縮成指令單裡的一行(它決定數量,但它是永續的成本模型);
    # 實盤資格縮成「還沒關掉的洞」的一行。
    # 順序照「我現在要做什麼」由上而下排(2026-09-18 執政官要求重排):
    #   ① 指令單      要你動手的,永遠第一
    #   ② 模擬持倉    系統抱著什麼 —— 指令單就是從這裡推出來的
    #   ③ 交易所帳戶  你實際抱著什麼 —— ②和③的差距就是①
    #   ④ 成績單      這套到底行不行
    #   ⑤ 幣種        為什麼是這些幣
    # ②③相鄰是刻意的:模擬與真實的差距是這個系統最容易出事的地方,
    # 隔著別的卡片看,那個差距就不會被注意到。
    # 順序照「我現在要做什麼」由上而下(2026-09-18):
    #   ① 指令單     要你動手的,永遠第一
    #   ② 模擬帳戶   系統自己在跑的 —— 指令單就是從這裡推出來的
    #   ③ 交易所帳戶 你實際有什麼 —— ②和③的差距就是①
    #   ④ 幣種       為什麼是這些幣
    # ②原本是「模擬持倉」+「成績單」兩張,數字對不上(一張讀每日記帳
    # 的檔案,一張用即時價),2026-09-18 合成一張,同一組價格算完。
    desk = (block_status() + block_tickets() + block_sim()
            + block_exchange() + block_screen())
    system = block_gaps() + block_proposals() + block_system()
    return _collapse_prose(f"""<!doctype html><html lang="zh-Hant"><head>
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
/* ══ 指令單的有效期限 ══════════════════════════════════════
   2026-09-18 執政官:「如果訊號單有效期限過了就撤掉。」

   伺服器每 180 秒重算一次,所以從伺服器出來的單很少是過期的。
   真正會過期的是**開著沒關的頁面** —— TTL 30 分鐘,手機擺著半小時
   那張單就死了,而畫面上跟活的一模一樣,照著按會用一個過時的數量
   去冒一個不是原本那個的風險。

   所以這裡每秒檢查一次:剩幾分鐘就印幾分鐘,時間到就**就地撤掉**
   (複製鈕失效、連結收起、標成「已過期 · 不要按」),不等重新整理。

   撤掉 ≠ 消失:一張安靜不見的單會讓畫面變成「今天沒有要按的」,
   而那是假話 —— 實際上是「有,但窗口關了」。                     */
/* 價格帶 —— 用串流回來的現價檢查每一張單還算不算數。
   ticket.price_in_band() 在伺服器端寫好了但沒有人呼叫;而真正需要
   它的時刻是**頁面開著、價格在動**的時候,所以判斷放在這裡最直接。
   跑出帶外就跟過期一樣撤掉:數量與止損是照報價算的,價格走了 1%,
   那個數量代表的風險就不是原本那個了。                          */
function tickBand(marks){{
  document.querySelectorAll('.ticket[data-sym]').forEach(function(el){{
    var now = marks[el.dataset.sym];
    if(now === undefined || now === null) return;
    var cell = el.querySelector('.tnow');
    if(cell) cell.textContent = now.toLocaleString(undefined,
      {{minimumFractionDigits:2, maximumFractionDigits:6}});
    var lo = parseFloat(el.dataset.lo), hi = parseFloat(el.dataset.hi);
    if(!(now < lo || now > hi)) return;
    if(el.classList.contains('gone')) return;
    el.classList.add('gone');
    var pill = el.querySelector('.t-head .pill');
    if(pill){{ pill.className = 'pill p-dead';
               pill.textContent = '價格跑掉了 · 不要按'; }}
    var head = el.querySelector('.t-head');
    if(head && !el.querySelector('.tdead')){{
      var d = document.createElement('div');
      d.className = 'flag warn tdead';
      d.innerHTML = '現價已經跑出這張單的有效範圍 —— <b>不要照它按</b>。'
        + '本金與止損是照報價算的,價格走掉之後那個數量代表的風險'
        + '就不是原本那個了。系統會用新價格重算,幾分鐘內會再推一張。';
      head.parentNode.insertBefore(d, head.nextSibling);
    }}
  }});
}}

function tickExpiry(){{
  var now = Date.now();
  document.querySelectorAll('.ticket[data-until]').forEach(function(el){{
    var until = Date.parse(el.dataset.until);
    if(isNaN(until)) return;
    var left = until - now;
    var lab = el.querySelector('.tleft');
    if(left > 0){{
      var m = Math.floor(left/60000), sec = Math.floor((left%60000)/1000);
      if(lab){{
        lab.textContent = (m > 0 ? m + ' 分 ' : '') + sec + ' 秒後過期';
        lab.className = 'tleft' + (left < 300000 ? ' soon' : '');
      }}
      return;
    }}
    if(el.classList.contains('gone')) return;   /* already withdrawn */
    el.classList.add('gone');
    if(lab){{ lab.textContent = '已過期'; lab.className = 'tleft soon'; }}
    var pill = el.querySelector('.t-head .pill');
    if(pill){{ pill.className = 'pill p-dead';
               pill.textContent = '已過期 · 不要按'; }}
    var head = el.querySelector('.t-head');
    if(head && !el.querySelector('.tdead')){{
      var d = document.createElement('div');
      d.className = 'flag warn tdead';
      d.innerHTML = '這張單的有效期限過了 —— <b>不要照它按</b>。'
        + '數量與止損是照當時的價格算的,價格走掉之後那個數量'
        + '代表的風險就不是原本那個了。<br>'
        + '系統會重算;訊號還在、價格還在帶內的話,'
        + '幾分鐘內會再推一張新的(單號會不一樣)。';
      head.parentNode.insertBefore(d, head.nextSibling);
    }}
  }});
}}
if(document.querySelector('.ticket[data-until]')){{
  tickExpiry();
  setInterval(tickExpiry, 1000);
}}

/* ══ 模擬帳戶的即時數字 ══════════════════════════════════════
   2026-09-18 執政官:「我想看到即時的盈虧數字變化。」
   每 15 秒跟 /api/sim 要一次,數字變了就換掉並閃一下。

   ⚠️ 打回來的是**同一個 sim_snapshot()** —— 頁面剛渲染時用的、
   這裡輪詢回來的,是同一套算法同一組價格。各寫一份的話,兩份
   遲早分岔,而那就是「數字對不上」。                            */
function simPut(id, text, val){{
  var el = document.getElementById(id);
  if(!el || el.textContent === text) return;
  el.textContent = text;
  if(typeof val === 'number'){{
    el.className = 'v ' + (val > 0 ? 'up' : (val < 0 ? 'down' : ''));
  }}
  el.classList.remove('tick');
  void el.offsetWidth;          /* 重新觸發動畫 */
  el.classList.add('tick');
}}

function simNum(v, dp, sign){{
  if(v === null || v === undefined) return '—';
  /* 千分位。這一整段在 Python 的 f-string 裡,所以正規表示式的
     反斜線要寫兩個 —— 一個的話是 Python 的無效跳脫序列(現在是
     DeprecationWarning,以後會是錯誤),而輸出的 JS 要的是一個。
     這行註解第一版自己就犯了這個錯:它把範例寫進了同一個字串裡。 */
  var t = Math.abs(v).toFixed(dp).replace(/\\B(?=(\\d{{3}})+(?!\\d))/g, ',');
  if(sign) return (v < 0 ? '-' : '+') + t;
  return (v < 0 ? '-' : '') + t;
}}

function simApply(d){{
  var at = document.getElementById('s-at');
  if(d.error){{
    if(at) at.textContent = '更新失敗:' + d.error;
    return;
  }}
  simPut('s-eq',  simNum(d.equity, 2, false));
  simPut('s-ret', simNum(d.return_pct, 2, true) + '%', d.return_pct);
  simPut('s-exc', simNum(d.excess_pct, 2, true) + '%', d.excess_pct);
  simPut('s-unr', simNum(d.unrealized, 2, true), d.unrealized);
  simPut('s-rea', simNum(d.realized, 2, true), d.realized);
  var body = document.getElementById('s-legs');
  if(body && d.rows) body.innerHTML = d.rows;
  if(d.marks) tickBand(d.marks);
  /* 說得出**來源**與**年齡**的才叫即時。
     「每 N 秒更新」只是我們問的頻率;那筆價格本身有多舊是另一回事,
     而後者才是真正的尺度。說不出年齡的「即時」是沒有證據的話。 */
  if(at){{
    var src = d.source || '?';
    var age = (d.age_s === null || d.age_s === undefined)
      ? '' : ' · 行情 ' + d.age_s.toFixed(1) + ' 秒前';
    at.textContent = src + age;
  }}
}}

/* ── 一、SSE:伺服器自己推,瀏覽器不問 ────────────────────
   交易所 WebSocket 逐筆 -> 伺服器 -> 這裡,每秒一次。
   (逐筆約 10 筆/秒,再快人眼讀不了,而且數字會抖到看不清) */
var SIMES = null;
function simStream(){{
  try{{
    SIMES = new EventSource('/api/simstream?key=' + encodeURIComponent(KEY));
  }} catch(e){{ simPoll(); return; }}
  SIMES.onmessage = function(ev){{
    try{{ simApply(JSON.parse(ev.data)); }} catch(e){{}}
  }};
  SIMES.onerror = function(){{
    /* SSE 斷了就退回輪詢,並且**講出來** —— 一個安靜退化成
       「每 5 秒」的即時面板,跟一個壞掉的即時面板長得一樣。 */
    if(SIMES){{ SIMES.close(); SIMES = null; }}
    var at = document.getElementById('s-at');
    if(at) at.textContent = '推播斷線,改用每 5 秒輪詢';
    simPoll();
  }};
}}

/* ── 二、輪詢退路 ─────────────────────────────────── */
var SIMTIMER = null;
function simTick(){{
  fetch('/api/sim?key=' + encodeURIComponent(KEY), {{cache:'no-store'}})
    .then(function(r){{ return r.json(); }})
    .then(simApply)
    .catch(function(){{
      var at = document.getElementById('s-at');
      /* 連不上要說連不上 —— 不說的話畫面上會是一組凍住的數字,
         而凍住的數字跟活著的數字長得一模一樣。 */
      if(at) at.textContent = '連不上,數字是舊的';
    }});
}}
function simPoll(){{
  if(SIMTIMER) return;
  simTick();
  SIMTIMER = setInterval(simTick, 5000);
}}

if(document.getElementById('s-eq')){{
  simTick();                       /* 先給一次,不要等第一個推播 */
  if(window.EventSource) simStream(); else simPoll();
}}

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
</script></body></html>""")


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

        # ── 模擬帳戶的即時數字 ───────────────────────────────
        # 2026-09-18 執政官:「我想看到即時的盈虧數字變化。」
        # 回的是 block_sim() **同一個** sim_snapshot(),所以頁面上
        # 剛渲染出來的數字與這裡輪詢回來的永遠是同一套算法。
        # SSE:交易所推 -> 這裡 -> 瀏覽器。**瀏覽器不問,伺服器自己送。**
        # 2026-09-18 執政官:「可以做到真正的即時嗎?」
        # 可以 —— 而且需要的東西 2026-09-08 就寫好了(portfolio/stream.py
        # 的 WebSocket + /api/stream 的 SSE),只是這張卡沒有接上。
        # 每秒推一次:交易所逐筆約 10 筆/秒,再快人眼讀不了,
        # 而且數字會抖到看不清。
        if u.path.startswith("/api/simstream"):
            import time as _t
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            try:
                while True:
                    line = ("data: " + json.dumps(sim_payload(),
                                                  ensure_ascii=False) + "\n\n")
                    self.wfile.write(line.encode("utf-8"))
                    self.wfile.flush()
                    _t.sleep(1.0)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass                                 # 使用者關了頁面
            return

        if u.path.startswith("/api/sim"):
            self._send(json.dumps(sim_payload(),
                                  ensure_ascii=False).encode("utf-8"),
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
