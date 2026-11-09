"""商品設定：交易時段、資料代碼、成本預設值。

美股指數 CFD 的時間一律用美東時間（America/New_York）處理，夏令時間自動換算：
  交易日切換   18:00（期貨慣例：週日 18:00 開盤的那段屬於週一）
  每日休市     17:00–18:00
  正規盤 RTH   09:30–16:00
  延長盤 ETH   16:00–翌日 09:30（跨過每日休市，類比台指夜盤）
成本都是「指數點」：spread 是買賣價差（整筆來回付一次），slippage 每邊各算一次。
隔夜利息用年化 % 計（正數 = 成本），每跨一個交易日收一次，週末三天。
實際數字請用 tools/mt5_probe.py 從你要下單的券商撈出來校準。
"""

TZ = "America/New_York"
DAY_ROLL_HHMM = 1800        # 交易日切換點
RTH_START, RTH_END = 930, 1600

INSTRUMENTS = {
    "US500": {
        "name": "標普 500",
        "duka": "USA500IDXUSD",
        "price_range": (1000, 20000),      # 合理價格區間，用來自動判斷 Dukascopy 的價格倍率
        "spread": 0.5,
        "slippage": 0.2,
        "commission": 0.0,
        "swap_long_pct": 6.5,
        "swap_short_pct": 0.0,
        "usd_per_point": 1.0,              # 1 手每點幾美元，各券商合約規格不同，僅供換算金額
    },
    "NAS100": {
        "name": "那斯達克 100",
        "duka": "USATECHIDXUSD",
        "price_range": (3000, 80000),
        "spread": 1.5,
        "slippage": 0.5,
        "commission": 0.0,
        "swap_long_pct": 6.5,
        "swap_short_pct": 0.0,
        "usd_per_point": 1.0,
    },
}


def cost_defaults(symbol):
    i = INSTRUMENTS[symbol]
    return {k: i[k] for k in ("spread", "slippage", "commission", "swap_long_pct", "swap_short_pct", "usd_per_point")}
