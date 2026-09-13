"""
AGMCIS — 原子寫入(2026-09-03 體檢 Finding 01)

問題:全系統 64 個寫入點全是「開檔 → 清空 → 逐步寫回」。
寫到一半斷電 / 被 kill / 磁碟滿,檔案就停在半截 —— 帳本 1283 案是
這座城唯一不可重建的資產,而它每兩小時整檔重寫一次。

作法(借鑑放養組 wild.py 的 jsave,再補一道 fsync):
    寫 tmp → flush → fsync → os.replace 原子換上

os.replace 在同一個檔案系統上是原子操作:要嘛看到舊檔完整,
要嘛看到新檔完整,不存在「半截」的中間狀態。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def write_text_atomic(path: Path | str, text: str, *,
                      encoding: str = "utf-8") -> None:
    """原子寫入純文字。tmp 與目標同目錄,確保 os.replace 不跨檔案系統。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding=encoding) as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    # 目錄項也落盤(部分檔案系統需要);不支援時忽略,不影響資料正確性
    try:
        dfd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except OSError:
        pass


def write_json_atomic(path: Path | str, obj: Any, *, indent: int = 2) -> None:
    """原子寫入 JSON。序列化失敗時不會動到現役檔(先在記憶體組完字串)。"""
    write_text_atomic(path, json.dumps(obj, ensure_ascii=False, indent=indent))
