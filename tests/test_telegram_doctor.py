"""Telegram 健檢與設定 · 2026-09-25

═══ 這兩支存在的理由 ═══
2026-09-25 我給執政官的排查步驟是:「去 @BotFather 確認 token,
再開 https://api.telegram.org/bot<TOKEN>/getUpdates 看 chat.id」。

回覆是:**看不懂。**

那是我的問題。他用 iPhone + Termius,不是工程師,而我把讀 JSON、
拼網址這些活丟給他做。**能自動的全部自動,只留「用手機講一句話」
給人做** —— 那一步機器真的做不到(Telegram 規定使用者要先開口)。

═══ 測試守三件事 ═══
一、**四個環節分開診斷**。沒設定 / 鑰匙壞 / 號碼錯 / 真的送不出去,
    這四件事的修法完全不同,而它們在 log 裡長得一模一樣。
    混在一起講,就會變成上一則那種看不懂的步驟。
二、**token 永遠不出現在輸出裡**(§10 / §84)。
三、**壞掉時不寫入**。鑰匙沒驗過就寫進 .env,下次只會得到同一個
    「送不出去」,而且舊的設定已經被蓋掉了。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DOC = ROOT / "scripts/telegram_doctor.py"
SET = ROOT / "scripts/set_telegram.sh"


def _doctor():
    spec = importlib.util.spec_from_file_location("tgdoc", DOC)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_the_token_is_never_printed_in_full():
    """§10 / §84:金鑰不得出現在任何輸出。"""
    m = _doctor()
    tok = "123456789:AAFabcdefghijklmnopqrstuvwxyz0123456"
    out = m.mask(tok)
    assert tok not in out
    assert out.startswith("1234") and out.endswith("4567") is False
    assert "…" in out
    assert len(out) < 20


def test_a_short_string_is_not_half_revealed():
    """太短的時候不要露出一半 —— 那反而洩得更多。"""
    m = _doctor()
    assert "…" not in m.mask("abc")
    assert "abc" not in m.mask("abc")


def test_each_of_the_four_stages_has_its_own_verdict_and_next_step():
    """四個環節分開講,而且每一個都要有**下一步做什麼**。

    使用者要的不是「哪裡壞了」,是「我現在該按哪裡」。
    """
    src = DOC.read_text(encoding="utf-8")
    for stage in ("一、.env 裡有沒有設定", "二、這把鑰匙 Telegram 還認不認",
                  "三、這個機器人看得到哪些對話", "四、真的送一則試試"):
        assert stage in src, stage
    # 三種壞法各有各的結論與步驟
    assert "機器人的鑰匙根本沒設定" in src
    assert "鑰匙是壞的" in src
    assert "鑰匙是好的,但送不到你那裡" in src
    assert src.count("下一步(只有你能做") >= 3


def test_it_explains_why_the_human_must_speak_first():
    """「你要先開口」這件事必須講,不然使用者會卡在原地。

    Telegram 規定:使用者沒先傳訊息給機器人,機器人不准傳給他。
    這是整個流程裡**唯一機器做不到**的一步。
    """
    src = DOC.read_text(encoding="utf-8")
    assert "你要先開口" in src or "先開口" in src
    assert "機器人才被允許傳訊息給你" in src


def test_it_says_which_thing_is_safe_to_paste_and_which_is_not():
    """chat id 可以貼,token 不行 —— 這個分辨要講清楚。"""
    src = DOC.read_text(encoding="utf-8")
    assert "不要把那串鑰匙貼進跟我的對話" in src
    assert "chat id 只是一串數字,不是密碼" in src


def test_the_setup_script_verifies_the_token_before_writing_anything():
    """**驗過才寫。**

    沒驗就寫,下次只會得到同一個「送不出去」,而且舊的設定已經
    被蓋掉了 —— 使用者比之前更難回頭。
    """
    sh = SET.read_text(encoding="utf-8")
    i_verify = sh.index("先問 Telegram 這把鑰匙認不認")
    i_write = sh.index("TELEGRAM_BOT_TOKEN=${TG_TOKEN}")
    assert i_verify < i_write, "先寫了才驗"
    assert "沒有寫入任何東西" in sh


def test_the_setup_script_reads_the_token_silently():
    """不走命令列參數(會進 history、會被 ps 看到),螢幕也不顯示。"""
    sh = SET.read_text(encoding="utf-8")
    assert "read -rsp" in sh, "token 沒有用靜音輸入讀"
    assert "unset TG_TOKEN" in sh, "用完沒有清掉"


def test_the_setup_script_only_touches_telegram_lines():
    """只動 TELEGRAM_ 那兩行。BINGX_ 那些不能被波及。"""
    sh = SET.read_text(encoding="utf-8")
    assert "sed -i '/^TELEGRAM_BOT_TOKEN=/d;/^TELEGRAM_CHAT_ID=/d'" in sh
    assert "BINGX" not in sh.split("sed -i")[1]


def test_it_can_find_the_chat_id_by_itself():
    """使用者不知道號碼是常態 —— 那就自己去問,不要叫他讀 JSON。"""
    sh = SET.read_text(encoding="utf-8")
    assert "不知道就直接按 Enter" in sh
    assert "getUpdates" in sh
