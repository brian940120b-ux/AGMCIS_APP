"""
LIVE SAFETY GATE 檢查。

    python scripts/live_gate.py

回答一個問題:**現在可以用真錢交易嗎?**

這支腳本**不會**打開閘門,它只是把每一項檢查跑過一次並顯示結果。
閘門要開,需要一個由人手動建立的確認檔 —— 見下方說明與
docs/PHASE_17_REPORT.md。

離開碼:0 = 閘門開啟,1 = 關閉。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agmcis.safety import live_gate as gate_module  # noqa: E402
from agmcis.safety.providers import build_gate  # noqa: E402


def print_confirmation_template():
    print("\n確認檔範本(必須由**你本人**手動建立):")
    print(f"  檔案:{gate_module.CONFIRMATION_FILE}")
    print("  內容:")
    print(json.dumps({
        "phrase": gate_module.REQUIRED_PHRASE,
        "signed_at": "<現在的 UTC 時間,例如 2026-09-11T12:00:00+00:00>",
        "approved_notional_usdt": 20,
    }, ensure_ascii=False, indent=2))
    print(f"\n  有效期 {gate_module.CONFIRMATION_VALID_HOURS} 小時。")
    print(f"  單筆名目上限不得超過 {gate_module.MAX_INITIAL_NOTIONAL_USDT} USDT。")
    print("  這個檔案不進版控。")


def main():
    print("=" * 64)
    print("AGMCIS — LIVE SAFETY GATE")
    print("=" * 64)

    result = build_gate().evaluate()

    print()
    for line in result.summary_lines():
        print(line)

    print("\n" + "=" * 64)

    if not result.open:
        print(f"閘門關閉。有 {len(result.failed)} 項未通過。")
        if any(c.name == "人工確認" for c in result.failed):
            print_confirmation_template()
        return 1

    print(f"閘門開啟。批准單筆名目上限 {result.approved_notional} USDT。")
    print()
    print("⚠️  提醒:閘門開啟**不代表系統會下實單**。")
    print("    LiveBroker 並不存在 —— 實單程式碼本身還要再經過一次審視。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
