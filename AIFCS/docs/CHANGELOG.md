# CHANGELOG

版本標記（brief PART 34）。**標籤的 SHA 寫在這裡**，因為標籤 ref 從雲端環境推不上
遠端（代理在 refs/tags 更新時切斷連線，分支本身推得上去）。有這張表，回滾點不依賴
標籤是否存在於遠端 —— `git checkout <sha>` 永遠有效。

在筆電上重建標籤（若遠端沒有）：

```
git tag -a AIFCS_V1_STABLE 436d372 -m "AIFCS 1.x, last state before the 2.0 work"
git tag -a AIFCS_V2_AUDIT_COMPLETE 33c5be9 -m "PHASE 0 audit accepted; P0 fixed; experiments in place"
git push origin --tags
```

| 標籤 | Commit | 日期 | 意義 |
|---|---|---|---|
| `AIFCS_V1_STABLE` | `436d372` | 2026-09-30 | 2.0 動工前的最後狀態。程式碼與 `016432c` 相同（該 commit 只加了稽核報告）。已知：`competition/trace.py` 遮蔽 stdlib（潛在，當時未觸發）。**回滾點。** |
| `AIFCS_V2_AUDIT_COMPLETE` | `33c5be9` | 2026-09-30 | 稽核被接受；P0 已修（`trace.py` → `roundtrace.py`）；Experiment Manager、來源登錄、`EXP-001` 就位。完整套件 1,107 通過 / 0 失敗。 |

## 2026-10-01 — EXP-001 / EXP-002 結案（筆電配對比較）

- 筆電仍在 `6a32038`（無 `scoreboard.py`），改用當時的 `evaluate.py` 跑同一批 20 回合（seed 1000，對手 v4／v5，`results/exp002_old.json`）。`features.py`、`scoring.py`、`safety.py` 自該 commit 起未改，所以分數與新 code 相同；只差六個腳本對手那張表。
- **EXP-001 → keep**：主辦方原配方 vs v4 5%／墜毀 75%／−721，vs v5 10%／90%／+96，最佳瞄準 11.1°／25.8°，cone 0；置中搖桿同一批回合 100% 墜毀。是地板，永不晉升。
- **EXP-002 → reject**（作為 v6 的替代）：v8_pool vs v4 60%／+585／cone+ 0.62，v6 同一批 90%／+5,501／2.22；vs v5 75%／+246／0.00，v6 75%／+1,001／1.10。假設的核心（cone+ 超過 v6）不成立。保留為池成員與 EXP-003/005/006/007 的比較基準。
- 筆電與 Kaggle 同 seed 數字略不同（v8_pool vs v4：60%／+585／0.62 對 55%／+426／0.22）：JSBSim 版本（筆電 1.3.1 GitHub build）與 CPU／GPU 推論差異。順序與結論相同。
- 欠：兩個實驗的六對手 scoreboard 列，等筆電拿到新 code（熱點 `git pull` 或 Kaggle V8 Output 的 `AGMCIS_APP.bundle`）再補。

## 2026-10-03 — `--reward gunsnap`（H7）：錐內付 HOST 量到的 10,000/s；EXP-013 宣告

- ADT 兩隊的 gun-snap 項（Γ(d)·[1−S(θ̄,1e5,1/180)]）就是「射程內、錐內給一步階」，等於 HOST 的錐內指示項。我們的 `shaped` 本來就透過分數項含這一項，差別只在付多少：公告 2,000/s，對上瞄準斜率 400/s，錐只值斜率的五倍；HOST 兩次量到 10,000/s。冠軍對真 HOST 的兩局都是「射程內 149 s、錐內 1.75 s」：穿過去，沒人付它留下。
- `RewardMode.GUNSNAP` = `GunsnapReward(**kw)` → `ShapedReward` 但 `weights.attack_time` 換成 `ScoringWeights.host_measured().attack_time`（position 權重不動，一次只差一件事）。tier research。`gym_env` 會建；`train.py --reward gunsnap`。測試：錐內且射程內每幀差剛好 (10,000−2,000)/60，錐外、射程外差 0；position 權重照舊；gym 建得出來。
- 宣告 `EXP-013-gunsnap-reward`（session `v9_gunsnap`）：EXP-010 配方與池，只換獎勵。EXP-012 跑完就開。
- `test_make_vec_env_holds_the_limit...` 在雲端失敗是因為這台沒裝 SB3，與本次無關（改動前一樣失敗）。

## 2026-10-03 — v9_lookahead 對真 HOST：贏 39,810 對 406，HP 180 對 75，錐內 1.75 s

- `play.bat models/competition/v9_lookahead --record`：18,937 封包、0 壞包、最差決策 2.3 ms／平均 0.44 ms、3,157 次決策（每 6 幀一次）。40 維觀測沒有拖慢。
- `hostcsv`（`飛行競賽Host端Record_20261003_2052.csv`）：18,002 幀、300 s；起始 1,545 m、航向 260／80、同高 11,376 ft、340 kt，機頭 90° 離；射程內 149.4 s、一度錐內 1.75 s、最佳瞄準 0.03°、max |G| 8.5；最近 240 m（上次 v9_doctrine 4.3 m）；15 m 以內 0 s。HOST 的 Final 39,810 對 406，HP 180 對 75（掉 105 = 105 幀），W_time 10,000/s 再次量到；HOST 用 host 正規化（差 0.0000）。
- 判讀：和 v9_doctrine 那局（HP 49、錐內 2.18 s）一樣是「咬得到、咬不滿 3 秒」。射程內 149 秒卻只累積 1.75 秒錐 → 問題在**持續**不在瞄準。H7 錐邊階躍獎勵（gunsnap）是下一個要做的獎勵項，排在 EXP-012 之後。

## 2026-10-03 — EXP-010／011 結案：v9_lookahead 是冠軍、暫定當天模型；EXP-012 宣告

