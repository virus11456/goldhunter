"""MRSPENCER v4.6B 量化版：黃金區間極值逆勢 + 梯子鋪單 + 加碼 / 攤平 / 配對救援 + 整籃停損與冷卻。

由 TradingView Pine Script（https://www.tradingview.com/script/ntJuSPyD-MRSPENCER-v4-6B/）逐行轉換：
- 原策略跑 Vantage XAUUSD 1 分 K、初始資金 1,500 USD、1 口 = 1 盎司
- 本平台只做 USDT 永續：用黃金代幣永續合約（PAXG / XAUT，1 顆 ≈ 1 盎司），所有「$」門檻都是價差，可直接沿用
- 參數名稱與 Pine 相同；「口」換算成幣：口數 × unit ×（依權益放大時：開籃時權益 ÷ base_capital）

與 Pine 的差異（引擎一次只送出一個決策）：
- 同一根 K 棒同時觸發加碼 / 攤平 / 配對時，數量合併成一筆下單（各自的上限檢查與 Pine 相同）
- 同一根 K 棒同時觸發出場與加碼時，只出場（Pine 會兩筆同時送出，實盤上沒有意義）
- 另外附一個「災難止損」（整籃停損再多 disaster_buffer 美元）交給引擎盤中監控，只在 Bot 漏掉 K 棒時才會用到
"""

from __future__ import annotations

from collections import OrderedDict

from goldhunter.core.models import Action, Decision
from goldhunter.strategies.base import Strategy, StrategyContext

