"""一鍵套用的回測／掃描範本（2026-11 空方互補研究）。
資料一律用 Dukascopy 2021 起（含 2022 美股空頭、2021–22 恆指空頭），成本用商品預設（Pepperstone 點差＋滑價＋隔夜利息）。
grid 空的 = 單純回測；有 grid = 最佳化（目標報酬／回撤、平台挑選、滾動驗證）。"""
from .strategies import TXF_PRESET

START = "2021-02-05"          # Dukascopy 第一筆 2021-01-03，留一個月暖機
SYM_NAME = {"NAS100": "那指", "HK50": "恆指", "JP225": "日經", "US500": "標普"}

GY_S_GRID = {"TF_N": [0, 200, 100], "MA2": [0, 100, 50], "WIN": [1.5, 4.5, 0.5], "EBX": [1, 2, 1]}      # 126 組
ARX_S_GRID = {"TF_N": [0, 200, 100], "LOSS": [0.6, 1.2, 0.2], "WIN": [2, 5.5, 0.5]}                     # 96 組
TMFF_S_GRID = {"TF_N": [0, 200, 100], "SS": [0, 0.4, 0.1], "SLOSS": [0.6, 1.4, 0.2], "SWIN": [1.8, 3.8, 1]}  # 225 組


def _t(group, name, strategy, symbol, params=None, grid=None, note=""):
    return dict(group=group, name=name, strategy=strategy, symbol=symbol, source="duka", start=START, end=None,
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
