"""一鍵套用的回測／掃描範本（2026-11 空方互補研究）。
資料一律用 Dukascopy 2021 起（含 2022 美股空頭、2021–22 恆指空頭），成本用商品預設（Pepperstone 點差＋滑價＋隔夜利息）。
grid 空的 = 單純回測；有 grid = 最佳化（目標報酬／回撤、平台挑選、滾動驗證）。"""
from .strategies import TXF_PRESET

START = "2021-02-05"          # Dukascopy 第一筆 2021-01-03，留一個月暖機
SYM_NAME = {"NAS100": "那指", "HK50": "恆指", "JP225": "日經", "US500": "標普"}

GY_S_GRID = {"TF_N": [0, 200, 100], "MA2": [0, 100, 50], "WIN": [1.5, 4.5, 0.5], "EBX": [1, 2, 1]}      # 126 組
ARX_S_GRID = {"TF_N": [0, 200, 100], "LOSS": [0.6, 1.2, 0.2], "WIN": [2, 5.5, 0.5]}                     # 96 組
TMFF_S_GRID = {"TF_N": [0, 200, 100], "SS": [0, 0.4, 0.1], "SLOSS": [0.6, 1.4, 0.2], "SWIN": [1.8, 3.8, 1]}  # 225 組


def _t(group, name, strategy, symbol, params=None, grid=None, note="", minutes=None, costs=None, start=START, end=None):
    return dict(group=group, name=name, strategy=strategy, symbol=symbol, source="duka", start=start, end=end, minutes=minutes, costs=costs,
                align="session", params=params or {}, grid=grid or {}, objective="ret_mdd", rank_by="plateau",
                min_trades=20, wf_k=4, wf_m=2, split=0.67, note=note)


