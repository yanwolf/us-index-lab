"""參數最佳化：網格搜尋 + 兩種驗證，全部用同一批回測結果算，不重跑。

每組參數只在「全區間」跑一次，保留交易與每日權益，之後依日期切段計算：

1. 單次切分：前段（樣本內）挑參數、後段（樣本外）只看不挑
2. 滾動驗證（walk-forward）：把區間切成 m+k 等份，第 i 次用 [i, i+m) 段訓練、
   挑出樣本內最好的參數，拿到第 i+m 段測試；k 次測試段接起來就是「如果當時照這套流程
   定期重新調參數，實際會拿到的績效」

每一段都附上同期「買進持有」，用來分辨賺的是策略還是行情。
另外算三個穩健度指標：
  · 排名一致性：樣本內與樣本外排名的 Spearman 相關，越接近 1 代表樣本內挑得準
  · 優於中位數：樣本內前 20% 的組合，樣本外贏過「全部組合樣本外中位數」的比例
    （扣掉行情的共同影響，比單看「有沒有賺」準）
  · 行情偏多警示：樣本內最差 20% 的組合，樣本外也賺錢的比例很高時，代表樣本外行情本身就有利
"""
import itertools
import math
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
import multiprocessing as mp

import numpy as np

from . import backtest, metrics

MAX_COMBOS = int(os.getenv("OPT_MAX_COMBOS", "3000"))
WORKERS = int(os.getenv("OPT_WORKERS", str(max(1, os.cpu_count() or 2))))
OBJECTIVES = {"net_mdd": "淨利/最大回撤", "pf": "獲利因子", "net_pts": "淨利點數", "sharpe": "Sharpe"}

_REQ = None


def frange(a, b, step):
    a, b, step = float(a), float(b), float(step)
    if step <= 0:
        return [a]
    n = int(round((b - a) / step)) + 1
    ints = all(float(x).is_integer() for x in (a, b, step))
    vals = [round(a + k * step, 6) for k in range(max(n, 1))]
    return [int(v) if ints else v for v in vals]


def combos(grid):
    keys = list(grid)
    vals = [frange(*grid[k]) for k in keys]
    total = int(np.prod([len(v) for v in vals])) if vals else 1
    return keys, vals, total


def segment(trades, daily, a, b):
    """取交易日 [a, b) 的交易與每日權益（權益以區段起點歸零）。"""
    tr = [t for t in trades if a <= t[4] < b]     # t[4] = 進場交易日
    base = 0.0
    d = []
    for x in daily:
        if x[0] < a:
            base = x[1]
        elif x[0] < b:
            d.append((x[0], x[1] - base, x[2]))
    return tr, d


def seg_summary(trades, daily, a, b):
    tr, d = segment(trades, daily, a, b)
    s = metrics.summarize(tr, d)
    s["ret_mdd"] = round(s["ret_pct"] / s["mdd_pct"], 2) if s["mdd_pct"] > 0 else (99.0 if s["ret_pct"] > 0 else 0.0)
    return s


def bh_segment(daily, a, b):
    d = [x for x in daily if a <= x[0] < b]
    if len(d) < 2:
        return {"ret_pct": 0.0, "mdd_pct": 0.0, "ret_mdd": 0.0}
    bh = metrics.buy_hold(d)
    return {"ret_pct": bh["ret_pct"], "mdd_pct": bh["mdd_pct"], "ret_mdd": bh["ret_mdd"]}


def _eval(params):
    req = dict(_REQ)
    req["params"] = {**(req.get("params") or {}), **params}
    res = backtest.run(req, collect_trades=False)
    return params, res["trades"], res["daily"]


def _init(req):
    global _REQ
    _REQ = req


def _spearman(x, y):
    if len(x) < 3:
        return None
    rx = np.argsort(np.argsort(x)).astype(float)
    ry = np.argsort(np.argsort(y)).astype(float)
    if rx.std() == 0 or ry.std() == 0:
        return None
    return round(float(np.corrcoef(rx, ry)[0, 1]), 2)


def _obj(s, objective):
    v = s.get(objective, 0.0)
    return v if v == v else 0.0


