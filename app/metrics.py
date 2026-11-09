"""績效統計。點數是「每 1 單位指數」的點數；% 是以進場價為本金、1 倍名目的報酬（不複利），
方便直接跟「買進持有指數」比較。"""
import math
from datetime import date, datetime, timedelta, timezone

import numpy as np

EPOCH = date(1970, 1, 1)


def tday_str(o):
    return (EPOCH + timedelta(days=int(o))).isoformat()


def _mdd(curve):
    if len(curve) == 0:
        return 0.0
    c = np.asarray(curve, dtype=float)
    peak = np.maximum.accumulate(np.concatenate([[0.0], c]))[1:]
    return float((peak - c).max()) if len(c) else 0.0


def summarize(trades, daily, bars_in_pos=0, n_bars=0):
    """trades：engine 的 dict 列表或精簡 tuple 列表（entry_ts, net, ret_pct, dir）"""
    if trades and isinstance(trades[0], dict):
        nets = np.array([t["net"] for t in trades])
        rets = np.array([t["ret_pct"] for t in trades])
        dirs = np.array([t["dir"] for t in trades])
    elif trades:
        nets = np.array([t[1] for t in trades])
        rets = np.array([t[2] for t in trades])
        dirs = np.array([t[3] for t in trades])
    else:
        nets = rets = dirs = np.array([])
    n = len(nets)
    wins, losses = nets[nets > 0], nets[nets <= 0]
    gp, gl = float(wins.sum()), float(-losses.sum())
    eq = np.array([d[1] for d in daily]) if daily else np.array([])
    closes = np.array([d[2] for d in daily]) if daily else np.array([])
    mdd = _mdd(eq)
    # 以 % 計的每日損益：當日點數變化 / 當日收盤
    if len(eq) > 1:
        dpnl = np.diff(np.concatenate([[0.0], eq]))
        dret = dpnl / closes * 100.0
        eq_pct = np.cumsum(dret)
        sharpe = float(dret.mean() / dret.std() * math.sqrt(252)) if dret.std() > 0 else 0.0
        mdd_pct = _mdd(eq_pct)
        years = max((daily[-1][0] - daily[0][0]) / 365.25, 1e-9)
    else:
        eq_pct, sharpe, mdd_pct, years = np.array([]), 0.0, 0.0, 0.0
    ret_total = float(rets.sum()) if n else 0.0
    net = float(nets.sum()) if n else 0.0
    streak = mx = 0
    for x in nets:
        streak = streak + 1 if x <= 0 else 0
        mx = max(mx, streak)
    out = {
        "trades": n,
        "net_pts": round(net, 1),
        "ret_pct": round(ret_total, 2),
        "ann_pct": round(ret_total / years, 2) if years else 0.0,
        "win_rate": round(len(wins) / n * 100, 1) if n else 0.0,
        "pf": round(gp / gl, 2) if gl > 0 else (99.0 if gp > 0 else 0.0),
        "avg_pts": round(net / n, 2) if n else 0.0,
        "avg_win": round(float(wins.mean()), 2) if len(wins) else 0.0,
        "avg_loss": round(float(losses.mean()), 2) if len(losses) else 0.0,
        "payoff": round(float(wins.mean()) / abs(float(losses.mean())), 2) if len(wins) and len(losses) and losses.mean() != 0 else 0.0,
        "mdd_pts": round(mdd, 1),
        "mdd_pct": round(mdd_pct, 2),
        "net_mdd": round(net / mdd, 2) if mdd > 0 else (99.0 if net > 0 else 0.0),
        "sharpe": round(sharpe, 2),
        "max_consec_loss": mx,
        "exposure_pct": round(bars_in_pos / n_bars * 100, 1) if n_bars else 0.0,
        "long_trades": int((dirs > 0).sum()) if n else 0,
        "short_trades": int((dirs < 0).sum()) if n else 0,
        "long_pts": round(float(nets[dirs > 0].sum()), 1) if n else 0.0,
        "short_pts": round(float(nets[dirs < 0].sum()), 1) if n else 0.0,
        "years": round(years, 2),
    }
    return out


def buy_hold(daily):
    if len(daily) < 2:
        return {}
    c = np.array([d[2] for d in daily])
    first = c[0]
    ret = (c[-1] / first - 1) * 100
    pct = (c / first - 1) * 100
    peak = np.maximum.accumulate(c)
    mdd_pct = float(((peak - c) / peak).max() * 100)
    years = max((daily[-1][0] - daily[0][0]) / 365.25, 1e-9)
    dr = np.diff(c) / c[:-1]
    sharpe = float(dr.mean() / dr.std() * math.sqrt(252)) if dr.std() > 0 else 0.0
    return {"ret_pct": round(float(ret), 2), "ann_pct": round(float(ret) / years, 2), "mdd_pct": round(mdd_pct, 2),
            "sharpe": round(sharpe, 2), "ret_mdd": round(float(ret) / mdd_pct, 2) if mdd_pct > 0 else 0.0,
            "curve_pct": pct}


def yearly(trades):
    out = {}
    for t in trades:
        y = datetime.fromtimestamp(t["exit_ts"] + 2 * 3600 - 6 * 3600, timezone.utc).year   # 約略美東日期
        r = out.setdefault(y, {"year": y, "trades": 0, "net_pts": 0.0, "ret_pct": 0.0, "wins": 0})
        r["trades"] += 1
        r["net_pts"] += t["net"]
        r["ret_pct"] += t["ret_pct"]
        r["wins"] += t["net"] > 0
    rows = []
    for y in sorted(out):
        r = out[y]
        rows.append({"year": y, "trades": r["trades"], "net_pts": round(float(r["net_pts"]), 1),
                     "ret_pct": round(float(r["ret_pct"]), 2), "win_rate": round(float(r["wins"]) / r["trades"] * 100, 1)})
    return rows


def equity_series(daily, max_points=600):
    """給前端畫圖：[日期, 策略累積 %, 買進持有累積 %]（抽樣到 max_points 點）"""
    if not daily:
        return []
    eq = np.array([d[1] for d in daily])
    c = np.array([d[2] for d in daily])
    dret = np.diff(np.concatenate([[0.0], eq])) / c * 100
    s = np.cumsum(dret)
    bh = (c / c[0] - 1) * 100
    idx = np.unique(np.linspace(0, len(daily) - 1, min(max_points, len(daily))).astype(int))
    return [[tday_str(daily[k][0]), round(float(s[k]), 3), round(float(bh[k]), 3), round(float(eq[k]), 1)] for k in idx]
