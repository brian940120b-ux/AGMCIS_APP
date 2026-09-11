"""
校準報告:這個系統現在有多少數字是真的,多少是猜的。

    python scripts/calibration_report.py

上線前必看。全部都是 DEFAULT_GUESS 的系統,它算出來的強平價與成本
都只是估計 —— 那不代表不能跑,但代表不能拿那些數字當作「已驗證」。

離開碼:0 = 已校準且未過期,1 = 未校準或已過期。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agmcis.config import settings  # noqa: E402
from agmcis.exchange import specs as specs_module  # noqa: E402


def main():
    store = specs_module.get_store()
    report = store.calibration_report(settings.WATCHLIST_SYMBOLS)

    print("=" * 64)
    print("AGMCIS — 合約規格校準報告")
    print("=" * 64)
    print(f"快照檔案 : {report['path']}")
    print(f"已校準   : {'是' if report['calibrated'] else '否'}")
    print(f"擷取時間 : {report['captured_at'] or '(無)'}")
    print(f"經過天數 : {report['age_days'] if report['age_days'] is not None else '(無)'}")
    print(f"測試環境 : {report['testnet']}")

    if report["symbols"]:
        print("\n各標的:")
        for symbol, values in report["symbols"].items():
            print(f"\n  {symbol}")
            for name, info in values.items():
                mark = "✓" if info["source"] == specs_module.SOURCE_EXCHANGE else "?"
                print(f"    {mark} {name:<26} {info['value']:<12} {info['source']}")

    if report["warnings"]:
        print("\n警告:")
        for warning in report["warnings"]:
            print(f"  ⚠️  {warning}")

    print("\n" + "=" * 64)

    if not report["calibrated"] or report["stale"]:
        print("尚未校準或已過期。在 VPS 上執行:")
        print("  .venv/bin/python scripts/verify_bingx.py --write-specs")
        return 1

    print("已校準。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
