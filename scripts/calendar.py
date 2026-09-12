"""
事件日曆維護(Master Prompt 第五十一節)。

    python scripts/calendar.py                        看現在的狀態
    python scripts/calendar.py --add "FOMC 利率決議" YYYY-MM-DDTHH:MMZ HIGH
    python scripts/calendar.py --touch                只重新蓋時間戳
    python scripts/calendar.py --prune                清掉已經過去的事件

## 為什麼需要這支腳本

日曆是一份**人工維護**的 JSON,而它超過七天沒更新就會被當成不可信。
手改 JSON 的問題不是麻煩,是**很容易忘記改 generated_at** ——
改了事件卻沒改時間戳,系統仍然當它過期;或者只改時間戳沒加事件,
系統會當它是最新的但裡面什麼都沒有。

這支腳本把兩件事綁在一起:任何寫入都會重新蓋 generated_at。

## 它不會自己去抓日期

FOMC 的日期是公布的,不是算出來的;CPI 與 NFP 會因為假日與
日光節約時間移動。從模型記憶生一份日期出來,比沒有日曆更糟 ——
一份**看起來新、內容是猜的**日曆會通過所有檢查,然後在真正的
FOMC 當天讓系統照常開倉。

日期請從官方來源複製:
    FOMC      federalreserve.gov 的 FOMC calendar
    CPI / PPI bls.gov 的 release schedule
    NFP       bls.gov 的 Employment Situation release schedule

時間一律用 UTC。
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agmcis.risk import calendar_watch  # noqa: E402
from agmcis.risk import news_risk  # noqa: E402

IMPACTS = ("HIGH", "MEDIUM", "LOW")


def parse_args():
    parser = argparse.ArgumentParser(description="事件日曆維護(第五十一節)")
    parser.add_argument(
        "--path", default=news_risk.DEFAULT_CALENDAR_PATH,
        help="日曆檔位置。",
    )
    parser.add_argument(
        "--add", nargs=3, metavar=("名稱", "UTC時間", "影響"),
        help='新增一筆。時間用 UTC ISO 格式(YYYY-MM-DDTHH:MMZ)。'
             '**日期請從官方來源複製,不要憑印象填** —— '
             '一份看起來新、內容是猜的日曆會通過所有檢查。',
    )
    parser.add_argument(
        "--before", type=int, default=None,
        help="公布前封鎖幾分鐘(只跟 --add 一起用,預設 30)。",
    )
    parser.add_argument(
        "--after", type=int, default=None,
        help="公布後封鎖幾分鐘(只跟 --add 一起用,預設 30)。",
    )
    parser.add_argument(
        "--touch", action="store_true",
        help="不改內容,只重新蓋 generated_at。"
             "**只有在你確認過內容仍然正確時才用** —— "
             "蓋一個新時間戳到一份舊日曆上,等於騙過所有檢查。",
    )
    parser.add_argument(
        "--prune", action="store_true",
        help="清掉已經過去的事件。",
    )
    return parser.parse_args()


def _load(path):
    target = Path(path)
    if not target.exists():
        return {"events": []}

    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"⛔ 日曆讀不出來:{type(exc).__name__}: {exc}")
        print("   請先修好它,或把它移開讓這支腳本重建一份。")
        raise SystemExit(1)

    if not isinstance(payload, dict):
        print("⛔ 日曆的最外層不是物件。")
        raise SystemExit(1)

    payload.setdefault("events", [])
    return payload


def _save(path, payload):
    """
    任何寫入都會重新蓋 generated_at —— 見模組說明。
    """
    payload["generated_at"] = datetime.now(timezone.utc).isoformat()

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    return payload["generated_at"]


def add_event(payload, name, when, impact, before=None, after=None):
    """回傳錯誤訊息字串,或 None 代表成功。"""
    impact = impact.upper()
    if impact not in IMPACTS:
        return f"影響等級必須是 {' / '.join(IMPACTS)},收到 {impact!r}"

    moment = news_risk._utc(when)
    if moment is None:
        return (
            f"時間解析不了:{when!r}。請用 UTC ISO 格式,"
            f"形如 YYYY-MM-DDTHH:MMZ"
        )

    # 過去的事件加進來沒有意義,而且會讓 --prune 立刻把它清掉。
    if moment < datetime.now(timezone.utc):
        return f"{when} 已經過去了。日曆是給未來用的。"

    entry = {"name": name, "at": moment.isoformat(), "impact": impact}
    if before is not None:
        entry["before_minutes"] = int(before)
    if after is not None:
        entry["after_minutes"] = int(after)

    # 同名同時間視為重複。重複的事件不會造成錯誤,但會讓清單變難讀。
    for existing in payload["events"]:
        if existing.get("name") == name and existing.get("at") == entry["at"]:
            return f"已經有一筆一樣的:{name} @ {entry['at']}"

    payload["events"].append(entry)
    payload["events"].sort(key=lambda e: str(e.get("at")))
    return None


def prune(payload, now=None):
    """清掉已經過去的事件。回傳清掉幾筆。"""
    now = now or datetime.now(timezone.utc)

    kept = []
    removed = 0

    for event in payload["events"]:
        moment = news_risk._utc(event.get("at"))
        # 解析不了的**留著** —— 它是一個要人去看的問題,
        # 清掉等於把問題藏起來。
        if moment is not None and moment < now:
            removed += 1
            continue
        kept.append(event)

    payload["events"] = kept
    return removed


def show(path):
    state = calendar_watch.inspect_calendar(path=path)
    icon = calendar_watch.ICON.get(state["status"], "⚪")

    print(f"{icon} {state['status']}")
    print(f"   {state['detail']}")
    print(f"   檔案     {state['source']}")
    print(f"   事件數   {state['events']}")
    print(f"   更新於   {state['generated_at'] or '(沒有時間戳)'}")

    for error in state["errors"]:
        print(f"   ⚠️  {error}")

    if state["upcoming"]:
        print("\n   接下來兩週:")
        for event in state["upcoming"]:
            print(f"     {event['at']}  {event['impact']:<6} {event['name']}")
    else:
        print("\n   接下來兩週沒有事件。"
              "如果那不對,代表日曆漏了東西 —— 空的日曆會通過所有檢查。")

    return 0 if state["status"] == calendar_watch.OK else 1


def main():
    args = parse_args()

    if not (args.add or args.touch or args.prune):
        return show(args.path)

    payload = _load(args.path)

    if args.add:
        problem = add_event(
            payload, args.add[0], args.add[1], args.add[2],
            before=args.before, after=args.after,
        )
        if problem:
            print(f"⛔ {problem}")
            return 1
        print(f"✅ 已新增:{args.add[0]} @ {args.add[1]} ({args.add[2].upper()})")

    if args.prune:
        removed = prune(payload)
        print(f"🧹 清掉 {removed} 筆已經過去的事件。")

    if args.touch and not (args.add or args.prune):
        print("⚠️  只蓋時間戳。**請確認內容仍然正確** —— "
              "蓋一個新時間戳到一份舊日曆上,等於騙過所有檢查。")

    stamp = _save(args.path, payload)
    print(f"🕒 generated_at 更新為 {stamp}")
    print()

    return show(args.path)


if __name__ == "__main__":
    sys.exit(main())
