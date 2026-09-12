"""
LIVE 確認精靈(Master Prompt 第九十二節)。

    python scripts/live_confirm.py

從 PAPER 切到 LIVE 之前逐項確認七件事,最後逐字輸入確認句,
產生一份 24 小時後失效、指名批准金額的確認檔。

## 為什麼是命令列而不是網頁按鈕

網頁按鈕表達不了兩件事,而那兩件事正是這套機制的重點:

  * 這個批准 **24 小時後失效**。
  * 這次批准的是 **30 USDT,不是所有金額**。

而且一個誤觸就會發生的動作,不該是「開始用真錢交易」。

## 這支腳本不打開任何東西

它只產生確認檔。閘門還有其他幾項檢查(模擬盤筆數、天數、
上線前檢查、提款權限……),那些不是人簽名就能通過的。
簽完之後跑 `python scripts/live_gate.py` 看整體結果。

## 中途放棄很容易

任何一項回答不是 yes 就結束,而且**不寫檔**。
一份寫到一半的確認檔比沒有確認檔危險。
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agmcis.safety import live_confirm  # noqa: E402
from agmcis.safety import live_gate as gate_module  # noqa: E402
from agmcis.safety import live_path as live_path_module  # noqa: E402

RULE = "=" * 68


def _format(values):
    lines = []
    for key in sorted(values):
        value = values[key]
        if value is None:
            # None 與 0 / False 在這裡意思完全不同。
            shown = "(沒有值)"
        elif isinstance(value, bool):
            shown = "是" if value else "否"
        elif isinstance(value, list):
            shown = "、".join(str(v) for v in value) or "(空)"
        else:
            shown = str(value)
        lines.append(f"      {key:<28} {shown}")
    return "\n".join(lines)


def _ask(prompt, reader=input):
    try:
        return reader(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        return ""


def _review_live_path(reader, writer):
    """
    逐檔問「這一份會送真實訂單的程式碼你讀過了嗎」。

    回傳 (ok, 簽章, 訊息)。沒有實單程式碼時簽章是空的 —— 那是
    正常狀態,不是缺漏。
    """
    from agmcis.safety import live_path
    from agmcis.safety.live_gate import LiveGate

    try:
        found = LiveGate()._scan_live_broker_sources()
    except Exception as exc:
        return False, None, (
            f"掃不到實單路徑({type(exc).__name__}: {exc})。中止 ——"
            f"掃不動不等於沒有。"
        )

    if not found:
        return True, live_path.EMPTY, ""

    reasons = {}
    for where, name, why in found:
        reasons.setdefault(where, []).append(f"{name}({why})")

    try:
        signature = live_path.build_signature(found)
    except Exception as exc:
        return False, None, f"算不出原始碼雜湊({type(exc).__name__}: {exc})。中止。"

    writer("")
    writer("  ⚠️  這個系統裡有會送出**真實訂單**的程式碼。")
    writer("      第七十八節不允許我自己核可它上線,所以要請你逐檔讀過。")
    writer("      簽的是每一個檔案當下的 SHA-256 —— 改一個字就作廢。")
    writer("")

    for index, relative in enumerate(sorted(signature), start=1):
        writer(f"  [{index}/{len(signature)}] {relative}")
        writer(f"      {'、'.join(reasons.get(relative, []))}")
        writer(f"      SHA-256 {signature[relative]}")
        answer = _ask("      你讀過這個檔案了嗎?(yes / 其他任何字 = 放棄)", reader)
        writer("")

        if answer.lower() != "yes":
            return False, None, (
                f"在實單路徑第 {index} 個檔案({relative})中止。"
                f"沒有寫入任何檔案。"
            )

    return True, signature, ""


def run(reader=input, writer=print, path=None, settings_module=None):
    """
    跑一次精靈。回傳 (成功?, 訊息)。

    reader / writer 可注入,所以測試不需要真的敲鍵盤。
    """
    items = live_confirm.build_items(settings_module)

    writer(RULE)
    writer("AGMCIS — LIVE 確認精靈(第九十二節)")
    writer(RULE)
    writer("")
    writer("接下來七個問題問的是**現在這一刻的實際設定**。")
    writer("任何一項回答不是 yes 就結束,而且不會寫任何檔案。")
    writer("")

    for index, entry in enumerate(items, start=1):
        writer(f"  [{index}/7] {entry['title']}")
        writer(_format(entry["values"]))
        answer = _ask("      以上正確嗎?(yes / 其他任何字 = 放棄)", reader)
        writer("")

        if answer.lower() != "yes":
            return False, f"在第 {index} 項({entry['item']})中止。沒有寫入任何檔案。"

    # ---------- 實單路徑逐檔審視 ----------
    #
    # 第七十八節:AI 不得自我修改 → 自我測試 → 自我核可 → 自我上線。
    # 雜湊我算得出來,「我讀過了」不行 —— 所以這一段一定要問人。
    ok, signature, message = _review_live_path(reader, writer)
    if not ok:
        return False, message

    # ---------- 批准金額 ----------
    writer(f"  批准的**單筆名目上限**是多少 USDT?"
           f"(上限 {gate_module.MAX_INITIAL_NOTIONAL_USDT:g})")
    writer("  第一次用真錢跑的規模應該小到虧光也不影響任何事。")
    raw = _ask("      金額:", reader)
    writer("")

    try:
        notional = float(raw)
    except ValueError:
        return False, f"金額 {raw!r} 不是數字。中止,沒有寫入任何檔案。"

    if notional <= 0:
        return False, "金額必須大於 0。中止。"

    if notional > gate_module.MAX_INITIAL_NOTIONAL_USDT:
        return False, (
            f"金額 {notional:g} 超過首次實單上限 "
            f"{gate_module.MAX_INITIAL_NOTIONAL_USDT:g} USDT。中止。"
        )

    # ---------- 確認句 ----------
    writer("  最後,逐字輸入這一句(大小寫要一樣):")
    writer(f"      {gate_module.REQUIRED_PHRASE}")
    phrase = _ask("      > ", reader)
    writer("")

    if phrase != gate_module.REQUIRED_PHRASE:
        return False, "確認句不符。中止,沒有寫入任何檔案。"

    payload = {
        "phrase": gate_module.REQUIRED_PHRASE,
        "signed_at": datetime.now(timezone.utc).isoformat(),
        "approved_notional_usdt": notional,
        "confirmations": {entry["item"]: True for entry in items},
        # 簽的是**這一組設定**。之後有人改了風控參數,指紋就對不上,
        # 閘門會作廢這份確認 —— 見 agmcis/safety/live_confirm.py。
        "settings_fingerprint": live_confirm.fingerprint(items),
        "confirmed_values": {e["item"]: e["values"] for e in items},
        # 實單原始碼的雜湊。改一個字就作廢 —— 見 agmcis/safety/live_path.py。
        live_path_module.REVIEW_KEY: signature,
    }

    target = Path(path or gate_module.CONFIRMATION_FILE)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8",
    )

    return True, (
        f"已寫入 {target}\n"
        f"  批准單筆名目上限 {notional:g} USDT\n"
        f"  {gate_module.CONFIRMATION_VALID_HOURS} 小時後失效\n"
        f"  設定被改過就失效\n\n"
        f"  這**不代表閘門開了**。跑 python scripts/live_gate.py 看整體結果。"
    )


def main():
    ok, message = run()
    print(RULE)
    print(message)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