- 筆電、修正後評測器。六對手板 won：v9_lookahead 100/67/83/100/100/100、v9_defmix 100/50/100/100/100/100、v9_doctrine 100/33/100/100/100/67。cone：lookahead reference 3.00（擊殺 1/6）、wanderer 3.00（擊殺 2/6）；defmix 六個對手中五個非零；doctrine 三個。pursuit 分差：lookahead +121,365、defmix +41,753、doctrine +7,050。
- 配對 vs v9_doctrine（30 seed 兩座，60 回合）：**v9_lookahead 67%／+58,815**（兩座各 67%）、v9_defmix 63%／+54,314。兩個都明顯贏舊冠軍（56% 門檻）。
- **EXP-010 keep → 冠軍、暫定當天模型 = v9_lookahead**；**EXP-011 keep** 當材料（pursuit 33→50、錐最廣）。兩者共同代價：墜毀 17–27%，是下一個要量的東西（地板參數／守勢起始靠地）。
- 宣告 **EXP-012-lookahead-defmix-v9pool**（session `v9_combo`）：lookahead + defmix，池 = v5 + 三個 v9 + 腳本十一個。Kaggle 要先把 `v9_lookahead/`、`v9_defmix/`、`v9_doctrine/`（各含 card.json）上傳到 Dataset `aifcs_pool`。
- 當天 play.bat 改用 `models\competition\v9_lookahead`；真 HOST 預演要用它再跑一次（lookahead 觀測 40 維、決策時間可能略增，selftest 會量）。

## 2026-10-03 — 座位問題結案：兩條推論路徑相同；自打 60 回合 48%

- 筆電 `actorparity`：v9_doctrine 四通道最大差 4.0e-06／2.9e-06／1e-23／1.0e-06，v6 4.7e-06／2.9e-06／1e-23／1.4e-06 → **同一個策略，只差 float32 捨入**。
- v9 自打 `--both-seats`，seed 2000，60 回合合計 **48%／−1,254**（原位 50%／+943，另一座 47%／−3,450）→ 評測器公平。先前五次合計 37/100 是抽樣加混沌：1e-6 的推論差足以讓 300 秒的局走向不同結局，所以同 seed 兩座不會剛好抵銷，每局是獨立樣本。
- **結論**：20 回合的勝率標準誤差約 ±11%。之後比兩個模型一律 `--rounds 30 --both-seats`（60 回合，±6%）。
- v9_doctrine 對 v6 目前所有有效樣本：60 回合 v9 贏 27 場（45%），分差平均約 +3k，錐內 cone+ 1.20～1.77 對 v6 0.00～1.10。**published 起始下兩者是平手**；差別在六對手板（v9 錐內、擊殺 1 次、分差五項高；v6 贏 pursuit）。暫定當天模型維持 v9_doctrine，等 EXP-010／011。

## 2026-10-03 — `--both-seats` 的結果：模擬器對座位交換完全對稱；剩下的差異只可能在推論路徑

- 筆電：v9_doctrine vs v6 `--both-seats` 40 回合合計 **35%／−10,881**（原位 40%／−31,823，另一座 30%／+10,062）。v9 自打 `--both-seats` 20 回合合計 **40%／−23,593**（兩列 −40,623 與 −6,563），理論上同一策略坐兩邊的兩列分差必須互為相反數，實際不是。
- 雲端：手寫策略坐兩邊、同 seed 交換座位、300 秒整局、6 個 seed，**每一局兩列分差之和都是 0**（例：−10,604／+10,604、−35,713／+35,713）。所以 `CompetitionRound`、`play_round`、`PolicyOpponent`、計分對座位交換是精確對稱的。
- 剩下兩座不同的地方只有一處：受測方用 SB3 `model.predict`（torch、float32），池對手用 `NumpyActor`（float64）。數學上相同、單元測試量到 1.8e-07；但自打兩列不互為相反數，表示兩條路徑在真實 checkpoint 上有差。差 1e-7 只會讓混沌局去相關（期望仍 50%）；差到 1e-2 就是兩個不同的策略。這台機器沒有 SB3，無法在雲端量。
- 加 `competition/actorparity.py` + `scripts/actorparity.bat`：載入 checkpoint，飛 30 秒把每幀觀測留下，兩條路徑各算一次，印每個通道的最大差；≤ 1e-4 判同一策略（exit 0），否則 exit 2。在筆電跑。
- 五次量測藍方合計 37/100（公平時約 0.6% 機率），判讀暫留：等 parity 結果決定是「推論路徑的 bug」還是「抽樣」。

## 2026-10-03 — 兩個修正後的配對；`--both-seats` 讓同一場交戰從兩個座位各飛一次

- 兩個座位修正後重量（筆電，seed 1000，20 回合）：v9_doctrine vs v6 **40%／−31,823／墜毀 15%／cone+ 1.20／5° 內 1.9 s**；v6 vs v9_doctrine **35%／−31,754／墜毀 20%／cone+ 0.00／0.5 s**。兩邊加起來 75%，分差都是 −31.8k：**現在換紅方略佔便宜**，或只是 20 回合的抽樣（兩個 ≤8/20 同時出現約 3%）。
- 同一批 seed 的兩次跑其實不是同一場交戰的兩個座位：受測方永遠在圓心、航向先抽。所以加 `EnvConfig.swap_seats`（同一 seed，我方起在對方的起點與航向、對方起在我方的）和 `evaluate --both-seats`（每個池對手多飛一列「other seat」並印兩列合計）。測試：交換後的起點對調；同一個策略坐兩邊時，交換局的我方分 ≈ 原局的對方分（3 秒回合，誤差 2%）。
- 這個旗標同時是座位公平性的檢查：同一個模型自打加 `--both-seats`，合計必須是 50%。
- 目前的判讀：v9_doctrine 與 v6 在 published 起始下接近平手（合計 v9 52.5%），但 v9 錐內時間多（cone+ 1.20 對 0.00、5° 內 1.9 s 對 0.5 s）、六對手板分差五項高；v6 只贏 pursuit。暫定當天模型仍 v9_doctrine，待 `--both-seats` 與 EXP-010/011 的結果。

