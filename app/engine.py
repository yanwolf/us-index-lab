"""回測引擎：PowerLanguage 語意 + 1 分 K 盤中觸價。

流程（與 MultiCharts、txf-sim 一致）：
  · 每根 N 分 K 收盤呼叫一次 strategy.on_bar()，策略在裡面掛「下一根 K」的單
  · 下一根 K 期間逐分鐘檢查觸價；市價單在該 K 第一分鐘開盤成交
  · stop/limit 以掛單價成交；若該分鐘開盤已跳空穿過，以開盤價成交（對你不利的那邊）
  · 一根 K 內每支策略最多成交一次（MC 預設、不開 bar magnifier）；
    反手（持多時 sellshort 觸發）算一次成交：平多 + 開空同價
  · 同一分鐘內同時碰到停損與停利，以停損先成交（保守）

和台指版的差別：
  · 停損停利以「單位」計：單位 = 進場前一根 K 收盤時的 AvgRange(UNIT_N)，或價格的 0.1%（UNIT_MODE=1）
    所以參數是波動倍數，不受指數點位高低影響
  · 沒有結算日；週末規則：每週最後一筆報價前 WKX 分鐘起，市價出場並停止進場
    （直接看資料的休市缺口，任何市場、提早收盤、長假都適用）
  · 成本：每邊 spread/2 + slippage + commission，另計隔夜利息
"""
import numpy as np

from . import bars as B


class Order:
    __slots__ = ("kind", "action", "price", "label")

    def __init__(self, kind, action, price=None, label=""):
        self.kind, self.action, self.price, self.label = kind, action, price, label


COMMON_INPUTS = {"UNIT_N": 100, "UNIT_MODE": 0, "WKX": 15, "DAYX": 0}
COMMON_DOC = {"UNIT_N": "停損停利單位：AvgRange 期數（UNIT_MODE=0）",
              "UNIT_MODE": "單位模式：0 = AvgRange(UNIT_N)、1 = 價格的 0.1%",
              "WKX": "週末（或長假）休市前幾分鐘出場且不再進場；0 = 抱單過週末",
              "DAYX": "日內出場：每個交易日收盤前幾分鐘出場且當天不再進場，避開隔夜利息；0 = 關閉"}


class Strategy:
    name = "base"
    desc = ""
    minutes = 60
    inputs = {}
    doc = {}

    def __init__(self, params=None):
        self.p = dict(COMMON_INPUTS)
        self.p.update(self.inputs)
        if params:
            for k, v in params.items():
                if k in self.p:
                    self.p[k] = v
        self.mp = 0
        self.mp_prev = 0
        self.entryprice = None
        self.maxprofit = 0.0          # 價格單位（EL maxpositionprofit / bpv）
        self.entries_today = 0
        self.u = 1.0                  # 目前部位的停損停利單位（進場時凍結）
        self.u_now = 1.0
        self.orders = []
        self.b = None
        self.i = 0

    # ---------------------------------------------------------- 資料
    def _j(self, n):
        j = self.i - int(n)
        return j if j >= 0 else 0

    def C(self, n=0): return self.b.c[self._j(n)]
    def H(self, n=0): return self.b.h[self._j(n)]
    def L(self, n=0): return self.b.l[self._j(n)]
    def O(self, n=0): return self.b.o[self._j(n)]

    def avg(self, key, n, shift=0): return B.sma(self.b, key, n)[self._j(shift)]
    def avgrange(self, n, shift=0): return B.avgrange(self.b, n)[self._j(shift)]
    def highest(self, key, n, shift=0): return B.highest(self.b, key, n)[self._j(shift)]
    def lowest(self, key, n, shift=0): return B.lowest(self.b, key, n)[self._j(shift)]
    def swinghigh(self, strength, length): return B.swinghigh(self.b, strength, length)[self.i]
    def swinglow(self, strength, length): return B.swinglow(self.b, strength, length)[self.i]

    def closeD(self, n):
        if n == 0:
            return self.C()
        k = self.b.day_idx[self.i] - int(n)
        return self.b.day_close[max(k, 0)]

    def closeS(self, n):
        if n == 0:
            return self.C()
        k = self.b.ses_idx[self.i] - int(n)
        return self.b.ses_close[max(k, 0)]

    def highW(self, n):
        if n == 0:
            return self.b.wtd_high[self.i]
        return self.b.week_high[max(self.b.week_idx[self.i] - int(n), 0)]

    def lowW(self, n):
        if n == 0:
            return self.b.wtd_low[self.i]
        return self.b.week_low[max(self.b.week_idx[self.i] - int(n), 0)]

    def trend_ok(self, n, side=1):
        """趨勢濾網：前一個完整交易日收盤在 n 日均線之上（side=1）或之下（side=-1）。
        n=0 關閉（永遠通過）；歷史不足 n 天時不通過。只看已收盤的日線，沒有偷看。"""
        n = int(n)
        if n <= 0:
            return True
        k = self.b.day_idx[self.i] - 1
        if k < 0:
            return False
        ma = B.daily_sma(self.b, n)[k]
        if ma != ma:
            return False
        c = self.b.day_close[k]
        return c > ma if side > 0 else c < ma

    def U(self, x):
        """x 個單位換成價格距離（用進場時凍結的單位）"""
        return float(x) * self.u

    # ---------------------------------------------------------- 掛單
    def _add(self, kind, action, price=None, label=""):
        if price is not None and (price <= 0 or price != price):
            return
        self.orders.append(Order(kind, action, price, label))

    def buy_market(self, label="buy"): self._add("market", "buy", None, label)
    def buy_stop(self, price, label="buy stop"): self._add("stop", "buy", price, label)
    def buy_limit(self, price, label="buy limit"): self._add("limit", "buy", price, label)
    def sellshort_market(self, label="sellshort"): self._add("market", "sellshort", None, label)
    def sellshort_stop(self, price, label="sellshort stop"): self._add("stop", "sellshort", price, label)
    def sellshort_limit(self, price, label="sellshort limit"): self._add("limit", "sellshort", price, label)
    def sell_market(self, label="sell"): self._add("market", "sell", None, label)
    def sell_stop(self, price, label="sell stop"): self._add("stop", "sell", price, label)
    def sell_limit(self, price, label="sell limit"): self._add("limit", "sell", price, label)
    def buytocover_market(self, label="buytocover"): self._add("market", "buytocover", None, label)
    def buytocover_stop(self, price, label="buytocover stop"): self._add("stop", "buytocover", price, label)
    def buytocover_limit(self, price, label="buytocover limit"): self._add("limit", "buytocover", price, label)

    def exit_market(self, label="exit"):
        if self.mp > 0:
            self.sell_market(label)
        elif self.mp < 0:
            self.buytocover_market(label)

    def on_bar(self):
        raise NotImplementedError

    @classmethod
    def all_inputs(cls):
        p = dict(COMMON_INPUTS)
        p.update(cls.inputs)
        return p

    @classmethod
    def all_doc(cls):
        d = dict(COMMON_DOC)
        d.update(cls.doc)
        return d


