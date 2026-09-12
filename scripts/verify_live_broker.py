"""
實單路徑檢視(Master Prompt 第七十八 / 一百零三節)。

    python scripts/verify_live_broker.py

## 這支腳本不下單,也不自己重寫一套檢查

它做兩件事:

  1. 印出實單路徑上每一個檔案與它的 SHA-256 —— 讓人知道**要讀什麼**,
     以及待會在 scripts/live_confirm.py 裡簽的是哪一份原始碼。

  2. 跑 tests/test_live_broker.py。

第二件事刻意用既有的測試,不在這裡重寫一遍。同一組行為規則
寫在兩個地方,遲早會有一邊被改而另一邊沒有 —— 到時候「兩邊都通過」
只代表兩邊在驗不同的東西。這條原則整個專案都在守。

那組測試問的是同一句話:**這裡壞掉的時候,它往哪一邊倒?**
安全的方向是固定的:不知道多大就不送、不知道有沒有停損就當作沒有、
不知道部位方向就不動它、不知道訂單狀態就說不知道。

## 為什麼它不驗「能不能真的下單」

因為那需要真錢。真錢那一步在 LIVE SAFETY GATE 後面,不在這裡。
這支腳本是**上那個閘門之前**的功課。
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agmcis.safety import live_path  # noqa: E402
from agmcis.safety.live_gate import LiveGate  # noqa: E402

RULE = "=" * 68

BEHAVIOUR_TESTS = "tests/test_live_broker.py"


def show_live_path(writer=print):
    """印出要讀的檔案與雜湊。回傳 {檔名: 雜湊}。"""
    writer(RULE)
    writer("實單路徑 —— 這些檔案會送出真實訂單")
    writer(RULE)

    found = LiveGate()._scan_live_broker_sources()

    if not found:
        writer("  (沒有實單程式碼。LIVE SAFETY GATE 的實單路徑那一項直接通過。)")
        writer("")
        return {}

    signature = live_path.build_signature(found)
    reasons = {}
    for where, name, why in found:
        reasons.setdefault(where, []).append(f"{name}({why})")

    for relative in sorted(signature):
        writer(f"  {relative}")
        writer(f"      {'、'.join(reasons[relative])}")
        writer(f"      SHA-256 {signature[relative]}")

    writer("")
    writer("  這份雜湊要由**讀過這些檔案的人**簽下去:")
    writer("      python scripts/live_confirm.py")
    writer("  改一個字雜湊就變,舊的簽章作廢。")
    writer("")
    return signature


def run_behaviour_tests(writer=print, runner=None):
    """
    跑 LiveBroker 的行為測試。回傳 (通過?, 輸出)。

    測試跑不起來算**沒通過**,不是「跳過」——
    一個在自己壞掉時回報成功的驗證腳本不是驗證。
    """
    writer(RULE)
    writer(f"行為驗證 —— {BEHAVIOUR_TESTS}")
    writer(RULE)

    if runner is None:
        def runner():
            return subprocess.run(
                [sys.executable, "-m", "pytest", BEHAVIOUR_TESTS, "-q"],
                cwd=str(ROOT), capture_output=True, text=True,
            )

    try:
        completed = runner()
    except Exception as exc:
        writer(f"  ⛔ 跑不起來:{type(exc).__name__}: {exc}")
        return False, str(exc)

    output = (completed.stdout or "") + (completed.stderr or "")
    for line in output.strip().splitlines():
        writer(f"  {line}")
    writer("")

    return completed.returncode == 0, output


def run(writer=print, runner=None):
    """回傳 (全部通過?, 訊息)。"""
    show_live_path(writer)
    ok, _ = run_behaviour_tests(writer, runner)

    writer(RULE)
    if ok:
        writer("行為驗證通過。")
        writer("")
        writer("這**不代表可以下實單**。它代表這段程式碼在壞掉的時候")
        writer("會往安全的方向倒。能不能下實單由 LIVE SAFETY GATE 決定:")
        writer("      python scripts/live_gate.py")
        return True, "通過"

    writer("⛔ 行為驗證沒有通過。在修好之前,不要往下走。")
    return False, "沒通過"


def main():
    ok, _ = run()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
