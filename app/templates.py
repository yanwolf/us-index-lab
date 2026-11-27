"""一鍵套用的回測／掃描範本：只留「還沒跑」的。
跑完、分析完的就從這裡刪掉（結論記在 README 的「研究結論」）；完整歷史版本在 git 紀錄裡。
grid 空的 = 單純回測；有 grid = 最佳化（目標報酬／回撤、平台挑選、滾動驗證）。成本用商品預設（Pepperstone 點差＋滑價＋隔夜利息）。"""
from .strategies import TXF_PRESET

START = "2021-02-05"          # Dukascopy 第一筆 2021-01-03，留一個月暖機
SYM_NAME = {"NAS100": "那指", "HK50": "恆指", "JP225": "日經", "US500": "標普", "XAUUSD": "黃金"}

# AR_crossover 新出場：txf-sim 定案的停損 4／停利 8／移動停利 0.8／回吐 1.6 × AvgRange(200)（模擬倉 us_arx_new）
ARX_NEW = {"UNIT_N": 200, "LOSS": 4, "WIN": 8, "Twin": 0.8, "Tstop": 1.6}


def _t(group, name, strategy, symbol, params=None, grid=None, note="", minutes=None, costs=None, start=START, end=None, source="duka"):
    return dict(group=group, name=name, strategy=strategy, symbol=symbol, source=source, start=start, end=end, minutes=minutes, costs=costs,
                align="session", params=params or {}, grid=grid or {}, objective="ret_mdd", rank_by="plateau",
                min_trades=20, wf_k=4, wf_m=2, split=0.67, note=note)


def templates():
    out = []

    # A. 標普 AR 新出場：最後兩項驗證（要先在「資料」頁下載 US500 Dukascopy，開始日填 2012-12-01，再看完整度）
    gA = "A. 標普 AR 新出場（模擬倉 us_arx_new）：Dukascopy 樣本外"
    out += [
        _t(gA, "標普 AR 新出場｜2013–2017 樣本外（Dukascopy）", "AR_crossover", "US500", params=ARX_NEW,
           start="2013-01-02", end="2017-11-30",
           note="Pepperstone 2017-08 以前每天只有約 1 根，不能用；含 2015-08、2016 年初修正"),
        _t(gA, "標普 AR 新出場｜Dukascopy 2018 起（換資料來源對照）", "AR_crossover", "US500", params=ARX_NEW,
           start="2018-01-02", note="Pepperstone 同期 PF 1.18；差很多就是資料問題"),
    ]

    # B. 黃金 GuYuan_2025 60 分 K：候選確認
    #    Dukascopy 2021 起：年化 6.5%、回撤 10.5%、PF 1.37，和那指 GY25、標普 AR 相關 ≈ 0；其他策略見 README（已結案）
    gB = "B. 黃金 GuYuan_2025 60 分 K：候選確認（預設 HN 25、LOSS 1.0、WIN 6.2）"
    t = _t(gB, "黃金 GY25 60 分 鄰近值掃描（HN × LOSS × WIN，80 組，每 2 年重挑）", "GuYuan_2025", "XAUUSD", minutes=60,
           grid={"HN": [25, 55, 10], "LOSS": [0.5, 1.1, 0.15], "WIN": [4.2, 10.2, 2]},
           note="和那指 ⑨ 同一張網格；看預設是不是在平台上、樣本內排名對樣本外有沒有預測力")
    t.update(wf_k=3, wf_m=1)
    out += [
        _t(gB, "黃金 GY25 60 分｜滑價加倍（每邊 0.2）", "GuYuan_2025", "XAUUSD", minutes=60, costs={"slippage": 0.2},
           note="20 分 K 滑價加倍就垮（PF 1.08）；60 分 K 交易少很多，看撐不撐得住"),
        t,
    ]
    out += [_t(gB, f"黃金 GY25 {m} 分 K（K 棒鄰近值）", "GuYuan_2025", "XAUUSD", minutes=m,
               note="60 分是不是孤峰") for m in (45, 75, 90)]
    out.append(_t(gB, "黃金 GY25 60 分｜2018–2020 樣本外（Dukascopy）", "GuYuan_2025", "XAUUSD", minutes=60,
                  start="2018-01-02", end="2020-12-31",
                  note="要先在「資料」頁補下載黃金，開始日填 2017-12-01；含 2018 盤整下跌、2019–2020 大漲、2020-03 崩跌"))
    return out
