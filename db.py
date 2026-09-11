"""
資料庫連線層。

連線參數一律從環境變數讀取 —— 密碼絕對不寫在原始碼裡。
另外提供 transaction() context manager:交易系統裡「改帳戶餘額」與「改交易狀態」
必須是同一個 transaction,否則中途失敗會留下餘額已變、交易仍 OPEN 的不一致狀態。
"""
import os
from contextlib import contextmanager

import psycopg2
from dotenv import load_dotenv

load_dotenv()


class DatabaseConfigError(RuntimeError):
    """連線設定不完整時拋出,訊息直接告訴維運要補哪個環境變數。"""


DB_CONFIG = {
    "dbname": os.getenv("DB_NAME", "agmcis_db"),
    "user": os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD", ""),
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", "5432")),
}


def get_connection():
    if not DB_CONFIG["password"]:
        raise DatabaseConfigError(
            "DB_PASSWORD 未設定。請在 .env 加入 DB_PASSWORD=<資料庫密碼> "
            "(以及必要時的 DB_NAME / DB_USER / DB_HOST / DB_PORT)。"
            "舊版的密碼曾寫在 db.py 內並提交進 git,請改用環境變數並輪替該密碼。"
        )
    return psycopg2.connect(**DB_CONFIG)


@contextmanager
def transaction():
    """
    用法:
        with transaction() as cur:
            cur.execute(...)
            cur.execute(...)
    區塊正常結束才 commit;任何例外都 rollback,不會留下半套資料。
    """
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                yield cur
    finally:
        conn.close()