def _valid(action, mp):
    if action == "buy":
        return mp != 1
    if action == "sellshort":
        return mp != -1
    if action == "sell":
        return mp > 0
    return mp < 0          # buytocover


def run(strategy_cls, params, minutes_data, costs, minutes=None, align="session",
        start_ts=None, end_ts=None, cache_key=None, collect_trades=True, symbol=None):
    """回傳 dict：trades（list）、daily（[(tday_ord, equity_pts, close)]）、bars_in_pos、n_bars"""
    strat = strategy_cls(params)
    minutes = int(minutes or strat.minutes)
    m = B.minute_frame(minutes_data, symbol)
    b = B.build(m, minutes, align, cache_key=cache_key, symbol=symbol)
    strat.b = b
    mo, mh, ml, mc, mts = m["o"], m["h"], m["l"], m["c"], m["ts"]
    wkx = int(strat.p.get("WKX", 15))
    if wkx > 0:
        wk_only = m["to_week_end"] < wkx * 60
    else:
        wk_only = np.zeros(len(m["ts"]), dtype=bool)
    dayx = int(strat.p.get("DAYX", 0))
    wk_min = wk_only | (m["to_day_end"] < dayx * 60) if dayx > 0 else wk_only

    side_cost = costs["spread"] / 2.0 + costs["slippage"] + costs.get("commission", 0.0)
    swap_l = costs.get("swap_long_pct", 0.0) / 100.0 / 360.0
    swap_s = costs.get("swap_short_pct", 0.0) / 100.0 / 360.0

    n = b.n
    start_i = 0 if start_ts is None else int(np.searchsorted(b.ts_start, start_ts))
    end_i = n if end_ts is None else int(np.searchsorted(b.ts_start, end_ts))
    warm = 5

    trades = []
    daily = []
    realized = 0.0
    cur = None                    # 目前部位 dict
    pending = []
    bars_in_pos = 0
    prev_tday = None

    def open_pos(direction, price, j, label):
        nonlocal cur
        strat.mp = direction
        price = float(price)
        strat.entryprice = price
        strat.maxprofit = 0.0
        strat.u = float(strat.u_now)
        strat.entries_today += 1
        cur = {"dir": direction, "entry": price, "entry_ts": int(mts[j]), "label": label,
               "swap": 0.0, "bars": 0, "entry_i": i, "entry_tday": int(m["tday"][j])}

    def close_pos(price, j, label):
        nonlocal cur, realized
        price = float(price)
        gross = (price - cur["entry"]) * cur["dir"]
        cost = 2 * side_cost
        net = gross - cost - cur["swap"]
        realized += net
        if collect_trades:
            trades.append({"dir": cur["dir"], "entry_ts": cur["entry_ts"], "exit_ts": int(mts[j]),
                           "entry_tday": cur["entry_tday"], "exit_tday": int(m["tday"][j]),
                           "entry": round(cur["entry"], 2), "exit": round(price, 2),
                           "gross": round(gross, 2), "cost": round(cost, 2), "swap": round(cur["swap"], 2),
                           "net": round(net, 2), "ret_pct": float(net / cur["entry"] * 100.0),
                           "mfe": round(strat.maxprofit, 2), "bars": cur["bars"],
                           "in": cur["label"], "out": label})
        else:
            trades.append((cur["entry_ts"], net, net / cur["entry"] * 100.0, cur["dir"], cur["entry_tday"]))
        strat.mp = 0
        strat.entryprice = None
        strat.maxprofit = 0.0
        cur = None

    def fill(o, price, j):
        a = o.action
        if a in ("buy", "sellshort"):
            d = 1 if a == "buy" else -1
            if strat.mp == -d:
                close_pos(price, j, o.label)
            open_pos(d, price, j, o.label)
        else:
            close_pos(price, j, o.label)

    for i in range(n):
        a, e = int(b.m0[i]), int(b.m1[i])
        strat.i = i
        td = b.tday[i]
        if td != prev_tday:
            if prev_tday is not None and cur is not None:
                nights = int(td - prev_tday)
                rate = swap_l if cur["dir"] > 0 else swap_s
                cur["swap"] += b.o[i] * rate * nights
            strat.entries_today = 0
            prev_tday = td

        trading = start_i <= i < end_i
        # 週末切點
        cut = e
        if wk_min[a:e].any():
            cut = a + int(np.argmax(wk_min[a:e]))
        if cut == a:
            pending = [o for o in pending if o.action in ("sell", "buytocover")]
        entry_j = None
        if pending and trading and cut > a:
            best = None
            for idx, o in enumerate(pending):
                if not _valid(o.action, strat.mp):
                    continue
                up = o.action in ("buy", "buytocover")
                if o.kind == "market":
                    j, px, rank = a, mo[a], 0
                else:
                    if (o.kind == "stop") == up:
                        hit = mh[a:cut] >= o.price
                    else:
                        hit = ml[a:cut] <= o.price
                    if not hit.any():
                        continue
                    j = a + int(np.argmax(hit))
                    if o.kind == "stop":
                        px = max(o.price, mo[j]) if up else min(o.price, mo[j])
                        rank = 1
                    else:
                        px = min(o.price, mo[j]) if up else max(o.price, mo[j])
                        rank = 2
                key = (j, rank, idx)
                if best is None or key < best[0]:
                    best = (key, o, px, j)
            if best is not None:
                _, o, px, j = best
                was = strat.mp
                fill(o, px, j)
                if strat.mp != 0 and strat.mp != was:
                    entry_j = j
        # 最大浮盈（進場那根只算成交之後的分鐘）
        if cur is not None:
            s0 = entry_j + 1 if entry_j is not None else a
            seg_end = cut
            if s0 < seg_end:
                if strat.mp > 0:
                    strat.maxprofit = max(strat.maxprofit, float(mh[s0:seg_end].max()) - strat.entryprice)
                else:
                    strat.maxprofit = max(strat.maxprofit, strat.entryprice - float(ml[s0:seg_end].min()))
            elif entry_j is not None:
                strat.maxprofit = max(strat.maxprofit, (mc[entry_j] - strat.entryprice) * strat.mp)
        # 週末強制出場
        if cut < e and cur is not None:
            close_pos(mo[cut], cut, "週末出場" if wk_only[cut] else "日內出場")
        if cur is not None:
            cur["bars"] += 1
            bars_in_pos += 1

        # K 棒收盤
        strat.u_now = strat.avgrange(strat.p["UNIT_N"]) if int(strat.p.get("UNIT_MODE", 0)) == 0 \
            else b.c[i] * 0.001
        strat.orders = []
        if i >= warm:
            strat.on_bar()
        pending = strat.orders if (trading and i + 1 < end_i) else []
        if cut < e:                       # 週末時段內不再掛進場單
            pending = [o for o in pending if o.action in ("sell", "buytocover")]
        strat.mp_prev = strat.mp

        # 每日權益（交易日最後一根 K）
        if trading and (i + 1 == n or b.tday[i + 1] != td):
            open_pnl = 0.0
            if cur is not None:
                open_pnl = (b.c[i] - cur["entry"]) * cur["dir"] - 2 * side_cost - cur["swap"]
            daily.append((int(td), realized + open_pnl, float(b.c[i])))
        if i + 1 == end_i and cur is not None:
            close_pos(b.c[i], e - 1, "回測結束")
            if daily:
                daily[-1] = (daily[-1][0], realized, daily[-1][2])

    return {"trades": trades, "daily": daily, "bars_in_pos": bars_in_pos,
            "n_bars": max(0, min(end_i, n) - start_i), "minutes": minutes,
            "first_close": float(b.c[start_i]) if start_i < n else None,
            "bar_ts": (int(b.ts_start[start_i]) if start_i < n else None, int(b.ts_end[min(end_i, n) - 1]) if n else None)}