## 2026-10-03 — 池對手修正：每幀編碼、每 action_repeat 幀決策（藍方座位優勢的第二個原因）

- 決策率修正後重量（筆電，seed 1000，20 回合）：v9_doctrine vs v6 75%／+79,866／墜毀 20%／cone+ 0.53；v6 vs v9_doctrine 80%／+45,719／墜毀 10%／cone+ 1.10。兩邊加起來 155%，座位優勢還在。
- 用同一個手寫策略坐兩邊各量 36 回合（action_repeat／地板／G 限各自開關）：藍方 56%，沒有明顯偏差 → 搖桿、地板、G 限都公平，問題在**學出來的模型才讀的東西**。
- **原因**：`PolicyOpponent` 只在決策幀才呼叫 `encoder.encode`。`extended` 的四個速率欄是「和上一次 encode 的差 × 60」，時鐘欄數的是 encode 次數。池對手因此看到 **6 倍大的速率、慢 6 倍的時鐘**，飛的不是訓練出來的那個策略。受測方（評測器、訓練包裝器、當天 client）都每幀編碼，所以座位不對稱。
- **修正**：`PolicyOpponent.__call__` 每幀編碼、每 `action_repeat` 幀才 predict，與 `CompetitionClient` 相同。測試 `test_a_pool_opponent_encodes_every_frame_and_decides_every_action_repeat`。
- **影響**：所有 `extended` 模型當池對手的結果都偏弱——包括**訓練時的池**（EXP-002 v8_pool、EXP-008 v9_doctrine 的池對手 v4/v5/v6/v8）和評測的 `--opponent-pool` 列。腳本對手（reference/pursuit/…/doctrine 四個）用無狀態幾何，不受影響，六對手板有效。配對比較要再重量一次。
- 六對手板重量（決策率修正後，`results/exp008_009_v2.json`）：v9_doctrine won 100/33/100/100/100/67，cone reference 0.90／energy 1.98／wanderer 3.00（擊殺 1/6）；v9_wez 83/33/100/83/83/100，cone reference 3.00（擊殺 1/6）；v6 100/67/100/100/83/100，cone 全板 ≤ 0.08。分差 v9_doctrine 五項最高，pursuit 一項 v6 最高（+41,725 對 +7,050）。

## 2026-10-03 — 評測器修正：受測方每 action_repeat 幀決策一次（藍方座位優勢的原因）

- **原因找到**：`evaluate.play_round` 每一幀（60 Hz）都問策略一次，而池對手（`PolicyOpponent`）和當天的 client 都照卡片的 `action_repeat`（v6/v8/v9 皆為 6 → 10 Hz）持住決策。同一個策略坐受測方就多了六倍的反應速度，所以 v6 自打 85%、v9 自打 80%、v9 vs v6 與 v6 vs v9 都 90%。
- **修正**：`play_round` 現在持住決策 `config.action_repeat` 幀，與訓練包裝器（`gym_env.step`）和 `CompetitionClient` 完全相同。測試：`test_a_round_asks_the_policy_once_per_decision_not_once_per_frame`（2 秒回合 120 幀 → 20 次決策）。
- **影響**：本 commit 之前所有 scoreboard／evaluate 的「受測方」數字都是 60 Hz 搖桿量出來的，比當天會飛的 10 Hz 搖桿偏好；內建對手列（reference/wanderer/…）和池對手列都受影響，順序可能變。真 HOST 預演（play.bat）不受影響——client 一直是 10 Hz。**exp008_009 / 配對比較需要重量**。
- 筆電更新後重跑：`scripts\scoreboard.bat models\competition\v9_doctrine models\competition\v6 --json results\exp008_v2.json`。

## 2026-10-03 — v9_doctrine 對 v6 配對評測：暫定當天模型 = v9_doctrine，帶兩個代價

- 同一批 20 回合、各自坐藍方：v9_doctrine vs v6 90%／+87,946／cone+ 1.08／墜毀 10%；v6 vs v9_doctrine 90%／+48,781／0.42／0%。兩邊都 90% → **藍方座位有優勢**，won 分不出高下（待查：池對手怎麼被飛的）；分差和 cone 分得出：v9 做得多。v9 自打：擊殺 1 次、墜毀 20%。
- 暫定當天模型 v9_doctrine（錐、分差），代價：pursuit 弱、墜毀率 10–20%（v6 0–5%）。EXP-011 補 pursuit；墜毀要另外看。

## 2026-10-03 — 訓練期勝率讀出 pursuit 的洞；`--geometry defmix`；EXP-011

- EXP-008 訓練最後 50 回合：總 92%，**pursuit 50%**，其餘 88–100%；EXP-009 寫 100%（含 pursuit 100%）但板上 pursuit 33%：從錐內開始的回合裡「贏 pursuit」沒有意義，課程的意思就在這裡。結論：v9_doctrine 學會追、沒學會被追。
- **新**：`--geometry defmix` = 50% published／25% defensive／25% wezdef，`MIXTURES` + `resolve_geometry()` 在 reset 先抽具體幾何再決定距離；`mix` 不動（避免重播 EXP-007 的種子）。測試 2 個。
- 紀錄 `EXP-011-defensive-starts`（v9_defmix = EXP-008 只換起始）。

## 2026-10-03 — v9_doctrine 對真 HOST：第一次扣血；錐、HP、W_time 量到；4 m 擦過