def run(req, grid, split=0.67, objective="net_mdd", min_trades=30, wf_k=4, wf_m=2,
        progress=None, stop_flag=None, top=60):
    keys, vals, total = combos(grid)
    if total > MAX_COMBOS:
        raise ValueError(f"組合數 {total:,} 超過上限 {MAX_COMBOS:,}，請放大步距或減少參數")
    base = backtest.run({**req, "params": req.get("params") or {}}, collect_trades=False)
    if not base["daily"]:
        raise ValueError("這個區間沒有資料")
    bdaily = base["daily"]
    d0, d1 = bdaily[0][0], bdaily[-1][0] + 1
    split_tday = int(d0 + (d1 - d0) * float(split))

    plist = [dict(zip(keys, v)) for v in itertools.product(*vals)]
    raw = []
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
                raw.append(f.result())
                done += 1
                if progress:
                    progress(done, total)
    else:
        _init(req)
        for p in plist:
            if stop_flag and stop_flag():
                break
            raw.append(_eval(p))
            done += 1
            if progress:
                progress(done, total)

    # ---------------------------------------------------------- 單次切分
    rows = []
    for params, trades, daily in raw:
        if not daily:
            continue
        s_is = seg_summary(trades, daily, d0, split_tday)
        s_os = seg_summary(trades, daily, split_tday, d1)
        rows.append({"params": params, "is": s_is, "os": s_os, "ok": s_is["trades"] >= int(min_trades)})
    rows.sort(key=lambda r: (r["ok"], _obj(r["is"], objective)), reverse=True)
    valid = [r for r in rows if r["ok"]]
    n_ok = len(valid)
    q = max(1, n_ok // 5)
    head, tail = valid[:q], valid[-q:]
    robust = round(sum(r["os"]["net_pts"] > 0 for r in head) / len(head) * 100, 1) if head else 0.0
    tail_profit = round(sum(r["os"]["net_pts"] > 0 for r in tail) / len(tail) * 100, 1) if tail else 0.0
    os_vals = [_obj(r["os"], objective) for r in valid]
    med = float(np.median(os_vals)) if os_vals else 0.0
    beat_med = round(sum(_obj(r["os"], objective) > med for r in head) / len(head) * 100, 1) if head else 0.0
    rank_corr = _spearman([_obj(r["is"], objective) for r in valid], os_vals) if n_ok >= 3 else None
    regime_flag = n_ok >= 5 and tail_profit >= 80

    split_res = {
        "split_date": metrics.tday_str(split_tday), "robust_pct": robust, "beat_median_pct": beat_med,
        "tail_profit_pct": tail_profit, "rank_corr": rank_corr, "regime_flag": regime_flag,
        "bh_is": bh_segment(bdaily, d0, split_tday), "bh_os": bh_segment(bdaily, split_tday, d1),
        "rows": rows[:top],
    }

    # ---------------------------------------------------------- 滾動驗證
    wf = None
    k, m = int(wf_k), int(wf_m)
    if k >= 1 and m >= 1 and raw:
        nblk = m + k
        bounds = [int(round(d0 + (d1 - d0) * j / nblk)) for j in range(nblk + 1)]
        bounds[-1] = d1
        fold_min = max(5, int(round(int(min_trades) * m / max(1.0, (split * nblk)))))
        folds = []
        st_trades, st_daily, offset = [], [], 0.0
        for i in range(k):
            ta, tb, sa, sb = bounds[i], bounds[i + m], bounds[i + m], bounds[i + m + 1]
            best, best_v = None, None
            for params, trades, daily in raw:
                if not daily:
                    continue
                s = seg_summary(trades, daily, ta, tb)
                if s["trades"] < fold_min:
                    continue
                v = _obj(s, objective)
                if best_v is None or v > best_v:
                    best, best_v = (params, trades, daily, s), v
            if best is None:
                folds.append({"train": [metrics.tday_str(ta), metrics.tday_str(tb - 1)],
                              "test": [metrics.tday_str(sa), metrics.tday_str(sb - 1)], "params": None})
                continue
            params, trades, daily, s_tr = best
            s_te = seg_summary(trades, daily, sa, sb)
            folds.append({"train": [metrics.tday_str(ta), metrics.tday_str(tb - 1)],
                          "test": [metrics.tday_str(sa), metrics.tday_str(sb - 1)],
                          "params": params, "train_s": s_tr, "test_s": s_te, "bh": bh_segment(bdaily, sa, sb)})
            tr, d = segment(trades, daily, sa, sb)
            st_trades += tr
            st_daily += [(x[0], x[1] + offset, x[2]) for x in d]
            if d:
                offset = st_daily[-1][1]
        stitched = metrics.summarize(st_trades, st_daily) if st_daily else None
        if stitched:
            stitched["ret_mdd"] = round(stitched["ret_pct"] / stitched["mdd_pct"], 2) if stitched["mdd_pct"] > 0 else 0.0
        changes = sum(1 for a, b in zip(folds, folds[1:]) if a.get("params") and b.get("params") and a["params"] != b["params"])
        wf = {"k": k, "m": m, "fold_min_trades": fold_min, "folds": folds, "stitched": stitched,
              "bh": bh_segment(bdaily, bounds[m], d1), "param_changes": changes,
              "test_range": [metrics.tday_str(bounds[m]), metrics.tday_str(d1 - 1)]}

    return {"total": total, "done": done, "valid": n_ok, "objective": objective, "keys": keys,
            **split_res, "wf": wf}
