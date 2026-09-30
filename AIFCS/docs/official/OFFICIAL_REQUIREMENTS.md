# OFFICIAL_REQUIREMENTS

| 類別 | 項目 | 版本/值 | 來源 | 狀態 |
|---|---|---|---|---|
| **REQUIRED**（規則） | 能與主辦方進行 UDP 資料交換的電腦，具網路孔 | — | 公告 p.10–11 五.1、五.2 | `[VERIFIED]` |
| REQUIRED | 依主辦方指定的 IP/PORT 設定；實體網路線連 HUB | — | 公告 p.9 四.3；p.12 六.4 | `[VERIFIED]` |
| REQUIRED | 送出 30-byte CMD（5 float + "PLAYER_CMD"）、接收 208-byte OBS | — | 公告 表 1、表 2；指引03 | `[VERIFIED]` |
| REQUIRED | 單一運算環境；無外部連線 | — | 公告 六.5、六.6 | `[VERIFIED]` |
| REQUIRED（若用範例） | Python | 3.10（`conda create -n f16_ai python=3.10`）；範例模型由 3.10.20 產生 | 指引02 p.10；模型 `system_info.txt` | `[VERIFIED]` 範例需求；規則層面**不限制語言**（公告 p.11 五.3.(2).C） |
| REQUIRED（若用範例） | stable-baselines3[extra] | **==2.4.0** | 指引02 p.12；模型 metadata 2.4.0 | `[VERIFIED]` |
| REQUIRED（若用範例） | jsbsim | 未指定版本（`pip install jsbsim`） | 指引02 p.12 | `[PARTIAL]`（版本 `[UNKNOWN]`；指引06 截圖顯示 1.3.1） |
| REQUIRED（若用範例） | gymnasium | 未指定（`pip install gymnasium`）；模型 metadata 1.0.0 | 指引02 p.12；模型 | `[PARTIAL]` |
| REQUIRED（若用範例） | torch / torchvision / torchaudio | 依顯卡：cu128 / cu121 / cu118 index；模型由 2.12.0.dev20260408+cu128 產生 | 指引02 p.12–13；模型 | `[VERIFIED]`（三選一） |
| REQUIRED（若用範例） | numpy | 未指定；模型 1.26.4；bat 啟動檢查 NumPy | 指引03 p.2；模型 | `[PARTIAL]` |
| REQUIRED（若用範例） | tensorboard | 未指定 | 指引02 p.12 | `[PARTIAL]` |
| **RECOMMENDED** | Anaconda（Windows 版） | — | 指引02 p.2 「最適合新手」 | `[VERIFIED]` |
| RECOMMENDED | VS Code + Python 擴充 | — | 指引02 p.5–7 「建議」 | `[VERIFIED]` |
| RECOMMENDED | Tacview | — | 指引02 p.8 「建議」 | `[VERIFIED]` |
| RECOMMENDED | NVIDIA 驅動 + CUDA | 「依據實際電腦規格自行適當配置」 | 指引02 p.9；公告 五.3.(1).A | `[VERIFIED]` |
| RECOMMENDED | 硬體 | CPU i5/R5 以上、RTX30 以上、RAM 16G 以上 | 公告 p.11 五.1 「建議設備」 | `[VERIFIED]` RECOMMENDED，非 REQUIRED |
| **OPTIONAL** | Windows 或 Linux | 「可使用 Windows、Linux 等」；範例與 bat 為 Windows | 公告 p.11 五.2；指引01 p.2 | `[VERIFIED]` |
| OPTIONAL | 程式語言 | 任意（可用 C/C++ 等，注意 pack） | 指引03 p.6；公告 五.3.(2).C | `[VERIFIED]` |
| **PROVIDED** | 訓練程式、測試程式、參賽方連線程式、主辦方連線程式 | 見 PROGRAM_MAP | 公告 p.11–12 五.3.(2) | `[VERIFIED]` |
| PROVIDED | 範例模型 `jsbsim_sac_314400000_steps.zip` | — | zip | `[VERIFIED]` |
| PROVIDED | `aircraft/f16` 機體與引擎檔 | — | zip | `[VERIFIED]`（但範例程式不讀它） |
| **UNKNOWN** | 比賽日 Host 的 Python/JSBSim 版本 | — | 無 | `[UNKNOWN]` |
| UNKNOWN | JSBGYM 是什麼（公告寫「JSBGYM(JSBSIM F-16)」，指引只裝 jsbsim + gymnasium） | — | 公告 五.3.(1).C | `[UNKNOWN]`（可能指 jsbsim + gymnasium 的組合，`[INFERRED]`） |
| UNKNOWN | 「Socket」是要下載什麼 | — | 公告 五.3.(1).D | `[UNKNOWN]`（Python 標準庫 socket 不需下載，`[INFERRED]`） |
