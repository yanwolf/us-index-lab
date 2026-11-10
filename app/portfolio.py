"""策略組合：多個「商品 × 策略 × 參數」一起跑，看分散後的總權益與回撤。

做法：每一腳各自回測，把每日損益換成「當日收盤的 %」（1 倍名目、不複利，和回測頁一致），
以交易日的日期對齊（各市場用自己的交易日；某市場當天休市就算 0），
組合每日報酬 = Σ 權重 × 該腳報酬 ÷ Σ 權重（等於把資金依權重分給各腳）。
不同幣別（美元、日圓、港幣）都用 % 計，忽略匯率變動。
"""
from datetime import timedelta

import numpy as np
import pandas as pd

from . import backtest, metrics, store

WARMUP_DAYS = 40


def _daily_ret(daily):
    """[(tday, eq_pts, close)] → {日期字串: 當日報酬 %}"""
    out = {}
    prev = 0.0
    for td, eq, c in daily:
        out[metrics.tday_str(td)] = (eq - prev) / c * 100.0 if c else 0.0
        prev = eq
    return out


def _stats(dates, rets):
    r = np.asarray(rets, dtype=float)
    curve = np.cumsum(r)
    peak = np.maximum.accumulate(np.concatenate([[0.0], curve]))[1:]
    mdd = float((peak - curve).max()) if len(curve) else 0.0
    years = max((pd.Timestamp(dates[-1]) - pd.Timestamp(dates[0])).days / 365.25, 1e-9) if dates else 0
    total = float(curve[-1]) if len(curve) else 0.0
    ann = total / years if years else 0.0
    sd = r.std()
    return {"ret_pct": round(total, 2), "ann_pct": round(ann, 2), "mdd_pct": round(mdd, 2),
            "ann_mdd": round(ann / mdd, 2) if mdd > 0 else 0.0,
            "sharpe": round(float(r.mean() / sd * np.sqrt(252)), 2) if sd > 0 else 0.0,
            "years": round(years, 2)}, curve


def default_range(legs):
    inv = {(x["source"], x["symbol"]): x for x in store.inventory()}
    firsts, lasts = [], []
    for L in legs:
        x = inv.get((L.get("source", "duka"), L["symbol"]))
        if not x or not x["first"]:
            raise ValueError(f"{L.get('source', 'duka')}/{L['symbol']} 沒有資料")
        firsts.append(pd.to_datetime(x["first"], unit="s"))
        lasts.append(pd.to_datetime(x["last"], unit="s"))
    start = (max(firsts) + timedelta(days=WARMUP_DAYS)).strftime("%Y-%m-%d")
    end = min(lasts).strftime("%Y-%m-%d")
    return start, end


def run(legs, start=None, end=None, max_points=600):
    if not legs:
        raise ValueError("組合裡還沒有任何策略")
    if len(legs) > 12:
        raise ValueError("最多 12 腳")
    if not start or not end:
        s0, e0 = default_range(legs)
        start, end = start or s0, end or e0
    per, names, weights, summaries = [], [], [], []
    for k, L in enumerate(legs):
        req = {kk: L.get(kk) for kk in ("source", "symbol", "strategy", "minutes", "align", "params", "costs")}
        req["source"] = req["source"] or "duka"
        req["start"], req["end"] = start, end
        res = backtest.run(req, collect_trades=True)
        s = metrics.summarize(res["trades"], res["daily"], res["bars_in_pos"], res["n_bars"])
        per.append(_daily_ret(res["daily"]))
        w = float(L.get("weight", 1) or 0)
        weights.append(w)
        names.append(f"{L['symbol']} {L['strategy']}")
        summaries.append({"name": names[-1], "weight": w, "summary": s})
    dates = sorted(set().union(*[set(p) for p in per]))
    if not dates:
        raise ValueError("這段期間沒有任何交易日資料")
    M = np.array([[p.get(d, 0.0) for d in dates] for p in per])        # 腳 × 日
    W = np.array(weights)
    if W.sum() <= 0:
        raise ValueError("權重總和要大於 0")
    combo = (W[:, None] * M).sum(axis=0) / W.sum()
    cstat, ccurve = _stats(dates, combo)
    legs_out = []
    curves = []
    for k in range(len(per)):
        st, cv = _stats(dates, M[k])
        legs_out.append({**summaries[k], "stats": st})
        curves.append(cv)
    # 相關係數（只用至少一邊有部位變動的日子）
    corr = None
    if len(per) > 1:
        cm = np.corrcoef(M)
        corr = [[round(float(v), 2) if v == v else None for v in row] for row in cm]
    # 分散效果：各腳回撤的加權平均 vs 組合回撤
    avg_mdd = float(sum(W[k] * legs_out[k]["stats"]["mdd_pct"] for k in range(len(per))) / W.sum())
    idx = np.unique(np.linspace(0, len(dates) - 1, min(max_points, len(dates))).astype(int))
    series = {"dates": [dates[i] for i in idx], "combo": [round(float(ccurve[i]), 3) for i in idx],
              "legs": [[round(float(c[i]), 3) for i in idx] for c in curves]}
    yearly = {}
    for d, v in zip(dates, combo):
        yearly.setdefault(d[:4], 0.0)
        yearly[d[:4]] += v
    leg_years = [{y: 0.0 for y in yearly} for _ in per]
    for k in range(len(per)):
        for d, v in zip(dates, M[k]):
            leg_years[k][d[:4]] += v
    ytab = [{"year": y, "combo": round(float(yearly[y]), 2), "legs": [round(float(leg_years[k][y]), 2) for k in range(len(per))]}
            for y in sorted(yearly)]
    return {"range": [start, end], "combo": cstat, "legs": legs_out, "names": names, "corr": corr,
            "avg_leg_mdd": round(avg_mdd, 2), "series": series, "yearly": ytab}
