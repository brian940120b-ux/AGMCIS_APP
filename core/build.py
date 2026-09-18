"""
這個行程跑的是哪一版 —— 2026-09-13

═══ 為什麼需要這一支 ═══
執政官 `git pull` 之後看面板,說「完全沒變化」。

而面板每次請求都重新 render、`Cache-Control: no-store`,沒有任何快取。
所以答案幾乎一定是「服務還在跑舊的程式碼」—— 但**幾乎一定不是
一定**,而我沒有辦法從那張截圖分辨:

    · 沒 pull 到
    · pull 到了但沒重啟
    · 重啟了但失敗、systemd 用舊的行程繼續跑
    · 真的換了,而我改的東西有 bug 沒顯示出來

四種情況長得一模一樣。**分不出來的時候,兩邊都會開始用猜的** ——
我猜他沒重啟,他猜我沒改到。那是一個會來回好幾輪的死結。

所以:把版本印在畫面上。一眼就能分辨。

═══ 為什麼不用 subprocess 叫 git ═══
面板是一個網頁服務,而這個值每個請求都可能被讀。
直接讀 `.git` 的檔案:沒有行程開銷、沒有 PATH 依賴、
在沒有 git 執行檔的容器裡也能動。

讀不到就回 None —— **不要編一個版本號出來**。
一個假的版本號比沒有版本號糟得多:它會讓人相信自己在跑新版。
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]

#: 行程啟動的時間。**這是判斷「有沒有重啟」的關鍵** ——
#: 版本號相同但啟動時間很舊,就是「重啟沒生效」。
STARTED_AT = time.time()


def _head_commit(root: Path) -> str | None:
    """讀 .git 拿到目前的 commit。讀不到回 None,**不猜**。"""
    git = root / ".git"
    if git.is_file():                      # worktree:.git 是一個檔案
        try:
            line = git.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        if not line.startswith("gitdir:"):
            return None
        git = Path(line.split(":", 1)[1].strip())
        if not git.is_absolute():
            git = (root / git).resolve()

    head = git / "HEAD"
    try:
        text = head.read_text(encoding="utf-8").strip()
    except OSError:
        return None

    if not text.startswith("ref:"):
        return text[:12] or None           # detached HEAD

    ref = text.split(":", 1)[1].strip()

    # git worktree:HEAD 在自己的 gitdir,但 refs/ 是**共用**的,
    # 放在 commondir 指到的地方。只找 gitdir 會什麼都找不到 ——
    # 這是我 2026-09-13 第一版真的踩到的。
    roots = [git]
    try:
        common = (git / "commondir").read_text(encoding="utf-8").strip()
        path = Path(common)
        roots.append(path if path.is_absolute() else (git / path).resolve())
    except OSError:
        pass

    for root in roots:
        try:
            sha = (root / ref).read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if sha:
            return sha[:12]

    # 打包過的 ref(git gc 之後 refs/ 底下的檔案會不見)
    for root in roots:
        try:
            rows = (root / "packed-refs").read_text(
                encoding="utf-8").splitlines()
        except OSError:
            continue
        for row in rows:
            if row.startswith("#") or " " not in row:
                continue
            sha, name = row.split(" ", 1)
            if name.strip() == ref:
                return sha[:12]
    return None


#: 這個行程**啟動時**的 commit。
#:
#: ⚠️ 2026-09-18:第一版每次呼叫都重讀 `.git`,所以 `git pull` 之後
#: 不重啟,版本戳會顯示**新的** commit —— 而跑的還是舊的程式碼。
#: 一個會說謊的版本戳,比沒有版本戳危險:它讓「我明明更新了」
#: 變成一句有證據的錯話。
#:
#: 在 import 的時候抓一次就凍住,跟 STARTED_AT 同一個時刻。
RUNNING_COMMIT = _head_commit(BASE)


@dataclass(frozen=True)
class Build:
    commit: str | None
    source_mtime: float | None
    started_at: float
    #: 工作目錄**現在**的 commit(每次呼叫重讀)。
    #: 跟 `commit` 不一樣 = **pull 了但沒重啟**。
    on_disk: str | None = None

    @property
    def uptime_s(self) -> float:
        return max(0.0, time.time() - self.started_at)

    @property
    def stale(self) -> bool:
        """程式碼更新了,但這個行程還在跑舊的。

        **這一格是 2026-09-18 加的,而它要回答的問題已經被問了四輪:**
        「我明明 pull 了,為什麼畫面沒變?」
        """
        return bool(self.commit and self.on_disk
                    and self.commit != self.on_disk)

    def describe(self) -> str:
        """一行,給人看。**讀不到就明講讀不到,不留空白。**"""
        commit = self.commit or "版本不明"
        up = self.uptime_s
        if up < 90:
            age = f"{up:.0f} 秒前啟動"
        elif up < 5400:
            age = f"{up / 60:.0f} 分鐘前啟動"
        else:
            age = f"{up / 3600:.1f} 小時前啟動"
        out = f"{commit} · {age}"
        if self.stale:
            out += (f"  ⚠️ 磁碟上已經是 {self.on_disk} —— "
                    "**你 pull 了但沒重啟**")
        if self.source_mtime:
            out += (" · 原始碼 "
                    + time.strftime("%m-%d %H:%M",
                                    time.gmtime(self.source_mtime)) + " UTC")
        return out

    def to_dict(self) -> dict:
        return {"commit": self.commit, "on_disk": self.on_disk,
                "stale": self.stale, "started_at": self.started_at,
                "uptime_s": round(self.uptime_s, 1),
                "source_mtime": self.source_mtime,
                "describe": self.describe()}


def current(entry: str | os.PathLike | None = None) -> Build:
    """這個行程跑的是哪一版。

    `entry` 給進入點檔案的路徑(通常傳 `__file__`),用來取原始碼時間。
    """
    mtime = None
    if entry:
        try:
            mtime = Path(entry).stat().st_mtime
        except OSError:
            mtime = None
    return Build(commit=RUNNING_COMMIT, source_mtime=mtime,
                 started_at=STARTED_AT, on_disk=_head_commit(BASE))
