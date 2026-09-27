# 關掉筆電也不會停

**免費的方法先講,因為它就夠用了。**

---

# Kaggle:每週 30 小時免費 GPU,不用信用卡

Kaggle 的 **Save Version → Save & Run All (Commit)** 會把你的 notebook
**複製到 Kaggle 的機器上跑**。按下去之後,瀏覽器、筆電、網路全都不再相干,
**跑完還會寄 email 給你**。

這是這個專案裡**唯一「電腦關機而工作繼續」的安排**。

| | 免費額度 | 單次上限 |
|---|---|---|
| **Kaggle Notebooks** | **每週 30 小時** GPU(P100 / T4) | 9~12 小時 |
| Google Colab | 每週 15~30 小時 | 12 小時 |

兩個加起來每週最多 60 小時,**都不用信用卡**。

## 怎麼做(第一次)

1. 去 **kaggle.com** 註冊,手機驗證(開 GPU 需要)
2. **Create → New Notebook**
3. 右邊面板:
   - **Accelerator → GPU P100**
   - **Internet → On**(要 clone 和 pip install)
4. 把 `kaggle/aifcs_train.py` **整個檔案**貼進第一個 cell
5. **Save Version → Save & Run All (Commit)**
6. **關掉瀏覽器,關掉筆電,去上班**

跑完會收到 email。回來開那個 version 的 **Output** 分頁,模型在裡面。

## 第二次之後:把上一代帶進去

Kaggle 的 notebook 每次都是乾淨的機器,所以要自己把舊模型帶進去:

1. 上一次跑完的 **Output** 分頁 → **New Dataset**,發佈成一個 Dataset
2. 新的 notebook 裡:**Input → Add Input → Datasets**,選那個
3. 改 `kaggle/aifcs_train.py` 裡的兩行:

```python
NAME = "v7"
POOL = ["v4", "v5", "v6"]
```

腳本會自己把附上來的 checkpoint 複製進去,對手池就接上了。

## `MAX_HOURS` 是最重要的那個數字

```python
MAX_HOURS = 7.5
```

**Kaggle 到時間會直接把容器收掉**,正在跑的東西連同上一次存檔之後的進度一起消失。
所以訓練要**自己先停**:`--max-hours` 讓它在預算到的時候乾淨地存檔退出。

留一小時給訓練後的評估。

## 要注意的

- **P100 不一定比你的 4050 快。** 我們的瓶頸是很多次小的 GPU 呼叫
  (`--gradient-steps -1`),那是延遲問題不是算力問題。**重點不是更快,是
  它在你關機的時候還在跑。**
- 一次 session 跑**一階**,不是整條階梯 —— 時間不夠三階
- **主辦方的檔案不會上去,也不需要**

---

# 付費的方法:租一台 GPU 機器

免費額度不夠的時候才需要。

---

## 一、租一台機器

你已經有 **DigitalOcean** 帳號(交易系統在上面)。同一個帳號、同一張卡:

**Create → Droplets → 機型列表找 GPU**

> 也可以用 vast.ai 或 RunPod,通常更便宜、卡的選擇更多。下面的步驟三家通用,
> 只有「怎麼開機器」那一步各家介面不同。

### 挑哪一張卡

**不要直接挑最貴的。** 實測你筆電那張 RTX 4050:

```
GPU-Util: 33%     429MiB / 6141MiB
```

**顯卡七成時間在閒著,6 GB 顯存只用了 429 MB。** 租一張 H100 等級的卡,
錢會花在一個你連三分之一都填不滿的東西上。

跑完 `scripts/profile.bat` 就知道要買什麼:

| 量出來的 | 該租的 |
|---|---|
| 學習佔大部分 | **先別租** —— 改 `--gradient-steps` 或 `--batch-size` 可能就夠了 |
| 模擬佔大部分 | **核心多的**機器,顯卡普通就好 |
| 各半 | 中階卡 + 多核心 |

**先量再租。** 那個量測不用花錢。

---

## 二、把機器架好(一行)

開好機器後,在它的網頁終端機(或 SSH)貼這一行:

