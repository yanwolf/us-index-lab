"""多空批次回測：同一組參數在多個指數上，各跑多方、空方，再算多空合跑（兩支各 1 單位，損益相加）。

台指上兩支幾乎不會同時持倉（空方進場條件 = 多方出場條件），合跑實際只佔約 1 單位保證金，
所以合跑的報酬% 用「兩腳損益相加 ÷ 當日收盤」計，跟單跑一支同一個基準。
評估重點（交接文件）：空方的價值在對沖，看「多空合跑的回復因子」有沒有高於「只跑多方」。
"""
import numpy as np

from . import backtest, metrics, store
from .instruments import INSTRUMENTS, cost_defaults

WARMUP_DAYS = backtest.WARMUP_DAYS


def _overlap_days(r1, r2):
    """兩支同時持倉的交易次數（粗估：時間區間重疊）"""
    a = sorted((t["entry_ts"], t["exit_ts"]) for t in r1)
    b = sorted((t["entry_ts"], t["exit_ts"]) for t in r2)
    n, j = 0, 0
    for s, e in a:
        while j < len(b) and b[j][1] <= s:
            j += 1
        k = j
        while k < len(b) and b[k][0] < e:
            n += 1; k += 1
    return n


def _summ(trades, daily):
    s = metrics.summarize(trades, daily)
    s["rf"] = round(s["net_pts"] / s["mdd_pts"], 2) if s["mdd_pts"] > 0 else 0.0      # 回復因子 = 淨利 / 最大回撤
    return s


def run_one(source, symbol, legs, start=None, end=None, slip_mult=1.0):
    costs = cost_defaults(symbol)
    costs["slippage"] = costs["slippage"] * float(slip_mult)
    res = {}
    for name, strat, params in legs:
        r = backtest.run({"source": source, "symbol": symbol, "strategy": strat, "params": params,
                          "start": start, "end": end, "costs": costs})
        res[name] = r
    out = {"symbol": symbol, "name": INSTRUMENTS[symbol]["name"], "costs": costs, "rows": []}
    for name, r in res.items():
        out["rows"].append({"leg": name, **_summ(r["trades"], r["daily"])})
    if len(res) == 2:
        (n1, r1), (n2, r2) = list(res.items())
        d1 = {d[0]: d for d in r1["daily"]}
        d2 = {d[0]: d for d in r2["daily"]}
        days = sorted(set(d1) | set(d2))
        last1 = last2 = 0.0
        comb = []
        for td in days:
            if td in d1: last1 = d1[td][1]
            if td in d2: last2 = d2[td][1]
            c = (d1.get(td) or d2.get(td))[2]
            comb.append((td, last1 + last2, c))
        trades = sorted(r1["trades"] + r2["trades"], key=lambda t: t["exit_ts"])
        row = {"leg": "多空合跑", **_summ(trades, comb), "overlap": _overlap_days(r1["trades"], r2["trades"])}
        out["rows"].append(row)
        yearly = {}
        for nm, r in ((n1, r1), (n2, r2)):
            for y in metrics.yearly(r["trades"]):
                yearly.setdefault(y["year"], {})[nm] = y["ret_pct"]
        out["yearly"] = [{"year": y, **{nm: round(v.get(nm, 0.0), 2) for nm in (n1, n2)},
                          "合跑": round(v.get(n1, 0.0) + v.get(n2, 0.0), 2)} for y, v in sorted(yearly.items())]
        out["legs"] = [n1, n2]
    any_daily = next(iter(res.values()))["daily"]
    bh = metrics.buy_hold(any_daily)
    bh.pop("curve_pct", None)
    if bh:
        bh["ann_mdd"] = round(bh["ann_pct"] / bh["mdd_pct"], 2) if bh["mdd_pct"] > 0 else 0.0
    out["buy_hold"] = bh
    out["range"] = [metrics.tday_str(any_daily[0][0]), metrics.tday_str(any_daily[-1][0])] if any_daily else None
    return out


def run(source, symbols, legs, start=None, end=None, slip_mult=1.0, progress=None, stop_flag=None):
    have = {(x["source"], x["symbol"]) for x in store.inventory()}
    out, skipped = [], []
    for k, sym in enumerate(symbols):
        if stop_flag and stop_flag():
            break
        if (source, sym) not in have:
            skipped.append(sym)
            if progress:
                progress(k + 1, len(symbols))
            continue
        out.append(run_one(source, sym, legs, start, end, slip_mult))
        if progress:
            progress(k + 1, len(symbols))
    return {"source": source, "results": out, "skipped": skipped, "legs": [l[0] for l in legs],
            "params": {l[0]: {"strategy": l[1], "params": l[2]} for l in legs}, "slip_mult": slip_mult}
