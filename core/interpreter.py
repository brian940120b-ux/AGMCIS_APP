"""
你用對 Python 了嗎 —— 2026-09-13

═══ 為什麼要有這一支 ═══
在 VPS 上跑 `python3 scripts/concentration_history.py`,得到的是:

    File "/root/agmcis/models/market.py", line 14
        from pydantic import BaseModel, Field, model_validator
    ModuleNotFoundError: No module named 'pydantic'

那個訊息是真的,但它指向錯的地方。pydantic **有**在 requirements.txt
裡,venv 裡也裝好了 —— 問題是這支腳本被系統 python 跑,而服務跑的是
`/root/agmcis/.venv/bin/python`(見 agmcis-dash.service 的 ExecStart)。

看到那則訊息的人會去想「是不是少裝套件」,然後 `pip install pydantic`
裝到系統 python 上,於是有兩份互不相干的環境,而下一個錯誤會更難查。

真正的問題只有一句話:**你用錯直譯器了。**

而且它一定會再發生:`models/market.py` 需要 pydantic,而它是
`market_data/history.py` 的必經之路 —— **任何要讀 K 線的腳本都繞不過**。

═══ 這支不做什麼 ═══
不自動切換直譯器、不自動裝套件、不 exec 到別的 python。
一個會偷偷換掉自己執行環境的腳本,除錯的時候會讓人瘋掉。

它只做一件事:在錯誤發生之前,把話講清楚。
"""
from __future__ import annotations

import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
VENV_PYTHON = BASE / ".venv" / "bin" / "python"

# 這個專案跑起來一定要有的。不是完整的相依清單 —— 是「少了它就
# 一定會炸,而且炸得讓人看不出真正原因」的那幾個。
REQUIRED = ("pydantic", "requests")


def missing() -> list:
    import importlib.util
    return [name for name in REQUIRED
            if importlib.util.find_spec(name) is None]


def explain(gaps: list) -> str:
    lines = [
        "",
        "═" * 60,
        f"這個 Python 少了:{', '.join(gaps)}",
        "",
    ]
    if VENV_PYTHON.exists():
        lines += [
            "多半不是套件沒裝,是**直譯器用錯了**。",
            f"你現在用的是:{sys.executable}",
            f"服務用的是:  {VENV_PYTHON}",
            "",
            "改成這樣跑:",
            f"    {VENV_PYTHON} {' '.join(sys.argv) or '<script>'}",
            "",
            "⚠️ 不要 pip install 到系統 python —— 那會變成兩份互不相干",
            "   的環境,而下一個錯誤會更難查。",
        ]
    else:
        lines += [
            f"找不到 venv({VENV_PYTHON}),看起來這台機器還沒建好環境:",
            "",
            "    python3 -m venv .venv",
            "    .venv/bin/pip install -r requirements.txt",
        ]
    lines += ["═" * 60, ""]
    return "\n".join(lines)


def require(exit_code: int = 2) -> None:
    """
    在 import 那些會炸的東西**之前**呼叫。

    少東西就印出人話並結束,不拋一個第 14 行的 ModuleNotFoundError。
    """
    gaps = missing()
    if not gaps:
        return
    print(explain(gaps), file=sys.stderr)
    raise SystemExit(exit_code)
