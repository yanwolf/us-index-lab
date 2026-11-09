"""參數最佳化：網格搜尋 + 樣本內/樣本外切分。

做法：每組參數只在「全區間」跑一次，再依進場時間把交易與每日權益切成
樣本內（前段，用來挑參數）與樣本外（後段，只看不挑）。排序只看樣本內，
樣本外的數字是用來檢查：挑出來的參數換到沒看過的行情還撐不撐得住。
"""
import itertools
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
import multiprocessing as mp

import numpy as np

from . import backtest, metrics

MAX_COMBOS = int(os.getenv("OPT_MAX_COMBOS", "3000"))
WORKERS = int(os.getenv("OPT_WORKERS", str(max(1, (os.cpu_count() or 2) - 0))))
OBJECTIVES = {"net_mdd": "淨利/最大回撤", "pf": "獲利因子", "net_pts": "淨利點數", "sharpe": "Sharpe"}

_REQ = None


def frange(a, b, step):
    a, b, step = float(a), float(b), float(step)
    if step <= 0:
        return [a]
    n = int(round((b - a) / step)) + 1
    vals = [round(a + k * step, 6) for k in range(max(n, 1))]
    return [int(v) if float(v).is_integer() and all(float(x).is_integer() for x in (a, b, step)) else v for v in vals]


def combos(grid):
    keys = list(grid)
    vals = [frange(*grid[k]) for k in keys]
    total = int(np.prod([len(v) for v in vals])) if vals else 1
    return keys, vals, total


def _tday_of(ts):
    """UTC 秒 → 交易日序數。美東 18:00 切換；17:00–18:00 休市，所以 UTC+2h 在冬夏令都落在正確的日子。"""
    return (int(ts) + 2 * 3600) // 86400


def _split(trades, daily, split_tday):
    tr_is = [t for t in trades if _tday_of(t[0]) < split_tday]
    tr_os = [t for t in trades if _tday_of(t[0]) >= split_tday]
    d_is = [d for d in daily if d[0] < split_tday]
    d_os = [d for d in daily if d[0] >= split_tday]
    if d_os:
        base = d_is[-1][1] if d_is else 0.0
        d_os = [(d[0], d[1] - base, d[2]) for d in d_os]
    return tr_is, d_is, tr_os, d_os


def _eval(params):
    req = dict(_REQ)
    req["params"] = {**(req.get("params") or {}), **params}
    res = backtest.run(req, collect_trades=False)
    daily, trades = res["daily"], res["trades"]
    if not daily:
        return params, None, None
    split = req["_split_tday"]
    tr_is, d_is, tr_os, d_os = _split(trades, daily, split)
    frac_is = len(d_is) / max(len(daily), 1)
    s_is = metrics.summarize(tr_is, d_is, int(res["bars_in_pos"] * frac_is), int(res["n_bars"] * frac_is))
    s_os = metrics.summarize(tr_os, d_os, 0, 0)
    return params, s_is, s_os


def _init(req):
    global _REQ
    _REQ = req


def run(req, grid, split=0.67, objective="net_mdd", min_trades=30, progress=None, stop_flag=None, top=60):
    keys, vals, total = combos(grid)
    if total > MAX_COMBOS:
        raise ValueError(f"組合數 {total:,} 超過上限 {MAX_COMBOS:,}，請放大步距或減少參數")
    # 先跑一次預設值取得交易日範圍，決定切分點
    base = backtest.run({**req, "params": req.get("params") or {}}, collect_trades=False)
    if not base["daily"]:
        raise ValueError("這個區間沒有資料")
    d0, d1 = base["daily"][0][0], base["daily"][-1][0]
    split_tday = int(d0 + (d1 - d0) * float(split))
    req = {**req, "_split_tday": split_tday}
    plist = [dict(zip(keys, v)) for v in itertools.product(*vals)]
    results = []
    done = 0
    if WORKERS > 1 and total > 4:
        ctx = mp.get_context("fork")
        with ProcessPoolExecutor(max_workers=WORKERS, mp_context=ctx, initializer=_init, initargs=(req,)) as ex:
            futs = [ex.submit(_eval, p) for p in plist]
            for f in as_completed(futs):
                if stop_flag and stop_flag():
                    for g in futs:
                        g.cancel()
                    break
                results.append(f.result())
                done += 1
                if progress:
                    progress(done, total)
    else:
        _init(req)
        for p in plist:
            if stop_flag and stop_flag():
                break
            results.append(_eval(p))
            done += 1
            if progress:
                progress(done, total)
    rows = []
    for params, s_is, s_os in results:
        if s_is is None:
            continue
        ok = s_is["trades"] >= int(min_trades)
        rows.append({"params": params, "is": s_is, "os": s_os, "ok": ok})
    rows.sort(key=lambda r: (r["ok"], r["is"].get(objective, 0)), reverse=True)
    n_ok = sum(r["ok"] for r in rows)
    # 穩健度參考：樣本內前 20% 的組合，樣本外有幾成是賺錢的
    head = [r for r in rows if r["ok"]][: max(1, n_ok // 5)]
    robust = round(sum(r["os"]["net_pts"] > 0 for r in head) / len(head) * 100, 1) if head else 0.0
    return {"total": total, "done": done, "valid": n_ok, "split_date": metrics.tday_str(split_tday),
            "objective": objective, "robust_pct": robust, "rows": rows[:top], "keys": keys}