- 回合：19,353 幀、0 錯誤、最慢決策 2.7 ms。HOST：對手 HP 180 → 49（扣 131），我們錐內 2.18 s = 131 幀，**HP 每幀扣 1、只在我們的 `AttackEnvelope` 判定的幀**（131／131／0），U02 半角 1° 與 U03 累積確認。**W_time = 10,000／秒**（錐內每幀 Final 多 166.667），U06 部分關；`ScoringWeights.host_measured()` 新增，評測預設不變。
- **相撞風險**：最近 4.3 m，15 m 內 2.43 秒，公開版 HOST 沒反應；規則是比優勢分。記在 `CONFORMANCE.md` F2 與 `TACTICS.md`。
- `hostcsv.py` 摘要加 `foe_hp_lost`、`attack_weight_per_s`、`seconds_under_15m`；第二回合 11 列樣本進 `backend/tests/data/`，測試 4 個。

## 2026-10-02 — 六對手板（修正後計分的第一張）：EXP-008 keep、EXP-009 keep（課程階段）

- 置中搖桿對 reference 只剩 17%（之前 83%）：位置項修正後 won 又有意義了。
- **v9_doctrine**：won 100/17/100/100/100/83；cone reference 2.42、wanderer 1.63；分差 reference +225,170（v6 +60,307）。弱點 **pursuit 17%**（v6 50%、v8_pool 83%），唯一會主動攻擊的對手。決定規則第二款（不輸 v8_pool 超過一回合）在 pursuit 不成立，**刻意覆寫**：錐是別的實驗都沒動過的東西，pursuit 是可命名、可補的洞。keep 為新基準與冠軍候選；當天模型等配對評測、HOST 預演、pursuit 補強。
- **v9_wez**：cone reference 3.00、wanderer 3.00，**各擊殺 1/6**，bench 上第一次；但 pursuit 33%（分差 −649）、energy 50%。keep 為課程第一階段，下一步 published 幾何續練。
- 下一步：配對評測 v9_doctrine vs v6 雙向；真 HOST 預演；看兩個 log 裡訓練期對 pursuit 的勝率，決定 EXP-011（pursuit 補強）怎麼開。

## 2026-10-02 — EXP-008／009 Kaggle 結果：第一次擊殺

- **EXP-008 v9_doctrine**：vs v4 100%、killed 5%（20 回合中 1 次）、cone+ 3.00 s、錐內平均 0.88 s、射程內 5° 以內 17.7 s、最近 0.0°；vs v5 100%、cone+ 0.37。v6 最好 2.22、v8_pool 0.22。
- **EXP-009 v9_wez**：vs v4 95%、1 次擊殺、cone+ 3.00、5° 內 21.5 s；vs v5 85%、墜毀 15%、cone+ 0.70。從錐內開始多了靠近錐的時間、少了存活。
- 兩個都同時含兩個變因（手冊對手進池、位置項修正後的 shaped 獎勵），紀錄設計上就是如此（EXP-008 是新基準）；分差換了尺度（+220,514）。決定等筆電六對手板。
- 下一步：v9_doctrine 放進筆電，`play.bat` 對真 HOST 預演一回合看有沒有擊殺（順便量 W_time／HP）；Kaggle Dataset 加 v9_doctrine 供 EXP-004 exploiter 用。

## 2026-10-01 — EXP-005（位能 shaping）結案：reject

- Kaggle V8（7.05 h，commit fda84e3）：v8_potential vs v4 65%／−321／cone+ 0.00／best 12.0°；vs v5 60%／−66／0.23／best 0.2°。同板 v8_pool：55%／+426／0.22；60%／+197／0.00。錐沒動（兩邊都是雜訊等級），對 v4 完全沒瞄到（最近 12°）。won 在置中搖桿地板（80%）之下。H4 以這個實作（Φ = 2000·aim·range_factor 疊在 shaped 上）不買到錐內時間。
- 注意：這一跑在位置項修正前，margin 不可比；cone／best 可比。不重跑，EXP-008 起的線取代它當基準。

## 2026-10-01 — Kaggle 的實驗改由 notebook cell 選（`AIFCS_EXPERIMENT`）

- `kaggle/aifcs_train.py` 的 `EXPERIMENT` 改讀環境變數 `AIFCS_EXPERIMENT`，沒設就用預設（現在是 `EXP-008-doctrine-pool`）。筆電連不上 GitHub，改第 60 行再 push 這條路走不通；在 cell 第一行設變數，三個實驗 = 三個 version，不用三個 commit。`bootstrap.py` 說明同步。測試一個。

## 2026-10-01 — `--observation lookahead`：把「預測未來軌跡」做進觀測（H16）

- 出處三個：Heron 說 10 Hz 下 agent「要知道未來 3 秒的軌跡才留得住 1° 錐」（SRC-020）；手冊 4.3.9.7.2.2.2「預判他出 jink 的位置，先把砲口放到 lead」（SRC-022）；韓國冠軍餵的是瞄準／距離的 margin 不是現值（SRC-012）。三者都是 lead computing：射擊解是預測，不是量測。
- **新**：`backend/competition/lookahead.py`，`LookaheadEncoder(ExtendedEncoder)`：extended 30 維之後，對 1 s 與 3 s 兩個視野，把兩機沿各自速度向量推算（等速推算，轉彎時會錯，這是刻意的：便宜的預測給它，修正留給它學），在**當下**機體座標報預測方位角／仰角／距離／track／閉合率，共 10 維，寬度 40。只用 OBS 封包裡有的，不送 HOST（R1）。
- `environment.OBSERVATIONS` 加 `lookahead`，`build_encoder`／`observation_width` 認得；`mirror.observation_signs(40)`：只翻預測方位角。評測照 card 重建，舊 pool 可用。
- 紀錄 `EXP-010-lookahead-observation`（v9_lookahead = EXP-008 配方只換觀測）。測試 8 個。

