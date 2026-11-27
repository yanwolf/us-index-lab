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

    # B. 黃金 GuYuan_2025 60 分 K：只剩真正的樣本外（滑價加倍、鄰近值掃描、K 棒 45～90 分都過了，見 README）
    gB = "B. 黃金 GuYuan_2025 60 分 K：2018–2020 樣本外（預設 HN 25、LOSS 1.0、WIN 6.2）"
    out += [
        _t(gB, "黃金 GY25 60 分｜2018–2020 樣本外（Dukascopy）", "GuYuan_2025", "XAUUSD", minutes=60,
           start="2018-01-02", end="2020-12-31",
           note="要先在「資料」頁補下載黃金，開始日填 2017-12-01；含 2018 盤整下跌、2019–2020 大漲、2020-03 崩跌"),
        _t(gB, "黃金 GY25 90 分｜2018–2020 樣本外（Dukascopy）", "GuYuan_2025", "XAUUSD", minutes=90,
           start="2018-01-02", end="2020-12-31", note="90 分在 2021 起最好（0.68），一起看樣本外"),
    ]
    return out
