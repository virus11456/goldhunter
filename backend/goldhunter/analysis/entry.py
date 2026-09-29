"""進場分析：現在進場的盈虧比、建議等待的進場點位、等得到的機率與期望值。

做法（全部用歷史資料模擬，不靠猜）：
1. 候選進場價：現價、EMA20 回檔、回測前高 / 前低（突破點）、0.5 ATR 回檔
2. 止損 / 目標：優先用策略（或 AI）給的；沒有就用 ATR 與前高 / 前低自動算
3. 對每個候選，以「ATR 倍數」為單位回放歷史：
   - 等得到的機率：訊號後 N 根 K 棒內，價格有沒有回到這個點位
   - 勝率：成交後先碰到目標還是先碰到止損
4. 期望值（以 R 計，1R＝止損距離）＝勝率 × 盈虧比 −（1 − 勝率）
   每次訊號的期望值＝等得到的機率 × 期望值（等不到＝錯過，這筆為 0）
5. 建議：每次訊號期望值最高的進場方式；差異很小或高頻週期時直接建議市價
"""

from __future__ import annotations

from pydantic import BaseModel

from goldhunter.core.models import Candle
from goldhunter.strategies import ta

HF_TIMEFRAMES = {"1m", "3m", "5m"}
WAIT_BARS = {"1m": 30, "3m": 20, "5m": 24, "15m": 16, "30m": 12, "1h": 12, "2h": 8, "4h": 6, "6h": 6, "8h": 5,
             "12h": 4, "1d": 3, "1w": 2}
HORIZON = 120  # 成交後最多觀察幾根 K 棒看先碰到目標或止損
MIN_EDGE_R = 0.05  # 等待要比市價多賺至少 0.05R 才建議等待


class EntryCandidate(BaseModel):
    key: str
    label: str
    price: float
    rr: float  # 盈虧比（目標距離 ÷ 止損距離）
    fill_prob: float  # 等得到的機率（市價＝1）
    win_prob: float | None  # 成交後先碰到目標的機率
    ev_r: float | None  # 成交後每筆期望值（R）
    ev_per_signal: float | None  # 考慮等不到：每次訊號的期望值（R）
    samples: int = 0


class EntryAnalysis(BaseModel):
    direction: str  # long / short
    price: float
    atr: float
    stop: float
    target: float
    stop_source: str
    target_source: str
    candidates: list[EntryCandidate]
    recommended: str  # 候選 key；skip＝建議不要進場
    recommendation: str  # 一句話建議
    wait_bars: int
    signal_samples: int  # 用來統計的歷史訊號數
    sample_basis: str  # 統計依據說明
    high_frequency: bool


def _swing(values: list[float], lookback: int, fn) -> float | None:
    window = values[-lookback - 1: -1] if len(values) > lookback else values[:-1]
    return fn(window) if window else None


def _simulate(candles: list[Candle], atr: list[float | None], signal_idx: list[int], long: bool,
              entry_off: float, stop_off: float, target_off: float, wait: int) -> tuple[float, float | None, int]:
    """entry_off / stop_off / target_off：以訊號當根收盤價為基準的 ATR 倍數（多單：進場在下方為正）

    回傳（等得到的機率, 成交後勝率, 成交樣本數）
    """
    n = len(candles)
    filled = wins = decided = 0
    usable = 0
    for i in range(len(signal_idx)):
        s = signal_idx[i]
        a = atr[s]
        if a is None or a <= 0 or s + 1 >= n:
            continue
        usable += 1
        c = candles[s].close
        entry = c - entry_off * a if long else c + entry_off * a
        stop = c - stop_off * a if long else c + stop_off * a
        target = c + target_off * a if long else c - target_off * a
        fill_at = None
        if entry_off <= 0:
            fill_at = s + 1
        else:
            for j in range(s + 1, min(n, s + 1 + wait)):
                if (long and candles[j].low <= entry) or (not long and candles[j].high >= entry):
                    fill_at = j
                    break
        if fill_at is None:
            continue
        filled += 1
        for j in range(fill_at, min(n, fill_at + HORIZON)):
            hit_stop = candles[j].low <= stop if long else candles[j].high >= stop
            hit_tgt = candles[j].high >= target if long else candles[j].low <= target
            if hit_stop:  # 同一根都碰到時保守算止損
                decided += 1
                break
            if hit_tgt:
                decided += 1
                wins += 1
                break
    if usable == 0:
        return 0.0, None, 0
    return filled / usable, (wins / decided if decided >= 5 else None), decided


