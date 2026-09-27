# 在租來的機器上跑,關掉筆電也不會停

這份文件解決一件事:**訓練不需要你的電腦開著。**

沒有任何軟體能讓一台關機的電腦繼續算 —— 所以答案是**一台不是你的機器**。
你的交易系統已經是這樣跑的,只是它不需要顯卡,而訓練需要。

而且在租來的機器上,「我不在時繼續跑」和「自動跑下一個實驗」變成同一件事,
所以這裡給的不是「跑一次」,是**一條會自己跑完的階梯**。

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