## 2026-10-01 — **P0：計分的位置項反了**；手冊對手進池；WEZ／CZ 起始；實測轉彎表

- **P0**：`Geometry.aspect_angle_deg` 是範例環境的角（目標正飛離 = 180、正對我 = 0），`scoring.position_advantage` 直接當公告的 AA（機尾起算）用，所以位置項反向：對頭給滿分、咬尾給零。影響：所有 `margin` 數字（evaluate／scoreboard／Kaggle）與 v6、v8_pool 的 `shaped` 獎勵（W_pos=10 的項）；`won`／`cone`／`killed` 不受影響。發現途徑：寫 HOST 對手時用 encoder 探針（目標在前方飛離 → P(t)=1.0，正對我 → 2.0）。修法：`position_advantage` 用 `180 − |aspect|`，`Geometry` 文件改寫；三個回歸測試（合成幾何、真 encoder、HOST 六列樣本）。`CONFORMANCE.md` C 表 AA 列改成「已修」；`EXPERIMENT_MANAGER.md` 加分差注意事項。
- **新**：`adversaries.DOCTRINE` 四個會「讀對手」的腳本對手：`flare`（後方 1,200 m 內被逼近就收油門拉高逼 overshoot，過去後反咬）、`jinker`（對方機頭在 10° 內且 1,200 m 內才出平面：55° 坡度向下 1.5 s 再拉回）、`reversal`（近距離對方從一側換到另一側就反轉，否則是 break turn）、`leadturn`（2 km 外 pure pursuit、2 km 內轉進去打一圈戰）。`SCRIPTED = ADVERSARIES + DOCTRINE` 供池與評測；**bench（ADVERSARIES）不變**。
- **新**：`--geometry wez`（700–2,800 ft 正後方、錐內、目標飛離）、`wezdef`（鏡像）、`cz`（2,500–4,500 ft、機尾後 25–45°）；`RANGED_GEOMETRIES` 覆寫起始距離，其他幾何距離不變。
- **新**：`backend/competition/turntable.py`：量 JSBSim F-16 的瞬時轉率／G／半徑對進入速度。結果進 `AIRCRAFT_KNOWLEDGE_BASE.md` 九：最快 14°/s 在 390–440 KCAS、半徑約 3,000 ft、6 秒內最大 7.6 G（9 G 限制器沒觸發）；真機手冊 20.1°/s、1,942 ft。**規則查過**：公告六.1–12 沒有限制訓練用什麼資料；限制是自家 PART 43（未驗證不進模型），所以用量出來的表，手冊數字只對照。
- 紀錄 `EXP-008-doctrine-pool`（v9_doctrine）、`EXP-009-wez-start`（v9_wez）。路線圖 H8／H12／H15 已做、H13 一半。
- 測試：adversaries +5、geometry +9、scoring 改 4 加 1、turntable 2。

## 2026-10-01 — 美軍 F-16 戰技手冊 → `docs/TACTICS.md`（SRC-022～026）

- f-16.net 的 MCH 11-F16 Vol 5 PDF 只有目錄；Internet Archive 的 AFTTP 3-3 Vol 5（1999）全文 OCR 讀完第四章：幾何、轉彎性能（半徑 3,000 ft／Tc 6,000 ft、corner plateau 330–440 KCAS、公式）、攻擊 BFM（入口、450–480 KCAS、閉合 5% 法則、1,000–2,000 ft 致命射程、壓到 2,000 ft 再脫）、防守 BFM（jink 時機 3,000–4,000 ft、Lv 偏 45–60°、rolling duckunder 1,000–1,500 ft、nose-counter、利用 overshoot 看 LOS rate）、高角度合併（一圈比半徑、兩圈比轉率、前兩個 pass 別想贏完）、機砲（1–2 秒 burst）。控制區尺寸 2,500–4,500 ft／25–45° 來自 AETC TTP 11-1 鏡像。
- `docs/TACTICS.md`：把規則翻成戰術語言（累積 3 秒 → 短 snap 加總；HOST 的 /180 讓「待在後半球」本身值錢；公開版開局 = 高角度合併），五節手冊摘要，ADT 的 AI 行為對照，H12–H15。路線圖加四列。
- 來源另登錄 GTRI 賽後文（SRC-024）、Gorton 綜述（SRC-025，僅摘要）、Chen 2025 AOS（SRC-026，MDPI 403，僅摘要）。

## 2026-10-01 — 美軍 DARPA AlphaDogfight Trials 文獻（SRC-018～021）

- 主辦方的 WEZ（2°、500–3000 ft、300 s）逐字等於 DARPA ADT 的定義；ADT 文獻因此是同一幾何的第一手資料。來源登錄：Lockheed 亞軍論文 arXiv 2105.00990（PDF 讀完，獎勵八項公式、SAC 超參、對手課程、GPU 決定性截斷）、JHU APL 技術文摘 36(2)（環境、四層對手、Elo 自我對弈、賽果 16–4／5–0）、Aviation Week 的 Heron 訪談（10 Hz 平順、102 agent league、40 億樣本）、DARPA ACE 頁。Heron 官網兩次 503。
- `docs/PRIOR_ART.md` 七：規則對照、對手集對照、兩隊配方、與我們的逐項對照（10 Hz 與 /180 正規化已一致）、新假設 H7–H11（錐邊階躍獎勵、WEZ 起始、寬淺網路、截斷決定性、對手門）。路線圖加五列，全部「未做」。
- 不能用的：ADT 程式碼未公開、DARPA 最終報告 FOUO、X-62A 勝負未公布。設計層重做，不抄。

## 2026-10-01 — HOST 的計分公式實測：不是公告寫的那條（C11）