def templates():
    out = [
        _t("① 先驗證：現有多方在 2021 起的表現", "日經多 台指參數（模擬倉 jp_gy24）", "GuYuan_2024", "JP225",
           note="看 2021–2023 橫盤與下跌期間虧多少"),
        _t("① 先驗證：現有多方在 2021 起的表現", "日經多 WIN9（模擬倉 jp_gy24_w9）", "GuYuan_2024", "JP225",
           params={"LOSS": 2, "WIN": 9, "EBX": 2}),
        _t("① 先驗證：現有多方在 2021 起的表現", "那指多 AR_crossover（模擬倉 nas_arx）", "AR_crossover", "NAS100",
           note="2022 空頭是重點"),
        _t("①b 多方補驗證", "日經多 台指參數 EBX=4（交接文件版）", "GuYuan_2024", "JP225", params={"EBX": 4},
           note="模擬倉 jp_gy24 是 EBX=0，這組看 EBX=4 在 2021 起差多少"),
        _t("①b 多方補驗證", "日經多 台指參數＋日線濾網 TF_N=200", "GuYuan_2024", "JP225", params={"TF_N": 200},
           note="前一日收盤在 200 日均線之上才進場，看能不能少虧空頭段"),
        _t("①b 多方補驗證", "那指多 AR_crossover＋日線濾網 TF_N=200", "AR_crossover", "NAS100", params={"TF_N": 200},
           note="2022 空頭期間應該幾乎不進場"),
        _t("①b 多方補驗證", "日經多 GuYuan_2025（20 分 K）", "GuYuan_2025", "JP225",
           note="之前另一段期間報酬/回撤 1.63，用同一段 2021 起重跑才能比"),
    ]
    g3 = "③ GuYuan_2025（目前最好的一支）"
    out += [
        _t(g3, "日經 GuYuan_2025 掃描（HN × LOSS × WIN）", "GuYuan_2025", "JP225",
           grid={"HN": [15, 35, 5], "LOSS": [0.6, 1.4, 0.2], "WIN": [4.2, 8.2, 1]}, note="125 組，MA1/MA2/ETD 維持原值"),
        _t(g3, "那指 GuYuan_2025（預設）", "GuYuan_2025", "NAS100", note="看能不能用在別的指數"),
        _t(g3, "標普 GuYuan_2025（預設）", "GuYuan_2025", "US500"),
        _t(g3, "恆指 GuYuan_2025（預設）", "GuYuan_2025", "HK50"),
    ]
    g4 = "④ 降低交易頻率（成本吃掉六成毛利）"
    out += [
        _t(g4, "日經 GuYuan_2024 改 120 分 K", "GuYuan_2024", "JP225", minutes=120, note="單位跟著 K 棒變大，交易數應該少一半左右"),
        _t(g4, "日經 GuYuan_2025 改 40 分 K", "GuYuan_2025", "JP225", minutes=40),
        _t(g4, "日經 GuYuan_2025 改 60 分 K", "GuYuan_2025", "JP225", minutes=60),
    ]
    g5 = "⑤ GuYuan_2025 候選確認（K 棒鄰近值、滑價加倍）"
    out += [_t(g5, f"日經 GuYuan_2025 {m} 分 K", "GuYuan_2025", "JP225", minutes=m,
               note="60 分 K 的 PF 1.32 是不是孤峰：看 50～120 分是否也不錯") for m in (50, 75, 90, 120)]
    out += [_t(g5, f"那指 GuYuan_2025 {m} 分 K", "GuYuan_2025", "NAS100", minutes=m) for m in (30, 40, 60)]
    out += [
        _t(g5, "日經 GuYuan_2025 60 分 K｜滑價加倍（每邊 6 點）", "GuYuan_2025", "JP225", minutes=60, costs={"slippage": 6}),
        _t(g5, "那指 GuYuan_2025 20 分 K｜滑價加倍（每邊 1 點）", "GuYuan_2025", "NAS100", costs={"slippage": 1}),
        _t(g5, "那指 GuYuan_2024_S（WIN 2、MA2 100、EBX 2）逐年", "GuYuan_2024_S", "NAS100",
           params=dict(TXF_PRESET["GuYuan_2024_S"], WIN=2, MA2=100, EBX=2), note="空方掃描裡前後兩段都賺的區域，看 2022 年貢獻多少"),
    ]
    S_PARAMS = dict(TXF_PRESET["GuYuan_2024_S"], WIN=2, MA2=100, EBX=2)
    g6 = "⑥ 新組合的鄰近值檢查"
    out += [_t(g6, f"那指 GuYuan_2025 {m} 分 K", "GuYuan_2025", "NAS100", minutes=m,
               note="20→30→40→60 分一路變好，看 60 分之後是平台還是掉下去") for m in (75, 90, 120)]
    out += [
        _t(g6, "那指 GuYuan_2024_S 鄰近參數掃描（WIN × MA2 × EBX）", "GuYuan_2024_S", "NAS100", params=S_PARAMS,
           grid={"WIN": [1.5, 2.5, 0.5], "MA2": [50, 150, 50], "EBX": [1, 3, 1]}, note="27 組，看 WIN 2／MA2 100／EBX 2 旁邊是不是也賺"),
        _t(g6, "那指 GuYuan_2024_S｜滑價加倍（每邊 1 點）", "GuYuan_2024_S", "NAS100", params=S_PARAMS, costs={"slippage": 1}),
        _t(g6, "那指 GuYuan_2025 60 分 K｜滑價加倍（每邊 1 點）", "GuYuan_2025", "NAS100", minutes=60, costs={"slippage": 1}),
    ]
    g7 = "⑦ 真正的樣本外：2018–2020（要先在「資料」頁補下載，開始日填 2017-12-01）"
    OS = dict(start="2018-01-02", end="2020-12-31")
    out += [
        _t(g7, "日經 GuYuan_2025 20 分 K｜2018–2020", "GuYuan_2025", "JP225", **OS, note="這段資料完全沒參與挑選，含 2018 Q4 下跌、2020 疫情崩跌"),
        _t(g7, "那指 GuYuan_2025 60 分 K｜2018–2020", "GuYuan_2025", "NAS100", minutes=60, **OS),
        _t(g7, "那指 GuYuan_2024_S（WIN 2、MA2 100、EBX 2）｜2018–2020", "GuYuan_2024_S", "NAS100", params=S_PARAMS, **OS),
    ]
    for sym in ("NAS100", "HK50", "JP225"):
        n = SYM_NAME[sym]
        g = f"② 空方掃描：{n}（{sym}）"
        out += [
            _t(g, f"{n} GuYuan_2024_S 趨勢濾網掃描", "GuYuan_2024_S", sym, params=dict(TXF_PRESET["GuYuan_2024_S"]),
               grid=GY_S_GRID, note="TF_N × MA2 × WIN × EBX，其他維持台指參數"),
            _t(g, f"{n} AR_crossunder 掃描", "AR_crossunder", sym, grid=ARX_S_GRID, note="TF_N × LOSS × WIN"),
            _t(g, f"{n} TMFF_S 掃描", "TMFF_S", sym, grid=TMFF_S_GRID, note="TF_N × SS × SLOSS × SWIN"),
        ]
    return out
