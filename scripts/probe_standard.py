"""
標準合約到底能不能自動下單 —— 用今天的條件重驗 · 2026-09-13

═══ 為什麼要重驗 ═══
`exchange/bingx/standard.py` 在 2026-09-10 下了結論:標準合約沒有下單
端點、沒有公開行情端點,所以無法自動化。

但那次實測是**沒有 API 金鑰**的 —— 拿到的是 code 100413(需認證)。
「需認證」不等於「不存在」。而今天我們有金鑰,`allPosition` 已經回了
200,證明讀的那一半是通的。

一個三天前、在不同條件下做出的結論,不該拿來決定要不要換整套市場。

═══ 這支怎麼問,而且為什麼是安全的 ═══
對每一個候選端點送出**不帶任何交易參數**的簽名請求,看交易所怎麼回:

    端點不存在      -> 404 / code 100400
    端點存在但缺參數 -> 參數錯誤(**證明它存在**)
    端點存在但沒權限 -> code 100004(**也證明它存在**)

三種回應都不會下單:沒有 symbol / side / quantity 的下單請求
不可能被接受。而這把金鑰目前也沒有交易權限。

**這不是猜,是讓交易所自己回答。** 和餘額端點 v2/v3 那次同一個做法。

═══ 讀的部分順便一起問 ═══
標準合約如果連「有哪些標的、精度多少、最小量多少」都查不到,
那麼即使有下單端點也不能用 —— 2026-09-09 的教訓:不知道精度
就會產生七張全部被拒的單(第十二條)。

用法:  .venv/bin/python scripts/probe_standard.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import interpreter

interpreter.require()

from core import ratelimit
from core.config import load_env
from exchange.bingx import private

# 讀:這些決定「即使能下單,下得出正確的單嗎」
READ_CANDIDATES = [
    ("合約清單 v1", "/openApi/contract/v1/contracts"),
    ("合約清單 cswap", "/openApi/cswap/v1/market/contracts"),
    ("行情 cswap", "/openApi/cswap/v1/market/ticker"),
    ("餘額", "/openApi/contract/v1/balance"),
    ("持倉", "/openApi/contract/v1/allPosition"),
    ("歷史訂單", "/openApi/contract/v1/allOrders"),
]

# 寫:這些決定「能不能自動下單」
# **一律不帶交易參數** —— 缺 symbol/side/quantity 的請求不可能成交。
WRITE_CANDIDATES = [
    ("下單 contract/v1", "/openApi/contract/v1/order"),
    ("下單 contract/v1/trade", "/openApi/contract/v1/trade/order"),
    ("下單 cswap", "/openApi/cswap/v1/trade/order"),
]


def ask(client, path: str) -> str:
    """
    問一個端點。回傳一句人看得懂的結論。

    **不帶任何交易參數。** 只有 timestamp 與 recvWindow。
    """
    query = {"timestamp": int(time.time() * 1000),
             "recvWindow": private.RECV_WINDOW_MS}
    sig = private.signature(query, client.creds.secret)
    url = f"{client.base}{path}?{urlencode(query)}&signature={sig}"

    try:
        resp = ratelimit.requests_request(
            None, "GET", url,
            headers={"X-BX-APIKEY": client.creds.key}, timeout=15.0)
    except ratelimit.RateLimited:
        return "被限流 —— 等一下再問"
    except Exception as e:
        return f"連不上:{type(e).__name__}"

    if resp.status_code == 404:
        return "✗ 不存在(HTTP 404)"
    if resp.status_code != 200:
        return f"HTTP {resp.status_code}"

    try:
        body = resp.json()
    except ValueError:
        return "回的不是 JSON"

    code = str(body.get("code", "0"))
    msg = str(body.get("msg") or "")

    if code == "0":
        data = body.get("data")
        size = len(data) if isinstance(data, (list, dict)) else "?"
        return f"✓ 存在且可讀(data {size} 項)"
    if code in ("100400", "104414"):
        return f"✗ 不存在(code {code})"
    if code == "100004":
        return f"✓ **存在**,但金鑰缺這個權限(code {code})"
    if code in ("100413", "100001"):
        return f"? 認證問題(code {code} {msg[:40]})"
    # 參數錯誤代表端點收到了請求 —— 也就是它存在
    return f"✓ **存在**(code {code} {msg[:50]})"


def main() -> int:
    load_env()

    try:
        client = private.ReadOnlyClient()
    except private.CredentialsMissing as e:
        print(f"\n{e}\n")
        return 2

    env = "實盤" if private.is_live() else "Demo(VST)"
    print()
    print("═" * 62)
    print(f"  標準合約端點探測 · {env}")
    print("  只送不帶交易參數的查詢 —— 不會下任何單")
    print("═" * 62)

    print()
    print("  【讀】沒有這些就算能下單也下不出正確的單(第十二條)")
    for label, path in READ_CANDIDATES:
        print(f"    {label:<18} {ask(client, path)}")
        print(f"    {'':<18} {path}")

    print()
    print("  【寫】沒有這些就無法自動下單")
    for label, path in WRITE_CANDIDATES:
        print(f"    {label:<18} {ask(client, path)}")
        print(f"    {'':<18} {path}")

    print()
    print("═" * 62)
    print()
    print("  怎麼讀這份結果:")
    print("    ✗ 不存在        -> 交易所沒有開這個端點")
    print("    ✓ 存在但缺權限  -> 端點在,加權限就能用")
    print("    ✓ 存在(參數錯) -> 端點在,補參數就能用")
    print()
    print("  如果【寫】那三個全部是「不存在」,那麼「只要標準合約」")
    print("  與「自動幫我下單」就是互斥的 —— 不是做不做的問題,")
    print("  是交易所沒有開那個 API。")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
