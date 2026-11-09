# us-index-lab — 美股指數回測室

把 txf-sim 的台指策略移植到標普 500（US500）、那斯達克 100（NAS100）CFD，
用多年 1 分 K 回測、調參數，判斷值不值得之後在 MT5 上程式交易。

## 部署（Coolify）
1. 建 GitHub repo，整包推上去，Coolify 新增服務選 Dockerfile
2. **Persistent Storage：掛一個 volume 到 `/data`**（下載的資料、參數存檔都在這，不掛的話重新部署就沒了）
3. 環境變數照 `.env.example`，至少設 `LAB_PASSWORD`
4. 埠號 8080，健康檢查 `/healthz`
資源：3 年兩個商品約 200MB 磁碟；記憶體 1GB 夠用。最佳化會用滿所有 CPU 核心。

## 使用流程
1. **資料 → 從 Dukascopy 下載**：US500、NAS100 各抓一次（預設 3 年）。可以重複按，只會補缺的日子
2. **回測**：選策略、K 棒週期、期間，先用預設參數跑一次看基準
3. **最佳化**：勾 2–3 個參數掃描。看「樣本外」欄位，以及頁首的穩健度百分比
4. 點最佳化結果的一列 → 自動帶回回測頁看完整報告 → 喜歡就「存目前參數」
5. **MT5 校準**（見下）：用要下單那家券商的實際點差，回填成本後重跑

## 判斷值不值得的標準（建議）
- 參數不重新優化或只微調，樣本外仍賺錢
- 交易數 > 100、獲利因子 > 1.3（扣成本後）
- **報酬／回撤要優於買進持有**：只做多的策略在美股長期多頭下，這一關最容易輸
- 參數附近一片都不錯（不是孤立的一個尖峰）

## 和台指版的差異
| 項目 | txf-sim（台指） | us-index-lab |
|---|---|---|
| 停損停利 | 固定點數 | 單位 × 倍數。單位 = 進場前 AvgRange(UNIT_N)，或 0.1% 價格（UNIT_MODE=1） |
| 交易日 | 期交所交易日 | 美東 18:00 切換（週日晚上算週一） |
| 時段 CloseS | 日盤／夜盤 | RTH 09:30–16:00／ETH 16:00–翌日 09:30 |
| 結算日出場 | 有 | 無（CFD 沒有結算） |
| 週末 | 週六 TNw 後出場 | 週五（美東）TNw 後出場，預設 16:45 |
| 成本 | 手續費＋滑價 | 點差＋滑價＋隔夜利息（年化 %） |

撮合規則與 MC 一致：收盤後掛下一根的單、1 分 K 內觸價、一根 K 最多成交一次、
跳空以開盤價成交、同一分鐘停損停利都碰到以停損先算（保守）。
策略預設參數是台指原參數的粗略換算，只是起點。

## MT5 券商探測（Windows）
```
pip install MetaTrader5 pandas
python tools/mt5_probe.py --broker pepperstone --terminal "C:\...\terminal64.exe"
```
先在 MT5「工具 → 選項 → 圖表 → 圖表中最大K線數」設成最大並重開。
每家跑一次，產出：
- `mt5_<券商>_report.json`：合約規格、點差、隔夜利息、1 分 K 能回溯多遠
- `mt5_<券商>_US500_M1.csv` 等：到「資料」頁匯入，再到「資料比對」和 Dukascopy 對照，取得實際點差

## 檔案
- `app/engine.py` 回測引擎（PowerLanguage 語意 + 1 分 K 觸價）
- `app/strategies.py` 策略（新增策略：繼承 Strategy、實作 on_bar、加進 REGISTRY）
- `app/bars.py` K 棒／交易日／時段／週 + 指標快取
- `app/dukascopy.py` 下載器（價格倍率與欄位順序自動判斷，結果存在 `/data/duka/<商品>/format.json`）
- `app/optimize.py` 網格搜尋 + 樣本內外切分
- `app/mt5import.py` MT5 CSV 匯入與比對
- `tools/mt5_probe.py` Windows 探測腳本
- `tests/` 合成資料測試：`python tests/test_engine.py`
