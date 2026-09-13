"""
BingX 私有端點 —— 簽章與唯讀帳戶存取 · 2026-09-13(PHASE 3 第一步)

═══ 這一支要做到哪裡,以及刻意不做什麼 ═══
執政官的目標是「能自動判斷並下單的獲利輔助工具」。這是那條路的第一步,
而它**只做唯讀**:餘額、持倉、訂單查詢。

**這裡沒有任何下單的程式碼。** 不是還沒寫完 —— 是刻意分開:

  一、下單之前必須先證明「我看得到帳戶」。看不到帳戶就下單,等於閉著
      眼睛開槍。這一步跑通,整條認證 / 簽章 / 解析的路也就跑通了 80%,
      而全程沒有一毛錢的風險。
  二、金鑰權限請只開**讀取**。這一支不需要交易權限,而一把只能讀的
      金鑰即使外洩也動不了錢。等真的要下單再開,那是另一次決定。
  三、提款權限**永遠 OFF**(第十條)。這一條沒有例外。

═══ 預設連的是 Demo,不是實盤 ═══
BingX 有 Demo Trading:虛擬 USDT(VST),十萬額度,行情與真實市場同步,
主機是 `open-api-vst.bingx.com`。

**這一支預設就連 Demo。** 要連實盤必須明確設定環境變數,而且即使連上
實盤,它能做的也只有讀 —— 下單那條路在這個檔案裡根本不存在。

═══ 簽章規格(2026-09-13 查證,不是憑記憶)═══
  · HMAC-SHA256,十六進位小寫
  · 被簽的是 query string:`k=v&k=v`,**值不做 URL 編碼**
  · 必須含 `timestamp`(毫秒);`recvWindow` 選用,預設視窗 5000ms
  · 簽章以 `&signature=...` 附在 URL 後面
  · API Key 走 `X-BX-APIKEY` 這個 header

第五條說「不要靠模型記憶猜 API」。上面每一條都查過,但**查過不等於
對** —— 所以這一支的第一個動作是拿一個唯讀端點去問交易所,
交易所說 yes 才算數。見 `verify()`。

═══ 金鑰絕不出現在任何輸出 ═══
第十條:Secret 不得出現在原始碼、Git、Log、Exception、資料庫、前端、
瀏覽器、Telegram 或 AI Prompt。這一支的做法:

  · `Credentials.__repr__` 與 `__str__` 一律遮蔽
  · 例外訊息只帶「去掉 query string 的路徑」,因為簽章與 timestamp
    都在 query 裡
  · 任何回傳給呼叫端的 dict 都不含金鑰
  · 有一組測試專門驗這件事,包括「把例外 str() 出來不能出現 secret」
"""
from __future__ import annotations

import hashlib
import hmac
import os
import time
from dataclasses import dataclass
from typing import Any

from core import ratelimit
from core.logging import get_logger

log = get_logger("bingx.private")

# 主機。**預設是 Demo。**
DEMO_HOST = "https://open-api-vst.bingx.com"
LIVE_HOST = "https://open-api.bingx.com"

# 環境變數名。刻意用完整前綴 —— 這台機器上不只一個系統。
ENV_KEY = "BINGX_API_KEY"
ENV_SECRET = "BINGX_API_SECRET"
ENV_MODE = "BINGX_ENV"          # demo(預設)/ live

RECV_WINDOW_MS = 5000

# 唯讀端點。**這份清單裡沒有任何會改變帳戶狀態的東西**,
# 而且有一條測試會檢查這件事。
READ_ONLY = {
    "perp_balance": "/openApi/swap/v3/user/balance",
    "perp_balance_v2": "/openApi/swap/v2/user/balance",
    "perp_positions": "/openApi/swap/v2/user/positions",
    "perp_open_orders": "/openApi/swap/v2/trade/openOrders",
    "std_balance": "/openApi/contract/v1/balance",
    "std_positions": "/openApi/contract/v1/allPosition",
    "std_orders": "/openApi/contract/v1/allOrders",
    # 單向 / 雙向持倉模式。這個查詢決定下單時 positionSide 要送什麼,
    # 送錯直接被拒 —— 見 position_mode()。
    "position_mode": "/openApi/swap/v1/positionSide/dual",
}

# 餘額端點在 v2 / v3 之間改過版。**不猜是哪一個** —— 兩個都問,
# 交易所回哪一個就用哪一個,並把答案記下來(見 discover_balance)。
BALANCE_CANDIDATES = ("perp_balance", "perp_balance_v2")


class CredentialsMissing(RuntimeError):
    """沒有金鑰。訊息裡不會有任何金鑰內容。"""


