"""
Migration 的靜態檢查。

`scripts/migrate.py` 的說明寫著「全部 migration 都設計成 idempotent,
重跑不會破壞資料」。在這之前**沒有任何東西在驗證那句話** ——
而一支非 idempotent 的 migration 只會在重跑的時候壞掉,
那通常是在復原或搬機器的時候,也就是最不想遇到意外的時刻。

這些檢查全部是純文字分析,不需要資料庫。
"""
import pathlib
import re

import pytest

MIGRATIONS = pathlib.Path("migrations")
NAME_PATTERN = re.compile(r"^(\d{3})_[a-z0-9_]+\.sql$")


def files():
    return sorted(MIGRATIONS.glob("*.sql"))


def statements(path):
    """去掉註解之後的 SQL 敘述。"""
    text = path.read_text(encoding="utf-8")
    body = "\n".join(
        line for line in text.splitlines()
        if not line.strip().startswith("--")
    )
    return [s.strip() for s in body.split(";") if s.strip()]


# ---------------- 命名與順序 ----------------

def test_there_are_migrations():
    assert files(), "migrations 目錄是空的"


def test_every_filename_sorts_correctly():
    """
    runner 用檔名排序決定執行順序。三位數補零可以撐到 999;
    一支叫 `10_foo.sql` 的會排在 `009` 之前,而它可能依賴 009 建的表。
    """
    for path in files():
        assert NAME_PATTERN.match(path.name), (
            f"{path.name} 不符合 NNN_name.sql —— 檔名決定執行順序"
        )


def test_no_duplicate_numbers():
    numbers = [NAME_PATTERN.match(p.name).group(1) for p in files()]
    duplicates = {n for n in numbers if numbers.count(n) > 1}

    assert not duplicates, f"重複的編號:{sorted(duplicates)}"


def test_numbers_are_contiguous():
    """
    有缺號代表有人刪掉了一支。runner 用**檔名**記錄已套用,
    所以刪掉的那一支在正式環境仍然記著,而新環境不會套用它 ——
    兩邊的 schema 會不一樣,而且沒有人會發現。
    """
    numbers = sorted(int(NAME_PATTERN.match(p.name).group(1)) for p in files())

    assert numbers == list(range(1, len(numbers) + 1)), (
        f"編號不連續:{numbers}。刪掉的 migration 在已部署的環境仍然記著"
    )


def test_the_sorted_order_matches_the_numeric_order():
    by_name = [p.name for p in files()]
    by_number = sorted(
        by_name, key=lambda n: int(NAME_PATTERN.match(n).group(1)))

    assert by_name == by_number


# ---------------- Idempotent ----------------

def _head(statement, words=6):
    return " ".join(statement.split()[:words]).upper()


@pytest.mark.parametrize("path", files(), ids=lambda p: p.name)
def test_creates_are_guarded(path):
    """
    重跑時 CREATE TABLE / INDEX 沒有 IF NOT EXISTS 會直接失敗,
    而失敗發生在**這一批的中間** —— 前面幾條已經套用了,
    schema_migrations 卻還沒記錄,於是下一次重跑又從頭來。
    """
    for statement in statements(path):
        upper = statement.upper()
        if upper.startswith("CREATE TABLE") or "CREATE INDEX" in _head(statement) \
                or "CREATE UNIQUE INDEX" in _head(statement):
            assert "IF NOT EXISTS" in upper, (
                f"{path.name}: 缺 IF NOT EXISTS —— {_head(statement)}"
            )


@pytest.mark.parametrize("path", files(), ids=lambda p: p.name)
def test_add_column_is_guarded(path):
    for statement in statements(path):
        if "ADD COLUMN" in statement.upper():
            assert "IF NOT EXISTS" in statement.upper(), (
                f"{path.name}: ADD COLUMN 缺 IF NOT EXISTS"
            )


@pytest.mark.parametrize("path", files(), ids=lambda p: p.name)
def test_backfills_only_touch_rows_that_have_not_been_filled(path):
    """
    回填用的 UPDATE 必須有 `WHERE ... IS NULL`。

    沒有那個條件的話,重跑會把**已經被系統更新過的值蓋回去** ——
    例如 007 的 original_stoploss:重跑一次,每一筆的原始停損都會
    變成現在的移動停損,而 R 倍數的目標會跟著整個飄掉。
    那不是失敗,是安靜的資料損毀,比失敗糟得多。
    """
    for statement in statements(path):
        if not statement.upper().startswith("UPDATE "):
            continue

        upper = statement.upper()
        assert "WHERE" in upper, f"{path.name}: UPDATE 沒有 WHERE"
        assert "IS NULL" in upper, (
            f"{path.name}: 回填的 UPDATE 缺 `IS NULL` 條件,重跑會覆蓋現值 —— "
            f"{_head(statement, 8)}"
        )


@pytest.mark.parametrize("path", files(), ids=lambda p: p.name)
def test_no_destructive_statements(path):
    """
    DROP / TRUNCATE / DELETE 不該出現在 migration 裡。

    這個系統的回滾方式是 `git checkout` 上一個版本再重啟,
    **migration 不回滾**(見 docs/DEPLOYMENT.md)。那個做法成立的
    前提就是沒有任何一支會刪東西:舊程式碼看不到新欄位但不會壞,
    而一個被刪掉的欄位會讓舊程式碼直接掛掉。
    """
    for statement in statements(path):
        head = _head(statement, 3)
        for forbidden in ("DROP TABLE", "DROP COLUMN", "TRUNCATE", "DELETE FROM"):
            assert forbidden not in head, (
                f"{path.name}: 有破壞性敘述 {forbidden} —— "
                f"這個系統的 migration 不回滾,見 docs/DEPLOYMENT.md"
            )


# ---------------- 內容 ----------------

@pytest.mark.parametrize("path", files(), ids=lambda p: p.name)
def test_every_migration_explains_itself(path):
    """
    每一支都要有註解說明它為什麼存在。半年後看一支只有 SQL 的
    migration,沒有人記得那個欄位是為了解決什麼問題。
    """
    text = path.read_text(encoding="utf-8")
    comments = [
        line for line in text.splitlines() if line.strip().startswith("--")
    ]

    assert len(comments) >= 3, f"{path.name} 幾乎沒有註解"


@pytest.mark.parametrize("path", files(), ids=lambda p: p.name)
def test_parentheses_are_balanced(path):
    """最便宜的語法檢查。少一個括號在正式環境跑起來才會發現。"""
    text = "\n".join(
        line for line in path.read_text(encoding="utf-8").splitlines()
        if not line.strip().startswith("--")
    )

    assert text.count("(") == text.count(")"), f"{path.name} 括號不對稱"


def test_the_runner_applies_them_in_filename_order():
    """
    這一條把上面所有的順序檢查跟 runner 的實際行為綁在一起 ——
    runner 改成別的排序方式時,這裡會紅。
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "migrate_script", "scripts/migrate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    class FakeCursor:
        def execute(self, *args, **kwargs):
            pass

        def fetchall(self):
            return []

    todo = module.pending(FakeCursor())
    assert [p.name for p in todo] == [p.name for p in files()]


def test_the_newest_migration_is_the_one_the_operator_list_names():
    """
    docs/OPERATOR_ACTIONS.md 告訴使用者要跑到哪一支。那份文件
    落後的時候,使用者會以為自己跑完了。
    """
    newest = files()[-1].name
    text = pathlib.Path("docs/OPERATOR_ACTIONS.md").read_text(encoding="utf-8")

    assert newest in text, (
        f"最新的 migration 是 {newest},但操作清單沒有提到它"
    )
