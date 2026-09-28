from __future__ import annotations

from pydantic import BaseModel, Field


class CopilotConfig(BaseModel):
    """AI 副駕駛設定（每個 Bot 一份）。

    你的策略決定「方向」，AI 負責判斷與微調：
      A. review  訊號審核：放行 / 否決 / 調整倉位倍數 / 收緊止損止盈（不能改方向）
      B. manage  持倉管理：只能做降低風險的動作（提前平倉、減倉、止損往有利方向移）
      C. tune    參數微調：在你設定的範圍內建議新參數，回測比較後才套用
    """

    review: bool = True
    review_min_confidence: float = Field(default=0.0, ge=0, le=1, description="AI 審核信心低於此值視為否決")
    size_mult_min: float = Field(default=0.5, ge=0, le=1)
    size_mult_max: float = Field(default=1.5, ge=1, le=3)

    manage: bool = True
    manage_interval_min: int = Field(default=60, ge=5, description="持倉中每隔幾分鐘讓 AI 檢查一次")

    tune: bool = False
    tune_interval_hours: int = Field(default=168, ge=6, description="預設每週一次")
    tune_lookback_bars: int = Field(default=1000, ge=200, le=5000)
    tune_auto_apply: bool = False  # False＝產生建議，等你在介面上按「套用」
    # {參數名: [最小值, 最大值]}；沒列出的參數 AI 不能動
    tune_ranges: dict[str, list[float]] = {}

    # 高影響經濟事件（CPI、FOMC、非農…）前後幾分鐘不開新倉；0＝關閉。不需要 AI，純規則。
    event_blackout_min: int = Field(default=60, ge=0)
    # 給 AI 的額外背景說明（例如：「我偏好順勢，不要逆勢抄底」）
    notes: str = ""