class PrivateCallFailed(RuntimeError):
    """
    私有端點失敗。

    **訊息不含 query string** —— 簽章與 timestamp 都在那裡面,
    而一份帶著簽章的錯誤訊息會被寫進 log、寄進 Telegram、貼進對話。
    """


@dataclass(frozen=True)
class Credentials:
    key: str
    secret: str

    def __repr__(self) -> str:           # noqa: D105
        return f"Credentials(key={self.masked()}, secret=<遮蔽>)"

    __str__ = __repr__

    def masked(self) -> str:
        """給人看的金鑰識別。只露頭尾各四碼,不足十二碼就整個遮蔽。"""
        if len(self.key) < 12:
            return "<遮蔽>"
        return f"{self.key[:4]}…{self.key[-4:]}"

    @classmethod
    def from_env(cls) -> "Credentials":
        key = (os.environ.get(ENV_KEY) or "").strip()
        secret = (os.environ.get(ENV_SECRET) or "").strip()
        if not key or not secret:
            raise CredentialsMissing(
                f"缺少 {ENV_KEY} 或 {ENV_SECRET}。\n"
                "  · 放在 .env(已 gitignore),不要放進任何原始碼\n"
                "  · 權限只勾**讀取**,這一支不需要交易權限\n"
                "  · **提款權限一律關閉**(第十條,沒有例外)")
        return cls(key=key, secret=secret)


def signature(params: dict, secret: str) -> str:
    """
    照 BingX 規格算簽章。

    被簽的字串是 `k=v&k=v`,**值不做 URL 編碼**。
    這一點是最容易出錯的地方 —— 先編碼再簽,交易所算出來的會不一樣,
    而它只會回一句「簽名錯誤」,不會告訴你為什麼。
    """
    payload = "&".join(f"{k}={params[k]}" for k in params)
    return hmac.new(secret.encode("utf-8"), payload.encode("utf-8"),
                    hashlib.sha256).hexdigest()


def host(mode: str | None = None) -> str:
    """
    要連哪一台。**預設 Demo。**

    只有把 BINGX_ENV 明確設成 live 才會連實盤,而且即使連上,
    這個模組能做的也只有讀。
    """
    picked = (mode or os.environ.get(ENV_MODE) or "demo").strip().lower()
    if picked == "live":
        return LIVE_HOST
    if picked in ("demo", "vst", "test", ""):
        return DEMO_HOST
    raise ValueError(
        f"{ENV_MODE} 只能是 demo 或 live,收到 {picked!r} —— "
        "拼錯就當成 demo 是危險的預設(反過來才安全),所以這裡直接拒絕")


def is_live(mode: str | None = None) -> bool:
    return host(mode) == LIVE_HOST