def analyze_entry(candles: list[Candle], timeframe: str, direction: str, stop: float | None = None,
                  target: float | None = None, signal_idx: list[int] | None = None) -> EntryAnalysis:
    """candles：到最新一根已收盤 K 棒為止；signal_idx：歷史上同方向訊號出現的 K 棒索引（沒有就用全部 K 棒）"""
    if len(candles) < 60:
        raise ValueError("K 線不足，至少需要 60 根")
    long = direction == "long"
    close = [c.close for c in candles]
    highs = [c.high for c in candles]
    lows = [c.low for c in candles]
    atr = ta.atr(highs, lows, close, 14)
    a = atr[-1] or (max(highs[-14:]) - min(lows[-14:])) / 14
    price = close[-1]

    stop_source = target_source = "策略 / AI"
    if stop is None or (long and stop >= price) or (not long and stop <= price):
        stop = price - 2 * a if long else price + 2 * a
        stop_source = "2 倍 ATR"
    if target is None or (long and target <= price) or (not long and target >= price):
        swing = _swing(highs, 50, max) if long else _swing(lows, 50, min)
        dist = abs(swing - price) if swing is not None else 0
        if swing is not None and dist >= a and ((long and swing > price) or (not long and swing < price)):
            target, target_source = swing, "近 50 根前高" if long else "近 50 根前低"
        else:
            target, target_source = (price + 3 * a if long else price - 3 * a), "3 倍 ATR"

    # 候選進場價（多單在現價下方、空單在上方，且不能越過止損）
    ema20 = ta.ema(close, 20)[-1]
    breakout = _swing(highs, 20, max) if long else _swing(lows, 20, min)
    raw = [("market", "現在市價進場", price)]
    if ema20:
        raw.append(("ema20", "等回檔到 EMA20", ema20))
    if breakout:
        raw.append(("retest", "等回測前高（突破點）" if long else "等回測前低（跌破點）", breakout))
    raw.append(("atr", "等回檔 0.5 ATR", price - 0.5 * a if long else price + 0.5 * a))
    cands_raw = []
    for key, label, px in raw:
        better = px < price if long else px > price
        if key != "market" and (not better or (long and px <= stop) or (not long and px >= stop)):
            continue
        if any(abs(px - p) < 0.15 * a for _, _, p in cands_raw):
            continue
        cands_raw.append((key, label, px))

    wait = WAIT_BARS.get(timeframe, 12)
    hist_end = len(candles) - 1  # 最新一根不能拿來統計（還不知道結果）
    if signal_idx:
        sig = [i for i in signal_idx if 20 <= i < hist_end - 1]
        basis = f"歷史上同方向訊號 {len(sig)} 次"
    else:
        sig = []
    if len(sig) < 15:
        sig = list(range(20, hist_end - 1))
        basis = f"近 {len(sig)} 根 K 棒（此策略歷史訊號不足或為 AI 判斷，改用全部 K 棒統計）"

    stop_off = abs(price - stop) / a
    target_off = abs(target - price) / a
    candidates = []
    for key, label, px in cands_raw:
        risk = abs(px - stop)
        rr = abs(target - px) / risk if risk > 0 else 0.0
        entry_off = (price - px) / a if long else (px - price) / a
        fill, win, n = _simulate(candles[:hist_end], atr[:hist_end], sig, long, entry_off, stop_off, target_off, wait)
        ev = win * rr - (1 - win) if win is not None else None
        candidates.append(EntryCandidate(
            key=key, label=label, price=round(px, 8), rr=round(rr, 2), fill_prob=round(fill if key != "market" else 1.0, 3),
            win_prob=round(win, 3) if win is not None else None, ev_r=round(ev, 3) if ev is not None else None,
            ev_per_signal=round(ev * (fill if key != "market" else 1.0), 3) if ev is not None else None, samples=n))

    hf = timeframe in HF_TIMEFRAMES
    market = candidates[0]
    best = market
    for c in candidates[1:]:
        if c.ev_per_signal is not None and (best.ev_per_signal is None or c.ev_per_signal > best.ev_per_signal):
            best = c
    if hf or best.key == "market" or best.ev_per_signal is None or market.ev_per_signal is None \
            or best.ev_per_signal - market.ev_per_signal < MIN_EDGE_R:
        rec = "market"
        why = ("高頻週期，等待與市價的差異很小，直接市價進場" if hf else
               "等待沒有明顯更划算（或樣本不足），建議直接市價進場")
    else:
        rec = best.key
        why = (f"建議掛單在 {best.price:.6g} 等待：盈虧比 1:{best.rr}，約 {best.fill_prob:.0%} 機率等得到，"
               f"每次訊號期望值 {best.ev_per_signal:+.2f}R（市價 {market.ev_per_signal:+.2f}R）；{wait} 根 K 棒內沒成交就取消")
    if market.ev_r is not None and market.ev_r < 0 and (best.ev_per_signal or 0) <= 0:
        rec = "skip"
        why = "所有進場方式的歷史期望值都是負的，建議這次不要進場"
    return EntryAnalysis(
        direction=direction, price=price, atr=round(a, 8), stop=round(stop, 8), target=round(target, 8),
        stop_source=stop_source, target_source=target_source, candidates=candidates, recommended=rec,
        recommendation=why, wait_bars=wait, signal_samples=len(sig), sample_basis=basis, high_frequency=hf,
    )


async def strategy_signal_indices(strategy, instrument, timeframe: str, candles: list[Candle], direction: str) -> list[int]:
    """回放策略，找出歷史上同方向進場訊號出現在哪些 K 棒（AI 策略不回放，避免產生費用）"""
    if getattr(strategy, "uses_ai", False):
        return []
    from goldhunter.backtest.engine import BacktestConfig, run_backtest
    from goldhunter.risk.manager import RiskConfig

    res = await run_backtest(strategy, instrument, timeframe, candles,
                             BacktestConfig(risk=RiskConfig(daily_loss_limit_pct=0, max_orders_per_hour=100_000,
                                                            allow_pyramiding=False)))
    idx = {c.ts: i for i, c in enumerate(candles)}
    side = "buy" if direction == "long" else "sell"
    # 回測在訊號的下一根開盤成交，所以訊號 K 棒＝成交 K 棒的前一根
    return sorted({idx[t.ts] - 1 for t in res.trades if not t.reduce_only and t.side == side and t.ts in idx})