TF_SEC = {"1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "2h": 7200, "4h": 14400}
W = [1.00, 0.80, 0.60, 0.45, 0.35, 0.30, 0.20, 0.20, 0.15, 0.10, 0.10, 0.05, 0.05]
TAIPEI_OFFSET_MIN = 8 * 60


def suffix_sum(k: int) -> float:
    return sum(W[13 - k:])


def next_rung(k: int) -> float:
    return W[13 - k - 1] if k < 13 else round(W[0] * 1.31 / 0.05) * 0.05


def _session_minutes(sess: str) -> tuple[int, int]:
    a, b = sess.split("-")
    return int(a[:2]) * 60 + int(a[2:]), int(b[:2]) * 60 + int(b[2:])


class MrSpencerStrategy(Strategy):
    name = "mrspencer"
    description = ("MRSPENCER v4.6B 黃金量化版：1 分 K 區間極值逆勢鋪單，加碼 / 幾何攤平 / 配對救援，"
                   "整籃停損 $25、停損後冷卻 240 根。建議標的 PAXG 或 XAUT 永續")
    default_params = {
        # ① 梯子
        "k": 8, "addonOn": True, "addonAdv": 2.0,
        # ② 進場
        "rangeMin": 240, "hiTh": 0.90, "loTh": 0.10, "useSess": True, "sess": "1600-2300",
        "clockGate": False, "clockPer": 60, "clockWin": 15,
        "useFilt": True, "momTh": 15.0, "dwellMin": 20,
        # ③ 出場與攤平
        "tgtUsd": 2.3, "rescueOn": True, "rescueStep": 5.0, "maxRescue": 3, "tgtRescue": 4.5, "rescueGeo": 1.3,
        # ④ 風控外掛
        "stopUsd": 25.0, "ddStopPct": 0.0, "lotCap": 9.0, "maxHoldMin": 0, "coolBars": 240, "tpCoolBars": 0,
        # ④.5 配對救援
        "relayOn": True, "trapDD": 8.0, "relayScl": 1.0, "maxRelay": 2, "relayAltHi": 0.92, "relayAltLo": 0.08,
        "trapCutMin": 150,
        # 平台換算
        "unit": 1.0,  # 1 口 = 幾顆幣（PAXG / XAUT 1 顆 ≈ 1 盎司）
        "scale_with_equity": True, "base_capital": 1500.0,  # 依權益等比例放大口數（原策略以 1,500 USD 設計）
        "leverage": 25,  # 原策略保證金 2%（50 倍）；滿 9 口約需 22 倍，實際仍受風控最大槓桿限制
        "mintick": 0.01, "disaster_buffer": 10.0,
    }
    # 這個策略需要同方向加碼、較高槓桿與曝險；建立 Bot / 回測時預設套用（使用者改過的值優先）
    recommended_risk = {"allow_pyramiding": True, "max_leverage": 25, "max_position_pct": 100.0,
                        "max_total_exposure_pct": 2500.0, "daily_loss_limit_pct": 0.0,
                        "max_orders_per_hour": 60, "default_stop_loss_pct": 5.0}
    paper_first = True
    warmup = 320  # 1 分 K：240 分鐘區間 + 60 分鐘滯留視窗；Bot 會依此多抓 K 線

    def __init__(self, params=None, ai=None):
        super().__init__(params, ai)
        self.addon_done = False
        self.rescue_cnt = 0
        self.relay_cnt = 0
        self.ladder_tot: float | None = None
        self.entry_bar: int | None = None
        self.cool_til = 0
        self.peak_eq: float | None = None
        self.mult = 1.0  # 本籃的「口 → 幣」倍率（開籃時固定）
        self.was_in = False
        self._pos_r: OrderedDict[int, float] = OrderedDict()

    # ---------------- 工具 ----------------
    def _bars(self, minutes: float, tf_sec: int) -> int:
        return max(2, round(minutes * 60 / tf_sec))

    def _pos_r_at(self, candles, i: int, n: int) -> float:
        ts = candles[i].ts
        v = self._pos_r.get(ts)
        if v is None:
            seg = candles[max(0, i - n + 1): i + 1]
            hi, lo = max(c.high for c in seg), min(c.low for c in seg)
            v = (candles[i].close - lo) / max(hi - lo, self.p("mintick"))
            self._pos_r[ts] = v
            if len(self._pos_r) > 2000:
                self._pos_r.popitem(last=False)
        return v

    def _decision(self, ctx: StrategyContext, action: Action, contracts: float, reasoning: str,
                  stop: float | None = None) -> Decision:
        return Decision(instrument=ctx.instrument, action=action, quantity=round(contracts * self.p("unit") * self.mult, 8),
                        leverage=int(self.p("leverage")), stop_loss=stop, reasoning=reasoning)

    # ---------------- 主邏輯 ----------------
    def on_bar(self, ctx: StrategyContext):  # noqa: C901 - 與 Pine 原稿結構一致，方便對照
        p = self.p
        tf_sec = TF_SEC.get(ctx.timeframe, 60)
        candles = ctx.candles
        n = len(candles)
        range_len = self._bars(p("rangeMin"), tf_sec)
        mom_len = self._bars(30, tf_sec)
        dwell_win = self._bars(60, tf_sec)
        dwell_need = max(1, round(p("dwellMin") * 60 / tf_sec))
        if n < range_len + dwell_win + 1 or n <= mom_len:
            return None
        bar = candles[-1]
        bar_index = bar.ts // (tf_sec * 1000)
        close = bar.close
        eq = ctx.balance.total

        # ---- 訊號 ----
        pos_r = self._pos_r_at(candles, n - 1, range_len)
        tpe = (bar.ts // 60_000 + TAIPEI_OFFSET_MIN) % 1440  # 台北時間（分鐘）
        s0, s1 = _session_minutes(p("sess"))
        in_sess = not p("useSess") or (s0 <= tpe < s1 if s0 <= s1 else (tpe >= s0 or tpe < s1))
        clock_ok = not p("clockGate") or (tpe % p("clockPer")) < p("clockWin")
        mom30 = close - candles[-1 - mom_len].close
        recent = [self._pos_r_at(candles, i, range_len) for i in range(n - dwell_win, n)]
        dwell_top = sum(1 for v in recent if v >= p("hiTh"))
        dwell_bot = sum(1 for v in recent if v <= p("loTh"))
        ok_long = not p("useFilt") or (mom30 <= -p("momTh") and dwell_bot >= dwell_need)
        ok_short = not p("useFilt") or (mom30 >= p("momTh") and dwell_top >= dwell_need)
        long_sig = pos_r <= p("loTh") and in_sess and clock_ok and ok_long
        short_sig = pos_r >= p("hiTh") and in_sess and clock_ok and ok_short

        # ---- 狀態 ----
        self.peak_eq = eq if self.peak_eq is None else max(self.peak_eq, eq)
        dd_pct = (self.peak_eq - eq) / self.peak_eq * 100 if self.peak_eq > 0 else 0.0
        size = ctx.position_size
        flat = size == 0
        if flat and self.was_in:  # 上一籃已平倉 → 重置
            self.addon_done, self.rescue_cnt, self.relay_cnt = False, 0, 0
            self.ladder_tot, self.entry_bar = None, None
        self.was_in = not flat

        # ---- 進場 ----
        if flat:
            if bar_index < self.cool_til or not (long_sig or short_sig):
                return None
            self.mult = max(eq / p("base_capital"), 0.0) if p("scale_with_equity") else 1.0
            qty = suffix_sum(p("k"))
            self.ladder_tot, self.entry_bar = qty, bar_index
            self.addon_done, self.rescue_cnt, self.relay_cnt = False, 0, 0
            long = bool(long_sig)
            stop = close - (p("stopUsd") + p("disaster_buffer")) if long else close + (p("stopUsd") + p("disaster_buffer"))
            why = (f"{'開多' if long else '開空'}鋪單：區間位置 {pos_r:.2f}（{p('rangeMin')} 分鐘區間），"
                   f"30 分位移 {mom30:+.2f}，極值滯留 {dwell_bot if long else dwell_top} 根")
            return self._decision(ctx, Action.OPEN_LONG if long else Action.OPEN_SHORT, qty, why,
                                  stop if p("stopUsd") > 0 else None)

        # ---- 持倉管理 ----
        if self.ladder_tot is None:  # 外部開的倉（例如重啟後），用目前部位當作梯子總量
            self.mult = max(eq / p("base_capital"), 1e-9) if p("scale_with_equity") else 1.0
            self.ladder_tot = abs(size) / (p("unit") * self.mult)
            self.entry_bar = bar_index
        dir_sgn = 1 if size > 0 else -1
        avg = ctx.position_avg_price or close
        profit = (close - avg) * dir_sgn
        adverse = -profit
        pos_abs = abs(size) / (p("unit") * self.mult)  # 換回「口」

        # 出場（整組全平）
        tgt = p("tgtRescue") if (self.rescue_cnt > 0 or self.relay_cnt > 0) else p("tgtUsd")
        side = "平多" if dir_sgn > 0 else "平空"
        exit_why, cool = None, None
        if profit >= tgt:
            tag = "配對TP+" if self.relay_cnt > 0 else ("清算+" if self.rescue_cnt > 0 else "TP+")
            exit_why = f"{side}{tag}{tgt:g}：合併均價 {avg:.2f}，獲利 {profit:.2f}"
            if p("tpCoolBars") > 0:
                cool = bar_index + p("tpCoolBars")
        trap_cut = self._bars(p("trapCutMin"), tf_sec) if p("trapCutMin") > 0 else 0
        if (p("relayOn") and trap_cut and adverse >= p("trapDD") and self.entry_bar is not None
                and bar_index - self.entry_bar >= trap_cut):
            exit_why, cool = f"{side}配對逾時砍：被套 {adverse:.2f} 已 {bar_index - self.entry_bar} 根", bar_index + p("coolBars")
        if p("stopUsd") > 0 and adverse >= p("stopUsd"):
            exit_why, cool = f"籃停損：逆行 {adverse:.2f} ≥ {p('stopUsd'):g}", bar_index + p("coolBars")
        if p("ddStopPct") > 0 and dd_pct >= p("ddStopPct"):
            exit_why, cool = f"DD停損：權益回撤 {dd_pct:.1f}%", bar_index + p("coolBars")
        hold = self._bars(p("maxHoldMin"), tf_sec) if p("maxHoldMin") > 0 else 0
        if hold and self.entry_bar is not None and bar_index - self.entry_bar >= hold:
            exit_why, cool = "逾時強平", bar_index + p("coolBars")
        if exit_why:
            if cool is not None:
                self.cool_til = cool
            return Decision(instrument=ctx.instrument, action=Action.CLOSE, reasoning=exit_why)

        # 加碼 / 攤平 / 配對（各自檢查總口數上限，與 Pine 相同）
        add, notes = 0.0, []
        k = p("k")
        if p("addonOn") and not self.addon_done and adverse >= p("addonAdv") and pos_abs + next_rung(k) <= p("lotCap"):
            add += next_rung(k)
            self.addon_done = True
            notes.append(f"爬梯加碼 {next_rung(k):g} 口")
        r_qty = self.ladder_tot * p("rescueGeo") ** self.rescue_cnt
        if (p("rescueOn") and self.rescue_cnt < p("maxRescue") and adverse >= p("rescueStep") * (self.rescue_cnt + 1)
                and pos_abs + r_qty <= p("lotCap")):
            add += r_qty
            self.rescue_cnt += 1
            notes.append(f"攤平{self.rescue_cnt} {r_qty:.2f} 口")
        relay_sig = long_sig if dir_sgn > 0 else short_sig
        relay_alt = pos_r <= p("relayAltLo") if dir_sgn > 0 else pos_r >= p("relayAltHi")
        relay_qty = max(0.05, pos_abs * p("relayScl"))
        if (p("relayOn") and self.relay_cnt < p("maxRelay") and adverse >= p("trapDD") and (relay_sig or relay_alt)
                and pos_abs + relay_qty <= p("lotCap")):
            add += relay_qty
            self.relay_cnt += 1
            notes.append(f"配對救援{self.relay_cnt} {relay_qty:.2f} 口")
        if add <= 0:
            return None
        new_avg = (pos_abs * avg + add * close) / (pos_abs + add)
        buf = p("stopUsd") + p("disaster_buffer")
        stop = (new_avg - buf if dir_sgn > 0 else new_avg + buf) if p("stopUsd") > 0 else None
        why = "、".join(notes) + f"：逆行 {adverse:.2f}，目前 {pos_abs:.2f} 口"
        return self._decision(ctx, Action.OPEN_LONG if dir_sgn > 0 else Action.OPEN_SHORT, add, why, stop)