class ReadOnlyClient:
    """
    只會讀的 BingX 私有端點客戶端。

    它沒有 post / delete 這種方法 —— 不是被擋住,是**根本沒有寫**。
    要下單的話得另外開一個檔案,而那件事需要另一次決定。
    """

    def __init__(self, creds: Credentials | None = None,
                 mode: str | None = None, timeout: float = 15.0,
                 session=None):
        self.creds = creds or Credentials.from_env()
        self.mode = mode
        self.base = host(mode)
        self.timeout = timeout
        self._session = session
        self._balance_path: str | None = None

    # ── 底層 ───────────────────────────────────────────
    def get(self, path: str, params: dict | None = None) -> Any:
        """
        簽名的 GET。回傳 BingX 的 `data` 欄位。

        ⚠️ 這個方法只用在 READ_ONLY 裡列的路徑上,而且有測試檢查。
        """
        query = dict(params or {})
        query["timestamp"] = int(time.time() * 1000)
        query["recvWindow"] = RECV_WINDOW_MS

        sig = signature(query, self.creds.secret)
        from urllib.parse import urlencode
        url = f"{self.base}{path}?{urlencode(query)}&signature={sig}"

        try:
            resp = ratelimit.requests_request(
                self._session, "GET", url,
                headers={"X-BX-APIKEY": self.creds.key},
                timeout=self.timeout)
        except (ratelimit.RateLimited, ratelimit.Banned):
            raise
        except Exception as e:
            # **只講路徑,不講 url** —— url 裡有簽章。
            raise PrivateCallFailed(
                f"{path} 連線失敗:{type(e).__name__}") from None

        if resp.status_code != 200:
            raise PrivateCallFailed(
                f"{path} HTTP {resp.status_code}"
                f"{self._hint(resp.status_code)}")

        try:
            body = resp.json()
        except ValueError:
            raise PrivateCallFailed(f"{path} 回傳的不是 JSON") from None

        code = str(body.get("code", "0"))
        if code != "0":
            raise PrivateCallFailed(
                f"{path} 業務錯誤 code={code} msg={body.get('msg')!r}"
                f"{self._signature_hint(code)}")

        return body.get("data")

    @staticmethod
    def _hint(status: int) -> str:
        if status == 401:
            return " —— 金鑰不對,或這把金鑰沒有這個環境的權限"
        if status == 403:
            return " —— 金鑰權限不足,或 IP 不在白名單"
        return ""

    @staticmethod
    def _signature_hint(code: str) -> str:
        """
        簽章錯誤是這裡最常見、也最難查的一種失敗。

        交易所只會說「簽名錯誤」,不會告訴你是哪一步做錯。
        把已知的三個坑寫在訊息裡,比讓人去翻文件快得多。
        """
        if code not in ("100001", "100413", "80014"):
            return ""
        return ("\n  簽章對不上。三個最常見的原因:"
                "\n  1. 簽之前先把值 URL 編碼了 —— 不可以,要用原始值"
                "\n  2. 簽的參數順序與送出的順序不一致"
                "\n  3. 機器時鐘偏差超過 recvWindow(5 秒)—— 對一下 NTP")

    # ── 唯讀查詢 ────────────────────────────────────────
    def discover_balance(self) -> tuple:
        """
        餘額端點在 v2 / v3 之間改過版。**不猜是哪一個。**

        兩個都問一次,回傳 (路徑, 資料)。交易所是唯一的權威 ——
        猜錯的話會拿到 404 或業務錯誤,而那比一個「看起來對的空值」好。
        """
        errors = []
        for name in BALANCE_CANDIDATES:
            path = READ_ONLY[name]
            try:
                data = self.get(path)
            except PrivateCallFailed as e:
                errors.append(f"{path}: {e}")
                continue
            self._balance_path = path
            return path, data

        raise PrivateCallFailed(
            "兩個餘額端點都問不到:\n  " + "\n  ".join(errors))

    def balance(self) -> Any:
        if self._balance_path:
            return self.get(self._balance_path)
        return self.discover_balance()[1]

    def positions(self, symbol: str | None = None) -> Any:
        params = {"symbol": symbol} if symbol else None
        return self.get(READ_ONLY["perp_positions"], params)

    def positions_everywhere(self, symbols) -> tuple:
        """
        持倉:先整批問一次,空的話再逐幣問一遍。

        ═══ 為什麼不能只問一次 ═══
        「不帶 symbol 就回全部」是一個**假設**。有些交易所的持倉查詢
        必須帶 symbol,不帶就回空清單 —— 而空清單長得跟「真的沒有倉」
        一模一樣。

        那是最壞的一種失敗:它給出一個確定的答案,而那個答案是錯的。
        風控會以為沒有倉,對帳會說一切正常。

        ⚠️ **這個函式只能證明「你給的這些幣沒有倉」,不能證明
        「沒有倉」。** 2026-09-13 就是這樣被騙的:逐幣只問了策略的
        七個幣,而執政官在 App 上開的是 AVA-USDT —— 不在那七個裡面。

        於是整批回 0、逐幣七個都沒有,程式說「兩種都問過了,可信」。
        那句話是錯的。診斷欄位因此改成明講**問了哪幾個**。

        回傳 (持倉列表, 診斷)。診斷會說出用哪一種方法問到的,
        以及兩種方法的結果是否一致 —— **不一致本身就是要報告的事**。
        """
        note = {"bulk": None, "per_symbol": None, "method": None,
                "disagreed": False}

        try:
            bulk = self.positions() or []
            note["bulk"] = len(bulk)
        except PrivateCallFailed as e:
            bulk = []
            note["bulk"] = f"失敗:{e}"

        if bulk:
            note["method"] = "整批"
            return bulk, note

        # 整批是空的。可能真的沒有倉,也可能這個端點需要 symbol。
        #
        # ⚠️ 這個迴圈是一串**同端點的連續請求**,也就是最容易踩到
        # 交易所逐端點限制的形狀(2026-09-13 就踩到了,回 429)。
        # 所以:被限流就**停下來並照實回報**,不是硬撐、也不是
        # 把整個對帳拖垮 —— 一個診斷用的備援查詢不該弄掛主流程。
        found = []
        errors = []
        completed = 0
        for symbol in (symbols or []):
            try:
                rows = self.positions(symbol) or []
            except ratelimit.RateLimited as e:
                errors.append(f"{symbol}: 被限流,逐幣查詢中止({e})")
                note["throttled"] = True
                break
            except PrivateCallFailed as e:
                errors.append(f"{symbol}: {e}")
                continue
            completed += 1
            found.extend(r for r in rows if isinstance(r, dict))

        note["per_symbol"] = len(found)
        note["asked"] = completed
        note["of"] = len(symbols or [])
        note["errors"] = errors
        note["disagreed"] = bool(found)

        if found:
            note["method"] = "逐幣"
        elif note.get("throttled") or completed < len(symbols or []):
            # **沒問完就不能說「沒有倉」。**
            note["method"] = f"沒問完({completed}/{len(symbols or [])})"
            note["incomplete"] = True
        else:
            # **不可以說「兩種都是空的」** —— 那句話聽起來像
            # 「確認沒有倉」,而實際上只問了這幾個幣。
            note["method"] = f"這 {len(symbols or [])} 個幣都沒有"
            note["scoped"] = True

        return found, note

    def open_orders(self, symbol: str | None = None) -> Any:
        params = {"symbol": symbol} if symbol else None
        return self.get(READ_ONLY["perp_open_orders"], params)

    def standard_balance(self) -> Any:
        """標準合約(§6:與永續分開,規格與槓桿上限都不同)。"""
        return self.get(READ_ONLY["std_balance"])

    def standard_positions(self) -> Any:
        return self.get(READ_ONLY["std_positions"])

    def position_mode(self) -> str | None:
        """
        單向(one-way)還是雙向(hedge)持倉模式。

        ═══ 為什麼這件事非查不可 ═══
        它決定下單時 `positionSide` 要送什麼:

          · 雙向模式 -> 必須送 LONG 或 SHORT
          · 單向模式 -> 必須送 BOTH(送 LONG 會被拒)

        **不可以猜。** 猜錯的症狀是下單被拒,而錯誤訊息通常只說
        「參數錯誤」,不會告訴你是哪一個參數。

        查不到就回 None —— **不回一個預設值**。呼叫端要自己決定
        「不知道」該怎麼辦,而不是拿到一個看起來確定的答案。
        """
        try:
            data = self.get(READ_ONLY["position_mode"])
        except PrivateCallFailed as e:
            log.warning(f"查不到持倉模式:{e}")
            return None

        raw = data
        if isinstance(data, dict):
            raw = data.get("dualSidePosition", data.get("dualSide"))
        if raw is None:
            return None
        if isinstance(raw, str):
            raw = raw.strip().lower()
            if raw in ("true", "1"):
                return "hedge"
            if raw in ("false", "0"):
                return "one_way"
            return None
        return "hedge" if bool(raw) else "one_way"

    # ── 自我驗證 ────────────────────────────────────────
    def verify(self) -> dict:
        """
        拿一個唯讀端點去問交易所:這把金鑰能不能用、簽章對不對。

        **這是這一支存在的主要理由。** 上面那份簽章規格是查來的,
        而查過不等於對 —— 交易所說 yes 才算數。

        回傳一份可以印出來給人看的摘要,裡面**沒有任何金鑰內容**。
        """
        result = {
            "環境": "實盤" if is_live(self.mode) else "Demo(VST 虛擬資金)",
            "主機": self.base,
            "金鑰": self.creds.masked(),
            "簽章": "未驗證",
            "餘額端點": None,
            "可讀": [],
            "讀不到": [],
        }

        try:
            path, data = self.discover_balance()
        except PrivateCallFailed as e:
            result["簽章"] = f"失敗 —— {e}"
            return result

        result["簽章"] = "通過(交易所接受了這個簽名)"
        result["餘額端點"] = path
        result["可讀"].append("餘額")
        result["餘額摘要"] = _summarise_balance(data)

        for label, call in (("持倉", self.positions),
                            ("掛單", self.open_orders),
                            ("標準合約餘額", self.standard_balance)):
            try:
                got = call()
            except PrivateCallFailed as e:
                result["讀不到"].append(f"{label}({e})")
                continue
            result["可讀"].append(label)
            if label == "持倉":
                result["持倉數"] = len(got or [])

        return result


def _summarise_balance(data) -> dict:
    """
    把餘額壓成幾個數字。

    **缺欄位就回 None,不回 0**(第九十四條)—— 「讀不到餘額」與
    「餘額是零」在這裡是天差地別的兩件事。
    """
    row = data
    if isinstance(data, dict):
        row = data.get("balance") or data
    if isinstance(row, list):
        row = row[0] if row else {}
    if not isinstance(row, dict):
        return {"原始型別": type(data).__name__}

    def num(*names):
        for n in names:
            if row.get(n) is not None:
                try:
                    return float(row[n])
                except (TypeError, ValueError):
                    return None
        return None

    return {
        "資產": row.get("asset"),
        "權益": num("equity"),
        "餘額": num("balance"),
        "可用保證金": num("availableMargin", "availableBalance"),
        "已用保證金": num("usedMargin"),
        "未實現損益": num("unrealizedProfit"),
    }
