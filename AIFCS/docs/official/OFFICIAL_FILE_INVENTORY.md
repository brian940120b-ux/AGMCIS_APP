# OFFICIAL_FILE_INVENTORY

Root（筆電）：`C:\Users\user\Desktop\AI飛行員 競賽辦法\`（注意名字中間有一個空格）。
同一資料夾旁有原始壓縮檔 `AI飛行員 競賽辦法.zip`（27,635,688 bytes）。

這次 audit 讀的是**上傳到這個 session 的同一批檔案**：`AI空戰競賽系統安裝及測試指引.zip`（sha256 `2ed5af8dc380a5fc…`，與筆電清單第 52 項相同）、兩份頂層 PDF、六份指引 PDF。
筆電清單裡多出的 `D.…/output_acmi/飛行競賽Host端Record_20260926_2008.acmi`、`飛行競賽Host端Record_20260926_2008.csv`、較長的 `player2_runtime.log`（12.4 MB）是 **2026-09-26 在筆電上跑 Host 產生的輸出**，不在主辦方的 zip 裡 → 標 `USER_GENERATED`，本次未讀。

| # | 路徑（相對 root） | 大小 | 類型 | 分類 | 官方？ | 已讀 | 影響競賽 |
|---|---|---|---|---|---|---|---|
| 1 | `2026 AI空戰競賽活動辦法_1150909.pdf` | 137,417 | PDF 1 頁 | OFFICIAL_DOCUMENT | 是 | 全文 | 是（日期、資格、規則依公告） |
| 2 | `AI空戰飛行競賽公告文件_0918.pdf` | 1,039,548 | PDF 14 頁 | OFFICIAL_DOCUMENT | 是 | 全文 + 7 張圖 | **是（規則、ICD、計分、限制）** |
| 3 | `報名文件/個人資料蒐集、處理或利用同意書_1150908.pdf` | 176,429 | PDF | OFFICIAL_DOCUMENT（報名） | 是 | 未讀（未上傳；報名文件，非技術） | 否 |
| 4 | `報名文件/參賽同意書_1150909v2.pdf` | 195,276 | PDF | OFFICIAL_DOCUMENT（報名） | 是 | 未讀（同上） | 否 |
| 5 | `AI空戰競賽系統安裝及測試指引.zip` | 26,276,273 | ZIP | OFFICIAL_PROGRAM 包 | 是 | 已解壓、列出 | 是 |
| 6 | `…指引/0.AI空戰資料說明文件.pdf` | 384,133 | PDF 2 頁 | OFFICIAL_DOCUMENT（01） | 是 | 全文 | 目錄性質 |
| 7 | `…指引/1.從0開始AI飛行員-環境安裝.pdf` | 1,722,348 | PDF 13 頁 | INSTALLATION（02） | 是 | 全文 + 28 張圖（安裝截圖） | 是（版本） |
| 8 | `…指引/2.比賽用參賽方連線程式.pdf` | 392,513 | PDF 6 頁 | PLAYER（03） | 是 | 全文 + 6 張圖 | **是（OBS/CMD/IP/state）** |
| 9 | `…指引/3.比賽用主辦方連線程式.pdf` | 295,253 | PDF 7 頁 | HOST（04） | 是 | 全文 + 6 張圖 | 是（Host 狀態機） |
| 10 | `…指引/4.訓練階段軟體訓練程式.pdf` | 670,494 | PDF 7 頁 | TRAINING（05） | 是 | 全文 + 14 張圖 | 是（訓練環境說明） |
| 11 | `…指引/5.訓練階段軟體測試程式.pdf` | 425,427 | PDF 2 頁 | TESTING（06） | 是 | 全文 + 3 張圖 | 否 |
| 12 | `…/AirCombat_Train_Test/.vscode/settings.json` | 135 | JSON | CONFIG | 是 | 全文 | 否 |
| 13 | `…/A.一鍵啟動train.bat` | 176 | BAT | TRAINING | 是 | 全文 | 否 |
| 14 | `…/B.一鍵啟動test.bat` | 175 | BAT | TESTING | 是 | 全文 | 否 |
| 15 | `…/C.一鍵啟動player1_Loadmodel.bat` | 188 | BAT | PLAYER | 是 | 全文 | 否 |
| 16 | `…/aircraft/f16/f16.xml` | 61,104 | XML | MODEL（機體） | 是（JSBSim 附帶，GPL） | 與 pip 版 diff | 見 SPEC §E |
| 17 | `…/aircraft/f16/Engines/F100-PW-229.xml` | 3,647 | XML | MODEL（引擎） | 是 | 與 pip 版 diff | 見 SPEC §E |
| 18 | `…/aircraft/f16/Engines/direct.xml` | 108 | XML | MODEL | 是 | 全文 | 否 |
| 19 | `…/aircraft/f16/reset00.xml` | 548 | XML | MODEL | 是 | 全文 | 否（跑道起始，程式未用） |
| 20 | `…/aircraft/f16/Systems/hook.xml` | 2,311 | XML | MODEL | 是 | 未逐行 | 否 |
| 21 | `…/aircraft/f16/Systems/pushback.xml` | 1,192 | XML | MODEL | 是 | 未逐行 | 否 |
| 22 | `…/aircraft/f16/INSTALL` | 387 | 文字 | MODEL | 是 | 全文 | 否 |
| 23 | `…/aircraft/f16/README` | 3,452 | 文字 | MODEL | 是 | 全文 | 否 |
| 24 | `…/D.比賽用主辦方連線程式…/JSB_host_GUI_publish.exe` | 19,041,280 | EXE | HOST | 是 | 只看 strings（打包二進位，無可讀協定字串） | **是** `[NOT VERIFIED]` |
| 25 | `…/D.…/ICON.ico` | 108,602 | ICO | MEDIA | 是 | 否 | 否 |
| 26 | `…/D.…/Setting.txt` | 750 | 文字 | NETWORK／CONFIG | 是 | 全文 | **是（IP/PORT/FPS）** |
| 27 | `…/D.…/readme_part1.txt` | 2,455 | 文字 | HOST README | 是 | 全文 | 是 |
| 28 | `…/D.…/readme_part2.txt` | 2,747 | 文字 | HOST README | 是 | 全文 | 是 |
| 29 | `…/D.…/player2_runtime.log` | 2,022,776（zip 內） | LOG | DATA | 是（主辦方測試 log） | 頭尾 | 部分（內建 Player2 行為） |
| 30 | `…/envs/jsbsimEnv/__init__.py` | 96 | PY | TRAINING | 是 | 全文 | 否 |
| 31 | `…/envs/jsbsimEnv/jsbsimEnv.py` | 18,796 | PY 515 行 | TRAINING | 是 | 全文 | **是（state/action/reward）** |
| 32 | `…/envs/jsbsimEnv/jsbsimFdm.py` | 14,538 | PY 386 行 | TRAINING | 是 | 全文 | 是（FDM 初始化、敵機 PID） |
| 33 | `…/envs/jsbsimEnv/utils/__init__.py` | 0 | PY | TRAINING | 是 | — | 否 |
| 34 | `…/envs/jsbsimEnv/utils/utils.py` | 12,367 | PY 423 行 | UTILITY | 是 | 全文 | 部分（`CalculateCoordinates`） |
| 35–46 | `…/envs/jsbsimEnv/**/__pycache__/*.pyc`（12 個：cpython-38 / -310 / -312） | — | PYC | UNKNOWN | 是（編譯快取） | 否 | 否；`[INFERRED]` 主辦方曾用 Python 3.8、3.10、3.12 執行過 |
| 47 | `…/JSBSimRecording.txt.acmi` | 1,602,426 | ACMI | DATA（範例輸出） | 是 | 檔頭 | 否 |
| 48 | `…/model/jsbsim_sac_314400000_steps.zip` | 3,249,024 | ZIP（SB3 模型） | MODEL | 是 | 列出 + metadata | 是（版本證據） |
| 49 | `…/player1_Loadmodel.py` | 15,858 | PY 472 行 | PLAYER | 是 | 全文 | **是（UDP/OBS/CMD）** |
| 50 | `…/test.py` | 813 | PY 33 行 | TESTING | 是 | 全文 | 否 |
| 51 | `…/train.py` | 2,169 | PY 67 行 | TRAINING | 是 | 全文 | 是（SAC 超參） |
| 52 | `…/D.…/output_acmi/飛行競賽Host端Record_20260926_2008.acmi` | 552,422 | ACMI | USER_GENERATED | **否**（筆電產生） | 否 | — |
| 53 | `…/D.…/飛行競賽Host端Record_20260926_2008.csv` | 2,883,428 | CSV | USER_GENERATED | **否** | 否 | — |
| 54 | `AI空戰競賽系統安裝及測試指引/AI空戰競賽系統安裝及測試指引/`（雙層同名資料夾） | — | 目錄 | — | 是 | — | 解壓造成的雙層 |
| 55 | `…/D.…/player2_runtime.log`（筆電上 12,417,625 bytes） | — | LOG | 部分 USER_GENERATED | zip 內原本 2.0 MB，筆電上被後續執行加長 | 頭尾（zip 版） | — |

計數（機械）：55 個檔案；PDF 10；Python 8（含兩個 `__init__.py`）；官方工具（exe）1；訓練相關 6（`train.py`、`jsbsimEnv.py`、`jsbsimFdm.py`、`utils.py`、`A.bat`、指引 05）；測試相關 3（`test.py`、`B.bat`、指引 06）。