- 用 HOST 的 CSV（18,002 幀）逐幀重建它的 AttackAdvantage／PositionAdvantage／FinalAdvantage：`DF×(180−角)/180`、≥90° 為 0、Final 每幀累加 Att+Pos（含 START 前保持幀）。36,000 個值最大差 0.0000。公告的 `(90−角)/90` 每幀每欄平均差 0.07。細節 `CONFORMANCE.md` F；衝突登記 C11；U01／U05 加註。
- **新**：`scoring.Normalisation`（`announcement` 預設不變、`host` 實測），`position_advantage(geometry, normalisation)`、`SideScore(normalisation=…)`。預設沒改：比賽日 HOST 版本未知，要改是人的決定。
- **新**：`backend/competition/hostcsv.py` + `scripts/hostcsv.bat <CSV>`：讀 HOST CSV，兩種算法各重建一次、報最大差、印起始幾何／距離／錐內秒數／HOST 最終分。固定樣本 `backend/tests/data/host_round_2026-10-01_sample.csv`（6 列，我們自己跑出來的資料）。
- 同一份 CSV 看到的：公開版起始幾何是相反航向並排 1,702 m（不是 3/6/9 千呎）；內建對手平飛 PID；v6 先逃 10.5 km 再追回 464 m，機頭最近 4.87°，沒進錐；HOST 判 v6 贏 16,998 對 404。W_time、W_G 仍未知（這回合沒進錐沒超 G）。
- 測試：`test_competition_hostcsv.py` 13 個、`test_competition_scoring.py` +2。

## 2026-10-01 — v6 第一次對真實 HOST 飛完整回合；一幀決策超時的兩個對策

- `play.bat models/competition/v6 --record` 對 `JSB_host_GUI_publish.exe`：19,146 幀、0 錯誤、state 2、3,191 次決策、平均 0.37 ms、最慢 24.6 ms（預算 16.7）。報告與逐幀 `.jsonl` 在筆電 `data/play/`。
- 對策一：`serve()` 期間 `gc.disable()`，結束 `gc.enable()`。`--record` 每幀留一個 dict，第二代回收掃全部物件的時間隨回合變長；每幀路徑沒有循環引用，refcount 就夠。測試鎖住：policy 執行時 collector 關、迴圈結束後開。
- 對策二：Windows 上 `SetPriorityClass(HIGH_PRIORITY_CLASS)`（不用 REALTIME，失控會鎖住要按 START 的那台機器）。非 Windows 或失敗只回報一行，不擋啟動。
- 量了：第二回合 18,451 幀、最慢 13.6 ms（< 16.7）、平均 0.42 ms。兩回合 24.6 → 13.6，方向對；樣本小，每次預演都記。

## 2026-10-01 — 六對手 scoreboard 補進 EXP-001／EXP-002；`play.bat --selftest` 在筆電 PASS

- 筆電 `scripts/scoreboard.bat v8_pool official_sac_baseline v6`（6 回合×6 對手，seed 1000）。置中搖桿＋地面防護對六個腳本對手贏 67–83%，所以這張板的 won 幾乎是地板在贏；看分差和 cone。全板無擊殺。
- EXP-002 決定規則自算：won ≥ v6 3/6、cone 贏 1/6 → reject 不變。但 v8_pool 是全板唯一有 cone 時間的（vs reference 0.72 s，分差 +3,812 也是最大）；v6 贏在 pursuit（+7,263）和 wanderer（+1,514）。
- EXP-001：cone 全 0，分差除 pursuit 外與置中搖桿同級。keep 不變。
- `experiments.record_result` 原本整個蓋掉 `results`，會把 Kaggle／配對結果丟掉；改成合併，加測試。這次的列是手動從貼上的表寫進 YAML（`laptop_scoreboard_2026-10-01`），`results/exp002.json` 留在筆電。
- `play.bat models/competition/v6 --selftest` 在筆電：`G-limit 9, ground floor on`、120/120 回覆、最慢決策 1.44 ms（預算 16.7）、PASS。

## 2026-10-01 — 三個 .bat 從錯的目錄跑（scoreboard／experiment／official_audit）

- 筆電首跑 `scripts\scoreboard.bat models\competition\v8_pool …` 回 `FileNotFoundError: models\competition\v8_pool\checkpoint.zip.zip`：bat 先 `cd backend` 再 `python -m competition.scoreboard`，命令列上的相對路徑全部變成 `backend\models\…`。`experiment.bat`（`--scoreboard results\…` 會存錯路徑）和 `official_audit.bat` 同病。
- 三個 bat 改成和 `evaluate.bat`／`play.bat` 一樣：在 AIFCS 根目錄把模組當檔案跑（三個模組本來就有 `sys.path` 自舉）。`official_audit.bat` 的 `--out` 跟著改為 `official_audit`。

## 2026-10-01 — 比賽用程式 `play.py`／`scripts/play.bat`，以及 client 漏接 G 限制

- **新**：`backend/competition/play.py` + `scripts/play.bat <session> [--listen-ip --listen-port --host-ip --host-port --record --selftest]`。載入 `card.json` + `checkpoint.zip`，用 `evaluate.config_from_card` 還原訓練時的飛機（觀測編碼、action repeat、方向舵／升降舵上限、地面防護、G 限制），套進 `CompetitionClient` + `serve`，飛到 Ctrl+C。網路位址以外沒有旗標能改飛機。先做一次 warm-up 決策（第一次 forward pass 最慢）。checkpoint 寬度和 card 的觀測不合就拒飛；少 card 拒飛並列出資料夾內容。報告寫 `data/play/play-<時間>.json`，`--record` 另存每幀 `.jsonl`（和探針同格式，可 `--compare`）。`--selftest` 在本機送 120 幀合成封包，檢查全部回覆且最慢決策 < 16.7 ms。
- **修**：`CompetitionClient` 原本沒有 `g_limit`，`shape_command` 永遠走範例的速度式升降舵限制。訓練環境（`environment.py:569`）有傳 `g_load`/`g_limit`，所以 v6 起每個用 `--g-limit 9` 練的 session 當天都會碰到不是自己練的那支搖桿（高空轉彎率減半）。加 `g_limit` 參數並傳入；兩個測試鎖住（−9.3 G 全拉時限幅、無限制時與範例逐位元相同）。
- 測試 `test_competition_play.py` 16 個：用未訓練的真 SAC checkpoint（8×8）建 session，跑 loopback 自測、整回合、拒飛路徑。
- 文件：`COMPETITION.md` 新節「比賽用程式」（自測、P1/P2 指令、用主辦方 HOST 預演）；`RULES.md` 狀態表；矩陣 C1 列註記。