```bash
curl -fsSL https://raw.githubusercontent.com/brian940120b-ux/AGMCIS_APP/claude/aifcs-flight-simulation-u32h56/AIFCS/scripts/cloud_setup.sh | bash
```

它會裝好系統套件、clone、建 venv、裝依賴,最後印出顯卡和 JSBSim 的版本
確認真的能訓練。

**主辦方的軟體包和 PDF 不會上去,也不需要。** 訓練用的是 pip 裝的 JSBSim
F-16 —— 那一份已經對真實 HOST 驗證過:同樣的開局,十秒後兩架飛機相差三呎。

---

## 三、把對手傳上去

階梯要拿 v4 和 v5 當對手,所以要先把它們的存檔傳過去。在**你筆電的 Git Bash**:

```bash
cd ~/AGMCIS_APP/AIFCS
scp -r models/competition/v4 models/competition/v5 root@機器的IP:~/AGMCIS_APP/AIFCS/models/competition/
```

每個 session 幾十 MB,一兩分鐘。

---

## 四、開跑,然後關掉筆電

在機器上:

```bash
cd ~/AGMCIS_APP/AIFCS
scripts/ladder.sh plans/ladder.yaml --shutdown
```

它會印出 pid 和 log 位置,然後**把終端機還給你**。

**`--shutdown` 是省錢的關鍵** —— 整條階梯跑完,機器自己關機,停止計費。
沒有它,跑完的機器會空轉整個週末,而你是按小時付錢。

**這時候就可以關掉 SSH、關掉筆電、去上班。**

---

## 五、隔天回來看

```bash
ssh root@機器的IP
tail -50 ~/AGMCIS_APP/AIFCS/models/competition/_logs/ladder-*.log
```

機器已經關掉的話,在控制台重新開機再看,log 還在硬碟上。

把模型抓回筆電:

```bash
scp -r root@機器的IP:~/AGMCIS_APP/AIFCS/models/competition/v6 ~/AGMCIS_APP/AIFCS/models/competition/
```

---

## 階梯檔案長什麼樣

`plans/ladder.yaml`:

```yaml
steps:
  - name: v6
    flags: --reward shaped --rudder-limit 0.6 --timesteps 2000000
    pool: [v4, v5]

  - name: v7
    flags: --reward shaped --rudder-limit 0.6 --timesteps 2000000
    pool: [v4, v5, v6]
```

**`pool` 寫的是 session 名字,不是路徑。** 所以 v7 打 v6 —— 上一階剛練出來的
那個。這就是自我對戰階梯變成一個檔案,而不是需要有人半夜三點起來開下一輪。

每一步練完會**自動評估**,而且是**對它自己練的那批對手**評估。打贏內建靶機
不代表什麼 —— 搖桿置中對靶機都有 65% 勝率。

### 改階梯

直接編輯那個 yaml,加一段就是多一代。跑之前先看它要做什麼:

```bash
scripts/ladder.sh plans/ladder.yaml --dry-run
```

---

## 出事的時候

**機器中途死掉 / 你手動停掉** —— 再打同一行。已經練完的步驟會跳過,
練到一半的會從斷點接上。**重跑整條階梯是安全的,那就是設計出來要這樣用的。**

**某一步練壞了** —— 佇列會記下來然後**繼續下一步**。八小時的階梯不會被
第四步的一個錯字毀掉。

**要停** —— 在機器上:

```bash
touch models/competition/v6/STOP
```

那一步會存好檔再退出,跟 Ctrl+C 走同一條路。**不要用 kill -9**,會損失最多
25,000 步。

**`pool` 裡寫了不存在的名字** —— 那一步會在**訓練之前**被擋下來並說缺哪一個,
不會練完八小時才發現。

---

## 錢

一次 2,000,000 步的訓練,在你筆電上是 7~8 小時。租的機器視規格而定。

**三步階梯 = 大約三次訓練的時間。** 開一次跑完自動關機,只付那幾個小時。

**41 天裡,這是能不能多做二十次實驗的差別。**
