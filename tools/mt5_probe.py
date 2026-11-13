"""MT5 券商資料探測 + 匯出（在 Windows 上跑，需要開著該券商的 MT5 終端機）

用途：
  1. 查這家券商的美股指數 CFD 合約規格：點差、合約大小、最小手數、隔夜利息
  2. 測 1 分 K 歷史能回溯多遠
  3. 匯出 1 分 K CSV（時間換成 UTC），到 us-index-lab「資料」頁匯入

安裝（一次就好）：
  pip install MetaTrader5 pandas

使用：
  python mt5_probe.py --terminal "C:\\Program Files\\Pepperstone MetaTrader 5\\terminal64.exe" --broker pepperstone
  三家各跑一次（每家 --terminal 指到各自的 terminal64.exe）。不加 --terminal 會用目前開著的那個。

跑之前請在 MT5：工具 → 選項 → 圖表 →「圖表中最大K線數」設成最大（Unlimited），重開終端機，
否則撈得到的歷史會被這個設定截斷。

伺服器時間：多數券商用「紐約收盤」時區（冬令 GMT+2、夏令 GMT+3，即美東 +7 小時），
腳本會自動判斷；判斷不出來就用目前偏移量當固定時差，也可以用 --server-tz 指定。
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

try:
    import MetaTrader5 as mt5
except ImportError:
    sys.exit("缺少 MetaTrader5 套件：pip install MetaTrader5 pandas（只支援 Windows）")
import pandas as pd

CANDIDATES = {
    "US500": ["US500", "US500.cash", "US500Cash", "SPX500", "SP500", "US500m", "US500.a", "SPX500.r", "US500-F", "USA500"],
    "NAS100": ["NAS100", "USTEC", "US100", "NAS100.cash", "US100.cash", "USTECH", "NDX100", "NAS100m", "USTEC.a", "NAS100.r", "USTEC-F"],
    "JP225": ["JP225", "JPN225", "JP225.cash", "JP225Cash", "NIKKEI", "N225", "J225", "JP225m", "JPN225.a", "JP225-F"],
    "HK50": ["HK50", "HK50.cash", "HK50Cash", "HSI", "HK50m", "HKG50", "HK50.a", "HK50-F", "HK33"],
}
SWAP_MODES = {0: "停用", 1: "點數", 2: "基礎貨幣", 3: "保證金貨幣", 4: "存款貨幣", 5: "年化利率（目前價）",
              6: "年化利率（開倉價）", 7: "重開倉（收盤價）", 8: "重開倉（買價）"}


def find_symbol(key, override=None):
    if override:
        return override if mt5.symbol_info(override) else None
    names = {s.name for s in (mt5.symbols_get() or [])}
    for c in CANDIDATES[key]:
        if c in names:
            return c
    # 模糊比對
    for n in sorted(names):
        u = n.upper()
        if key == "US500" and ("500" in u and ("US" in u or "SP" in u)):
            return n
        if key == "NAS100" and (("NAS" in u or "USTEC" in u or "US100" in u or "NDX" in u)):
            return n
        if key == "JP225" and ("225" in u or "NIKKEI" in u):
            return n
        if key == "HK50" and (u.startswith("HK") or "HSI" in u or "HANG" in u):
            return n
    return None


def wait_tick(symbol, seconds=15):
    """等到有報價（剛選入市場報價時要幾秒才會有）；等不到回 None"""
    t0 = time.time()
    while time.time() - t0 < seconds:
        tick = mt5.symbol_info_tick(symbol)
        if tick and tick.time > 0 and (tick.bid > 0 or tick.ask > 0):
            return tick
        time.sleep(0.5)
    return None


def server_offset_hours(symbol):
    tick = wait_tick(symbol, 5)
    if not tick:
        return None
    off = round((tick.time - time.time()) / 3600)
    return off if -14 <= off <= 14 else None          # 報價太舊（休市）就不採用


def make_converter(mode, symbol):
    """回傳 function(server_epoch_seconds_ndarray) -> UTC epoch seconds"""
    import numpy as np
    ny = "America/New_York"
    if mode == "auto":
        off = server_offset_hours(symbol)
        et_off = int(pd.Timestamp.now(tz=ny).utcoffset().total_seconds() // 3600)
        if off is not None and off == et_off + 7:
            mode = "nyclose"
        elif off is not None:
            mode = f"fixed:{off:+d}"
        else:
            mode = "nyclose"
        if off is None:
            print(f"  伺服器時差判斷：沒有即時報價可比對，採用多數券商的紐約收盤時區（美東 +7 小時）")
        else:
            print(f"  伺服器時差判斷：目前 GMT{off:+d}（美東 GMT{et_off:+d}）→ 使用 {mode}")
    if mode == "nyclose":
        def conv(t):
            wall = pd.to_datetime(t, unit="s") - pd.Timedelta(hours=7)          # 伺服器牆鐘 → 美東牆鐘
            loc = wall.tz_localize(ny, ambiguous="NaT", nonexistent="shift_forward")
            return loc.tz_convert("UTC").as_unit("s").asi8
        return conv, mode
    off = int(mode.split(":")[1])
    return (lambda t: (t - off * 3600)), mode


def probe_history(symbol, max_years):
    """以月為單位往回撈，連續兩個月沒資料就停。回傳 DataFrame（伺服器時間）。"""
    now = datetime.now(timezone.utc) + timedelta(days=1)
    frames, empty = [], 0
    cur_end = now
    for k in range(int(max_years * 12) + 1):
        cur_start = cur_end - timedelta(days=31)
        print(f"\r  往回撈 1 分 K：第 {k + 1} 個月（{cur_start:%Y-%m}）...  ", end="", flush=True)
        r = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M1, cur_start, cur_end)
        if r is None or len(r) == 0:
            empty += 1
            if empty >= 2:
                break
        else:
            empty = 0
            frames.append(pd.DataFrame(r))
        cur_end = cur_start
    print()
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames).drop_duplicates("time").sort_values("time")
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--terminal", default=None, help="terminal64.exe 路徑（不填用目前開著的）")
    ap.add_argument("--broker", required=True, help="券商名稱，用在輸出檔名，例如 pepperstone / vantage / topone")
    ap.add_argument("--years", type=float, default=4, help="最多往回撈幾年（預設 4）")
    ap.add_argument("--us500", default=None, help="手動指定標普商品代碼")
    ap.add_argument("--nas100", default=None, help="手動指定那指商品代碼")
    ap.add_argument("--jp225", default=None, help="手動指定日經商品代碼")
    ap.add_argument("--hk50", default=None, help="手動指定恆指商品代碼")
    ap.add_argument("--server-tz", default="auto", help="auto / nyclose / fixed:+2 之類")
    ap.add_argument("--out", default=".", help="輸出資料夾")
    ap.add_argument("--force", action="store_true", help="已有報告也重跑")
    a = ap.parse_args()

    ok = mt5.initialize(path=a.terminal, timeout=90000) if a.terminal else mt5.initialize(timeout=90000)
    if not ok:
        sys.exit(f"MT5 初始化失敗：{mt5.last_error()}（確認終端機已登入、路徑正確）")
    ti, ai = mt5.terminal_info(), mt5.account_info()
    print(f"終端機：{ti.company}／{ti.name}　帳號：{ai.login if ai else '?'}（{ai.server if ai else '?'}）")
    print(f"圖表最大 K 線數 maxbars = {ti.maxbars:,}" + ("　⚠ 偏小，歷史可能被截斷，建議改成最大後重開" if ti.maxbars < 2_000_000 else ""))
    os.makedirs(a.out, exist_ok=True)
    if ai:
        a.broker = f"{a.broker}-{ai.login}"
    if not a.force and os.path.exists(os.path.join(a.out, f"mt5_{a.broker}_report.json")):
        print(f"帳號 {ai.login if ai else '?'} 已經有報告，跳過（要重跑請刪掉 results 裡對應的檔案）")
        mt5.shutdown()
        return
    if not ti.connected:
        print("⚠ 終端機目前沒有連上券商伺服器：請確認 MT5 右下角不是「無連線」（需要開著 VPN），再重跑一次")
        mt5.shutdown()
        return
    report = {"broker": a.broker, "company": ti.company, "server": ai.server if ai else None,
              "maxbars": ti.maxbars, "probed_at": datetime.now(timezone.utc).isoformat()[:19], "symbols": {}}

    for key, override in (("US500", a.us500), ("NAS100", a.nas100), ("JP225", a.jp225), ("HK50", a.hk50)):
        sym = find_symbol(key, override)
        print(f"\n[{key}] 商品代碼：{sym or '找不到（用 --us500 / --nas100 / --jp225 / --hk50 指定）'}")
        if not sym:
            continue
        mt5.symbol_select(sym, True)
        tick = wait_tick(sym, 15)
        if not tick:
            print("  ⚠ 15 秒內沒有收到報價（可能休市或沒連線），點差欄位會是 0，其餘照跑")
        si = mt5.symbol_info(sym)
        spec = {
            "symbol": sym, "digits": si.digits, "point": si.point,
            "spread_now_points": si.spread, "spread_now_price": round(si.spread * si.point, 5),
            "bid": tick.bid if tick else None, "ask": tick.ask if tick else None,
            "contract_size": si.trade_contract_size, "tick_value": si.trade_tick_value, "tick_size": si.trade_tick_size,
            "volume_min": si.volume_min, "volume_step": si.volume_step, "volume_max": si.volume_max,
            "swap_mode": SWAP_MODES.get(si.swap_mode, si.swap_mode), "swap_long": si.swap_long, "swap_short": si.swap_short,
            "swap_rollover3days": si.swap_rollover3days, "margin_initial": si.margin_initial,
            "currency_profit": si.currency_profit, "description": si.description,
        }
        usd_per_point_per_lot = si.trade_tick_value / si.trade_tick_size if si.trade_tick_size else None
        spec["usd_per_point_per_lot"] = usd_per_point_per_lot
        for k, v in spec.items():
            print(f"  {k:24s} {v}")
        conv, mode = make_converter(a.server_tz, sym)
        print("  向伺服器索取 1 分 K 歷史，第一次每個月可能要等幾十秒 ...")
        df = probe_history(sym, a.years)
        if df.empty:
            print("  沒有撈到任何 1 分 K")
            report["symbols"][key] = {"spec": spec, "m1_rows": 0}
            continue
        utc = conv(df["time"].to_numpy())
        out = pd.DataFrame({
            "time_utc": pd.to_datetime(utc, unit="s", utc=True).strftime("%Y-%m-%d %H:%M:%S"),
            "open": df["open"].values, "high": df["high"].values, "low": df["low"].values, "close": df["close"].values,
            "tick_volume": df["tick_volume"].values, "spread_price": (df["spread"].values * si.point).round(5),
        }).dropna()
        first, last = out["time_utc"].iloc[0], out["time_utc"].iloc[-1]
        yrs = (pd.Timestamp(last) - pd.Timestamp(first)).days / 365.25
        print(f"  1 分 K：{len(out):,} 根，{first} ～ {last}（UTC），約 {yrs:.2f} 年")
        print(f"  點差中位數：{out['spread_price'].median()}（價格單位）")
        fn = os.path.join(a.out, f"mt5_{a.broker}_{key}_M1.csv")
        out.to_csv(fn, index=False)
        print(f"  已匯出 {fn}")
        report["symbols"][key] = {"spec": spec, "m1_rows": len(out), "m1_first_utc": first, "m1_last_utc": last,
                                  "m1_years": round(yrs, 2), "spread_median": float(out["spread_price"].median()),
                                  "time_mode": mode, "csv": fn}

    rp = os.path.join(a.out, f"mt5_{a.broker}_report.json")
    with open(rp, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1, default=str)
    print(f"\n報告：{rp}（三家都跑完，把報告 json 貼給 Claude 一起比較）")
    mt5.shutdown()


if __name__ == "__main__":
    main()