## 2026-10-01 — 筆電：主辦方環境裝好、範例跑通（指引02 步驟逐一驗證）

- Anaconda 全機版（`C:\ProgramData\anaconda3`，conda 24.11.3）。libmamba 解算器外掛壞（`libmambapy` 無 `QueryFormat`），`conda config --set solver classic` 後正常；每次仍印兩行 entry point 錯誤，無害。
- `conda create -n f16_ai python=3.10` → Python 3.10.21；bat 用的 `F16_ai` 在 Windows 視為同一環境（C5 確認無影響）。
- 驗收一行：`torch 2.11.0+cu128`、`cuda True`、`jsbsim 1.3.1`、`gymnasium 1.0.0`、`stable-baselines3 2.4.0`。與範例模型 metadata（SB3 2.4.0、gymnasium 1.0.0、cu128）及指引06 截圖的 jsbsim 1.3.1 一致。
- `AirCombat_Train_Test` 以 xcopy 放到 `C:\`（44 檔）。`B.一鍵啟動test.bat` 載入 `model/jsbsim_sac_314400000_steps.zip` 跑通，Ctrl+C 停；`JSBSimRecording.txt.acmi` 11.6 MB 重寫。Tacview 未裝（建議項，非規則）。
- 桌面工作副本比 zip 多一個 `D.比賽用主辦方連線程式…` 資料夾內 9/26 的 Host 端 CSV／acmi／`player2_runtime.log`：使用者當天跑過 Host 程式。未比對 hash。

## 2026-09-30 — 官方資料全面 Audit（`docs/official/`）

- 讀完主辦方全部檔案（公告 0918 十四頁、附件2、指引 01–06、八個 Python、bat、Setting.txt、readme、模型 metadata、機體/引擎 XML 與 pip diff、表 3／表 4／圖 3／圖 4）。產出九份純官方文件：索引、檔案清單（55）、規格總表、資料字典（OBS 26 欄／CMD 6 欄逐欄附來源）、程式地圖、需求分類、八個流程、Traceability（T01–T58）、總報告（A–T、U01–U18 未知、C1–C10 衝突、34 題）、AIFCS 對照矩陣。
- 有實質影響的衝突：C1 client 升降舵 Mach>0.8 限 0.4 vs 訓練環境 1.0；C2 訓練環境不實作擊殺且常數寫 5 秒；C4 指引寫 50 英尺、公告與程式為 50 公尺。最大風險：U08（比賽 Host 的機體/引擎/初始化順序）+ C1。
- 主辦方檔案本身不進 repo；`official_audit/`、`official/` 已在 .gitignore。

## 2026-09-30 — H6：`--geometry`（訓練用起始幾何，可當課程）

- `environment.GEOMETRIES`、`RoundSetup.geometry`、`initial_geometry()`：published 的三個亂數順序不變（同 seed 同回合）；abreast / headon / offensive / defensive / mix（4:1）。距離、高度、速度不動。card 記 `geometry`；`evaluate.config_from_card` 不讀（測試鎖住）。
- `session.GROWABLE` 加 `geometry`、`geometry_history`；`train.refresh_growable` 續練換幾何時寫下 `{from_step, geometry}` 歷史並印一行。
- 紀錄 `EXP-007-start-geometry`：EXP-002 配方 + `--geometry mix`。路線圖 4.5：七個實驗的執行順序與比較對象。

## 2026-09-30 — H5：`--observation frames`（多座標系觀測）

- `competition/frames.py`：`FramesEncoder` = extended + 25 組 (向量, 座標系) × 3 = 75，共 105 維；速度系/視線系以重力固定滾轉，垂直退化用北。`environment.OBSERVATIONS` 與 `observation_width()` 成為唯一的名單與寬度來源（evaluate 改用它）。`mirror.observation_signs` 涵蓋 105 維。
- 紀錄 `EXP-006-frames-observation`：EXP-002 配方只換觀測。煙霧測試 frames + `--mirror` + `--reward potential` 一起跑 120 步、存 card。

## 2026-09-30 — H4：`--reward potential`（位能差 shaping）

- `rewards.py` 新 `RewardMode.POTENTIAL` / `PotentialReward`：margin + deck 不變，`shaped` 的追蹤項改付 γΦ(s′)−Φ(s)，Φ = 2000 × aim × range_factor（我方減敵方）。測試證明 telescoping（路過六次 = 一次）、待在錐裡 shaping 為零、首幀不付差。tier = research。
- 紀錄 `EXP-005-potential-shaping`：EXP-002 配方只換獎勵；判定看 cone+ 與錐內比例，不看 won。

## 2026-09-30 — H3：訓練中的勝率與 exploiter 停止規則

- `gym_env`：每回合結束 `info["verdict"]` = 表 3 判定（有沒有池都給）。`competition/winrate.py`：`WinRate` 回呼，滾動勝率（整體、每對手）寫進 logger `league/*`；`--stop-at-win-rate RATE --win-window ROUNDS` 到達就停，`SessionState.stopped_at_win_rate` 記下原因、target 設為已達成。預設 None，不影響任何既有訓練。
- 紀錄 `EXP-004-exploiter`：v6 配方、池只有 v6、70% 停。Dataset 要加 v6。

## 2026-09-30 — 訓練模組：對手分佈、EMA 聯賽、鏡像增強（H1、H2）

- **腳本對手進池**：`--opponent-pool` 接受名字（`break` `energy` `scissors` `wanderer` `pursuit` `reference` `level`）與 checkpoint 混用；環境每回合重建名字對應的腳本，和存好的策略共用一個抽樣分佈。`environment.opponent_names()` 是唯一的名單；`evaluate.OPPONENT_NAMES` 由測試鎖定與它相等。Kaggle 腳本的 `split_pool` 只對 session 名字查 Dataset。
- **`--league ema`**（預設仍是 `paper`）：SRC-012 的抽樣器 p = 0.5·均勻 + 0.5·softmax(−EMA/0.3)。發現：PHANG-MAN 規則的閘門（每對手 100 場/worker）在 200 萬步 8 worker 內永遠不會開，之前所有有池的訓練都是均勻抽樣。
- **`--mirror`**（預設關，SAC 限定，PPO 直接拒絕）：`competition/mirror.py` 的 `MirroredReplayBuffer` 每筆經驗存原本與左右鏡像；20/30 維觀測各有一組符號向量，由編碼器本身驗證；動作在縮放空間鏡像，固定的方向舵保持固定。**實測** JSBSim F-16 每幀不對稱 < 0.005° 滾轉（10 秒隨機滿舵後 12°，是混沌放大不是偏差），測試鎖住界線。旗標記進 card 與 session，續練自動繼承。
- 實驗紀錄：`EXP-002-opponent-distribution`（v6 配方 + 池 + ema）、`EXP-003-mirror`（EXP-002 + `--mirror`）。`docs/TRAINING_ROADMAP.md`：逐步做法與判定規則。
- 端到端煙霧測試：SAC + `--mirror` + `--opponent-pool break scissors --league ema` 跑 240 步、存檔、無旗標續練繼承 `mirror=True, league=ema`，buffer 類別為 `MirroredReplayBuffer`、600 筆（300 步 × 2）。

## 2026-09-30 — PART 18 補查：找不到的換路線找

- **發現同題目的公開冠軍程式**：韓國航空大學 2026 AI Pilot Top Gun Challenge（9/17 決賽，290 隊），JSBSim F-16 1v1 純機砲，前段射擊錐 **2° / 500–3,000 ft 與我們相同**；冠軍隊程式已 clone 讀碼（無 LICENSE → 只讀設計不複製）。`research/sources.yaml` 新增 SRC-011～017；SRC-006（改走讀取代理）與 SRC-007（PDF 抽文）升為 verified；Shaw 讀不到，改以美國海軍 T-45 ACM 講義（SRC-016）替代。
- `docs/PRIOR_ART.md` 第五節：規則對照表、冠軍作法對照表、六個可驗證假設（H1 鏡像增強、H2 輸誰多打誰、H3 exploiter、H4 位能差 shaping、H5 座標系觀測、H6 課程初始分佈），全部走 `experiments/`，官方層不動。
- 旁證：冠軍程式把「2 度」寫成 `TIER1_CONE_DEG = 1.0`（半角 1°），與 CONFORMANCE D.1 一致。

## 2026-09-30 — PART 18/19 飛航資料研究

- **新**：`docs/AIRCRAFT_KNOWLEDGE_BASE.md` —— 機體身分（JSBSim F-16A Block-32，源自 NASA TP-1538 1979 公開風洞資料）、幾何、兩顆引擎的差別、HOST 實測初始條件、plant-vs-HOST 10 秒差 3 ft、G 正負與升降舵整形約定、Boyd E-M 只當讀圖語言、未驗證清單。**不引入任何新數據到模型。**
- `research/sources.yaml`：SRC-008 NASA TP-1538（verified，NTRS）、SRC-009 Boyd/Christie/Gibson APGC-TR-66-4（verified，原文 PDF 已核對 Ps 定義）、SRC-010 `f16.xml` 出處（verified）。SRC-004 的來源說法更正：Stevens & Lewis 與 `f16.xml` 同源於 TP-1538，`f16.xml` 引用的是 NASA 論文本身。
- `docs/PRIOR_ART.md` 同一處更正。

## 2026-09-30 — AIFCS 2.0 step 1（`4097529` + `33c5be9`）

- **修**：`competition/trace.py` 更名 `roundtrace.py`，消除 stdlib 遮蔽（同 `profile.py` 事件）。實測 `import trace` 解析回 `/usr/lib/python3.11/trace.py`。
- **修**：8 個 mypy 錯誤（皆在近兩次新增的測試）。mypy 173 檔全綠。
- **新**：`competition/experiments.py` + `experiments/`（PART 15）。
- **新**：獎勵三層標籤 `REWARD_TIERS`（PART 13）。
- **新**：`research/sources.yaml`，7 筆來源含驗證狀態（PART 18.3）。
- **新**：`experiments/EXP-001-official-sac-baseline.yaml`（PART 11.1）。
- **改**：`kaggle/aifcs_train.py` 改讀實驗紀錄，不再硬編 NAME/POOL/FLAGS。
- **新**：`scripts/experiment.bat`、`docs/EXPERIMENT_MANAGER.md`、`docs/RESEARCH_ENGINE.md`。
- 註：`4097529` 只含 7 個檔案（更名、型別修正、experiments.py 與其測試）；`experiments/`、`research/`、文件、Kaggle 腳本與 `.bat` 在 `33c5be9` 才進入版本庫。V2 標籤指向 `33c5be9`。

## 2026-09-30 — PHASE 0 audit（`436d372`）

- `docs/PROJECT_AUDIT.md`：22 項盤點。完整套件 1,107 個測試，1 失敗（即上述 P0）。
