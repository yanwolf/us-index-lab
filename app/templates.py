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

    # 古典型態（2026-11-28）：Pattern 策略，先看各型態、多空分開有沒有優勢，再看參數平台
    g = "古典型態 Pattern（2026-11-28）"
    for sym in ("NAS100", "US500", "JP225", "HK50", "XAUUSD"):
        out.append(dict(_t(g, f"① {SYM_NAME[sym]}：各型態 × 多空 × 週末出場", "Pattern", sym,
                           grid={"PAT": [1, 6, 1], "SIDE": [-1, 1, 2], "WKX": [0, 15, 15]},
                           note="24 組：每一列 = 一種型態的單一方向（PAT 1 雙頂底、2 三重頂底、3 頭肩、4 上升／下降三角形、5 對稱三角形、6 楔形；"
                                "SIDE 1 多、-1 空；WKX 0 抱過週末、15 週五出場）。先看哪幾列交易數夠、PF > 1.2、樣本外也賺。"
                                "這是分類表不是參數，挑選方式用「只看自己」。"), rank_by="self", min_trades=15))
    for sym in ("NAS100", "US500", "JP225", "HK50", "XAUUSD"):
        out.append(dict(_t(g, f"② {SYM_NAME[sym]}：ZZ × TPX × SLB 平台（全部型態、多空）", "Pattern", sym,
                           grid={"ZZ": [2, 6, 1], "TPX": [0.6, 1.6, 0.2], "SLB": [0, 0.6, 0.2]},
                           note="120 組：轉折門檻、量度目標倍數、停損緩衝。看參數面是不是一整片都賺，還是只有孤峰。"
                                "①找到有優勢的型態後，把 PAT／SIDE 改成那一組再跑一次這個掃描。"), min_trades=30))
    return out
