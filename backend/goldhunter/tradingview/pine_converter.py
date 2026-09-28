"""TradingView Pine Script → GoldHunter Python 策略（AI 輔助轉換）。

流程：
1. 把 Pine Script 連同本系統的策略 API 說明交給 AI 模型，要求輸出 UserStrategy 類別
2. 以 AST 白名單做安全檢查（見 strategies.custom）
3. 若檢查失敗，把錯誤訊息回饋給模型重試（最多 max_attempts 次）
4. 結果存成「待審核」策略；使用者檢視程式碼並跑過回測後才可啟用
"""

from __future__ import annotations

import re

from pydantic import BaseModel

from goldhunter.ai.base import AIProvider
from goldhunter.strategies.custom import StrategyCodeError, load_strategy_class, validate_code

CONVERTER_SYSTEM = '''你是 TradingView Pine Script 與 Python 的專家。把使用者的 Pine Script 策略或指標
轉換成 GoldHunter 的 Python 策略。只輸出一個 ```python 程式碼區塊，不要其他文字。

## 目標 API
```python
from goldhunter.strategies.sdk import Strategy, ta

class UserStrategy(Strategy):
    name = "英文小寫策略名"
    description = "中文說明"
    default_params = {...}   # 對應 Pine 的 input.*()，保留原本預設值
    warmup = 100             # 指標需要的最少 K 棒數

    def on_bar(self, ctx):   # 每根 K 棒收盤呼叫一次，回傳決策或 None
        ...
```
ctx 可用：ctx.open / ctx.high / ctx.low / ctx.close / ctx.volume（list[float]，最後一個元素＝當前 K 棒）、
ctx.price（最新收盤價）、ctx.position_size（>0 多、<0 空、0 空手）、ctx.capabilities.supports_short、
ctx.long(size_pct, stop_loss=None, take_profit=None, leverage=1, reasoning="")、
ctx.short(...)（同參數）、ctx.close_position(reasoning="")。size_pct 為佔權益百分比。
self.p("參數名") 讀取參數。

ta 模組（皆回傳與輸入等長的 list，資料不足處為 None）：
ta.sma(src,n) ta.ema(src,n) ta.rma(src,n) ta.rsi(src,n) ta.atr(high,low,close,n) ta.tr(high,low,close)
ta.stdev(src,n) ta.bb(src,n,mult)->(basis,upper,lower) ta.macd(src,fast,slow,signal)->(line,signal,hist)
ta.highest(src,n) ta.lowest(src,n) ta.change(src,n)
ta.crossover(a,b) ta.crossunder(a,b)（回傳 bool，判斷最後一根；a、b 可為 list 或數字）

## 轉換規則
- Pine 的 x[1] 對應 python 的 series[-2]，x 對應 series[-1]；取值前檢查 None。
- strategy.entry(long) → ctx.long；strategy.entry(short) → ctx.short（先檢查 ctx.capabilities.supports_short）；
  strategy.close / strategy.exit 條件平倉 → ctx.close_position；strategy.exit 的 stop/limit → stop_loss/take_profit。
- 已有同向持倉時不要重複開倉；反向訊號時先回傳 ctx.close_position。
- default_qty_value（百分比）對應 size_pct，未指定時用 10。
- 只能 import math、statistics、goldhunter.strategies.sdk；不可使用檔案、網路、eval/exec、底線開頭屬性。
- 若是純指標（indicator），依其 plotshape/alertcondition 的買賣條件轉成進出場。
- 無法對應的功能（如 request.security 多週期、繪圖）略過，並在程式碼開頭用註解列出「未轉換項目」。'''


class ConversionResult(BaseModel):
    code: str
    ok: bool
    errors: list[str]
    attempts: int
    notes: list[str] = []


def extract_code(text: str) -> str:
    m = re.search(r"```(?:python)?\s*\n(.*?)```", text, re.S)
    return (m.group(1) if m else text).strip() + "\n"


def extract_notes(code: str) -> list[str]:
    """抓出「未轉換項目」註解，顯示在介面上提醒使用者"""
    notes, capture = [], False
    for line in code.splitlines():
        s = line.strip()
        if "未轉換" in s:
            capture = True
            continue
        if capture:
            if s.startswith("#") and s.lstrip("# ").strip():
                notes.append(s.lstrip("# -").strip())
            else:
                break
    return notes


async def convert_pine(ai: AIProvider, pine: str, max_attempts: int = 3, previous_code: str | None = None,
                       problems: list[str] | None = None) -> ConversionResult:
    """轉換 Pine Script；帶 previous_code + problems 時代表「依審查報告修正上一版」"""
    prompt = f"請轉換以下 Pine Script：\n```pine\n{pine}\n```"
    if previous_code and problems:
        prompt = (
            f"原始 Pine Script：\n```pine\n{pine}\n```\n\n目前的 Python 轉換：\n```python\n{previous_code}```\n\n"
            "自動審查發現以下問題，請修正後重新輸出完整程式碼：\n- " + "\n- ".join(problems)
        )
    code, errors = "", []
    for attempt in range(1, max_attempts + 1):
        text = await ai.complete_text(CONVERTER_SYSTEM, prompt)
        code = extract_code(text)
        errors = validate_code(code)
        if not errors:
            try:
                load_strategy_class(code)
            except StrategyCodeError as e:
                errors = [str(e)]
            except Exception as e:  # 載入時期錯誤（例如 NameError）
                errors = [f"{type(e).__name__}: {e}"]
        if not errors:
            return ConversionResult(code=code, ok=True, errors=[], attempts=attempt, notes=extract_notes(code))
        prompt = (
            f"原始 Pine Script：\n```pine\n{pine}\n```\n\n你上次的輸出：\n```python\n{code}```\n\n"
            f"有以下問題，請修正後重新輸出完整程式碼：\n- " + "\n- ".join(errors)
        )
    return ConversionResult(code=code, ok=False, errors=errors, attempts=max_attempts, notes=extract_notes(code))
