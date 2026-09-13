"""
新系統 — 驗收判準(預先登記)· 2026-09-08

═══ 判準在看到任何結果之前寫死,之後不得修改 ═══

一、時序切分,不是幣種切分。
    舊系統的陪審團按**幣種**切 —— 那對「一連串獨立賭注」是合理的,
    但曝險策略問的是「什麼時候在場」,它的風險是**時間上的過擬合**
    (在已知的崩盤前離場)。所以切分必須按時間:
    前 2/3 為訓練段,後 1/3 為驗證段,驗證段的結論不得回頭改參數。

二、要贏的是**風險調整後**,不是報酬。
    比 Calmar(CAGR ÷ 最大回撤)。單看報酬會選出「滿倉裸曝險」,
    那不是策略,是加大賭注。

三、必須同時在訓練段與驗證段都贏過等權買入持有。
    只贏一段 = 運氣或過擬合。

四、回撤契約沿用執政官既有的數字(畢業契約第四條:<= 15%)。
    我不另立標準。達不到就如實報「贏了基準但仍不可交易」——
    那是必須被看見的資訊,不是可以模糊掉的細節。

五、成本必須已扣(手續費、滑點、持有永續的資金費)。
    未扣成本的比較一律作廢。
"""
from __future__ import annotations

MAX_DD_CONTRACT_PCT = 15.0     # 沿用畢業契約第四條,不另立標準
TRAIN_FRACTION = 2.0 / 3.0     # 前 2/3 訓練,後 1/3 驗證


def split_index(n: int) -> int:
    return int(n * TRAIN_FRACTION)


def verdict(name: str, train: dict, test: dict,
            base_train: dict, base_test: dict) -> dict:
    """對照基準給判決。只報告,不自動晉升(實盤閘門永遠人工)。"""
    def calmar(d):
        return d.get("calmar") if d.get("calmar") is not None else -99.0

    beat_train = calmar(train) > calmar(base_train)
    beat_test = calmar(test) > calmar(base_test)
    dd_ok = (max(train.get("max_dd_pct", 99), test.get("max_dd_pct", 99))
             <= MAX_DD_CONTRACT_PCT)

    if not (beat_train and beat_test):
        v = ("訓練段贏、驗證段輸 —— 過擬合的典型形狀"
             if beat_train else
             ("驗證段贏、訓練段輸 —— 樣本運氣,不是優勢"
              if beat_test else "兩段都沒贏過買入持有"))
        ok = False
    elif not dd_ok:
        v = (f"兩段都贏過買入持有,但最大回撤 "
             f"{max(train.get('max_dd_pct', 0), test.get('max_dd_pct', 0)):.1f}% "
             f"超過契約 {MAX_DD_CONTRACT_PCT}% —— **贏了基準但仍不可交易**")
        ok = False
    else:
        v = "兩段都贏過買入持有,且回撤在契約內 —— 有資格進入前向模擬"
        ok = True
    return {"name": name, "passed": ok, "verdict": v,
            "beat_train": beat_train, "beat_test": beat_test,
            "dd_contract_ok": dd_ok}
