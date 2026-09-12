"""
實單路徑的人工審視簽章(Master Prompt 第七十八 / 一百零二節)。

## 為什麼需要這個檔案

第七十八節:AI 不得自我修改 → 自我測試 → 自我核可 → 自我上線。

`LiveBroker` 是那條鏈的最後一節。我可以寫它、可以測它,
但「這段會送真實訂單的程式碼我讀過了,可以上線」這句話
必須由人說 —— 而且要能證明說的是**這一份**程式碼,
不是上週那一份。

## 做法:對原始碼算雜湊,簽的是雜湊

    python scripts/live_confirm.py

會列出目前所有實單路徑的檔案與各自的 SHA-256,人讀過之後簽下去。
確認檔裡存的是 {檔名: 雜湊}。閘門驗證時**重新算一次**:

    * 檔案被改過一個字     → 雜湊不同 → 作廢
    * 多了一個新的實單檔案 → 沒有簽章 → 作廢
    * 少了一個檔案         → 那個簽章沒用到,但剩下的照樣要對

三種都不放行。這與第九十二節「確認綁定的是當時的那組設定」
是同一條原則,只是綁的東西從設定值換成原始碼。

## 我產不出這個簽章

雜湊我算得出來,但簽章的意義不在雜湊值,在「有人讀過」。
所以這個模組只做兩件事:算出要簽的東西、驗證簽的對不對。
把簽章寫進確認檔的那一步在 scripts/live_confirm.py,而它會
逐檔問「你讀過了嗎」。
"""
import hashlib
import logging
from pathlib import Path

logger = logging.getLogger("agmcis.safety.live_path")

# 確認檔裡放簽章的欄位。
REVIEW_KEY = "reviewed_live_path"

# 沒有實單程式碼時,這一欄應該長什麼樣。空 dict 與「沒有這一欄」
# 是同一件事 —— 兩者都代表「沒有簽過任何實單程式碼」。
EMPTY = {}


def digest(path):
    """一個檔案的 SHA-256。讀不到就拋例外 —— 讀不到不等於沒問題。"""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify(found, data, root=None):
    """
    驗證一份確認檔涵蓋了掃描到的每一個實單檔案。

    `found` 是 live_gate 掃出來的 [(檔案, 名稱, 原因)]。
    回傳 (ok, 說明)。**任何不確定都算沒通過。**
    """
    files = sorted({where for where, _name, _why in found})

    if not files:
        return True, "沒有實單程式碼"

    signatures = (data or {}).get(REVIEW_KEY)
    if not isinstance(signatures, dict) or not signatures:
        return False, (
            f"實單程式碼存在({'、'.join(files)}),但確認檔沒有 "
            f"{REVIEW_KEY}。第七十八節:核可上線的那一步必須是人做的。\n"
            f"請執行 python scripts/live_confirm.py 逐檔審視後重新簽署。"
        )

    root = Path(root) if root else Path(__file__).resolve().parents[2]

    unsigned, changed, unreadable = [], [], []

    for relative in files:
        signed = signatures.get(relative)
        if not signed:
            unsigned.append(relative)
            continue

        try:
            actual = digest(root / relative)
        except Exception as exc:
            logger.error("LIVE PATH | 算不出雜湊 | %s | %s", relative, exc)
            unreadable.append(f"{relative}({type(exc).__name__})")
            continue

        if str(signed) != actual:
            changed.append(relative)

    problems = []
    if unsigned:
        problems.append(f"沒有簽章:{'、'.join(unsigned)}")
    if changed:
        problems.append(
            f"簽署之後被改過:{'、'.join(changed)} —— "
            f"簽的是當時那一份原始碼,不是「以後所有的版本」"
        )
    if unreadable:
        problems.append(f"讀不到,無法比對:{'、'.join(unreadable)}")

    if problems:
        return False, ";".join(problems)

    return True, f"{len(files)} 個實單檔案都經過人工審視並簽章"


def build_signature(found, root=None):
    """
    產生要簽的 {檔名: 雜湊}。給 scripts/live_confirm.py 用。

    這個函式**不代表核可** —— 它只是把要讀的東西列出來。
    核可是人逐檔回答「讀過了」之後才發生的事。
    """
    root = Path(root) if root else Path(__file__).resolve().parents[2]
    files = sorted({where for where, _name, _why in found})
    return {relative: digest(root / relative) for relative in files}
