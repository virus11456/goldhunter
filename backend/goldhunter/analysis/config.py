from __future__ import annotations

from pydantic import BaseModel, Field


class EntryConfig(BaseModel):
    """Bot 的進場方式"""

    mode: str = "market"  # market：訊號出現就市價進場；smart：依進場分析掛單等待
    max_wait_bars: int | None = Field(default=None, ge=1, le=200)  # 空白＝依 K 線週期自動
    skip_negative_ev: bool = False  # 所有進場方式期望值都為負時，放棄這次進場
