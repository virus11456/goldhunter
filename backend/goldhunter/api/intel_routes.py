"""市場情報：資料來源設定與即時預覽。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from goldhunter.core.models import Instrument
from goldhunter.intel.hub import hub, load_settings, save_settings

router = APIRouter()


class IntelSettingsIn(BaseModel):
    derivatives: bool | None = None
    fear_greed: bool | None = None
    news: bool | None = None
    macro: bool | None = None
    calendar: bool | None = None
    rss_urls: list[str] | None = None
    fred_api_key: str | None = None  # 留空＝不修改
    cryptopanic_token: str | None = None


@router.get("/intel/settings")
def get_intel_settings():
    return load_settings().public()


@router.put("/intel/settings")
def put_intel_settings(body: IntelSettingsIn):
    return save_settings(body.model_dump()).public()


@router.get("/intel/snapshot")
async def intel_snapshot(symbol: str = "crypto:BTC/USDT:perp", exchange_id: str = "binance"):
    """預覽 AI 會看到的市場情報"""
    try:
        inst = Instrument.parse(symbol)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    snap = await hub.snapshot(exchange_id, inst)
    return {**snap.model_dump(), "prompt": snap.to_prompt()}
