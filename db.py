"""
資料庫連線層。

連線參數一律從 agmcis/config/settings.py 讀取(最終來源是環境變數)——
密碼絕對不寫在原始碼裡。

transaction() 是交易系統的必要設施:「改帳戶餘額」與「改交易狀態」
必須在同一個 transaction,否則中途失敗會留下餘額已變、交易仍 OPEN 的不一致狀態。
"""
from contextlib import contextmanager

import psycopg2

from agmcis.config import settings
from agmcis.core.errors import ConfigError


class DatabaseConfigError(ConfigError):
    """連線設定不完整時拋出,訊息直接告訴維運要補哪個環境變數。"""


def db_config():
    """每次呼叫重新從環境變數取值,金鑰輪替不需要改程式。"""
    return settings.db_config()


# 向下相容:backup_system.py / restore_system.py 直接讀這個名稱。
# 它是 import 時的快照;需要最新值請呼叫 db_config()。
DB_CONFIG = db_config()


def get_connection():
    config = db_config()
    if not config["password"]:
        raise DatabaseConfigError(
            "DB_PASSWORD 未設定。請在 .env 加入 DB_PASSWORD=<資料庫密碼> "
            "(以及必要時的 DB_NAME / DB_USER / DB_HOST / DB_PORT)。"
            "舊版的密碼曾寫在 db.py 內並提交進 git,請改用環境變數並輪替該密碼。"
        )
    return psycopg2.connect(**config)


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
