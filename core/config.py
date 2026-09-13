"""
AGMCIS Phase 5 — 設定載入
對應架構文件:第十四章「API 金鑰只存於安全環境變數,不寫入程式碼或版本庫」

用法:backend/ 目錄下放一個 .env 檔(參考 .env.example),
啟動時呼叫 load_env() 會把裡面的 KEY=VALUE 讀進環境變數。
.env 絕對不可以進 Git(.gitignore 已列入)。
"""
from __future__ import annotations

import os
from pathlib import Path


def load_env(path: Path | None = None) -> None:
    """讀取 .env(若存在)。已存在的環境變數不覆蓋 —— 系統層設定優先。"""
    env_path = path or Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip().lstrip("\ufeff")
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def require(key: str) -> str:
    value = os.environ.get(key, "")
    if not value:
        raise RuntimeError(
            f"缺少環境變數 {key}。請在 backend/.env 加入一行:{key}=你的值")
    return value
