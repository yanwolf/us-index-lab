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

    # B. 黃金：台指策略原封不動先跑一次（資料頁下載 XAUUSD，開始日填 2021-01-01）
    gB = "B. 黃金 XAUUSD：台指策略先用原參數跑一次（2021 起，含 2022 下跌）"
    out += [
        _t(gB, "黃金多 GuYuan_2025（預設，20 分 K）", "GuYuan_2025", "XAUUSD", note="指數上目前最好的一支"),
        _t(gB, "黃金多 GuYuan_2025 60 分 K", "GuYuan_2025", "XAUUSD", minutes=60, note="那指 60 分 K 比 20 分好，看黃金是不是一樣"),
        _t(gB, "黃金多 GuYuan_2024 台指參數", "GuYuan_2024", "XAUUSD"),
        _t(gB, "黃金多 AR_crossover（原出場）", "AR_crossover", "XAUUSD"),
        _t(gB, "黃金多 AR_crossover（新出場）", "AR_crossover", "XAUUSD", params=ARX_NEW, note="標普上明顯比原出場好"),
        _t(gB, "黃金 TMFF（多空）", "TMFF", "XAUUSD"),
        _t(gB, "黃金空 GuYuan_2024_S 台指參數", "GuYuan_2024_S", "XAUUSD", params=dict(TXF_PRESET["GuYuan_2024_S"])),
        _t(gB, "黃金空 TMFF_S", "TMFF_S", "XAUUSD"),
        _t(gB, "黃金多 GuYuan_2025｜滑價加倍（每邊 0.2）", "GuYuan_2025", "XAUUSD", costs={"slippage": 0.2},
           note="成本敏感度：滑價加倍還站得住才算數"),
    ]

    # C. TurtleMA：指數上已結案（README）。只剩黃金待跑
    SWING = {"SIDE": 1, "XS": 0, "X60": 1, "WKX": 0}      # 只做多波段版：關斜率出場、跌破 60MA 才出、抱過週末
    gC2 = "C. 黃金 TurtleMA（黃金資料下載完再跑）"
    out += [
        _t(gC2, "黃金 TurtleMA 預設（多空）", "TurtleMA", "XAUUSD"),
        _t(gC2, "黃金 TurtleMA 波段版", "TurtleMA", "XAUUSD", params=SWING, note="對照組是 B 的黃金 GuYuan_2025"),
    ]
    return out
