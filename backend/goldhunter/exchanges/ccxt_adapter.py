"""加密貨幣交易所（透過 ccxt）：Binance / OKX / Bybit / Hyperliquid，只做 USDT 永續合約。"""

from __future__ import annotations

import ccxt.async_support as ccxt

from goldhunter.core.models import (
    Balance,
    Candle,
    Instrument,
    InstrumentType,
    Market,
    Order,
    OrderRequest,
    OrderStatus,
    OrderType,
    Position,
    Side,
)
from goldhunter.exchanges.base import Capabilities, ExchangeAdapter

SUPPORTED = {
    "binance": {"label": "Binance", "needs_passphrase": False, "max_leverage": 125},
    "okx": {"label": "OKX", "needs_passphrase": True, "max_leverage": 100},
    "bybit": {"label": "Bybit", "needs_passphrase": False, "max_leverage": 100},
    # Hyperliquid 使用錢包地址 + API 錢包私鑰：api_key 欄位填錢包地址，secret 填私鑰
    "hyperliquid": {"label": "Hyperliquid", "needs_passphrase": False, "max_leverage": 50},
}

def exchange_symbol(exchange_id: str, instrument: Instrument) -> str:
    """統一用 USDT 報價；Hyperliquid 永續實際以 USDC 報價，自動轉換"""
    sym = instrument.ccxt_symbol
    if exchange_id == "hyperliquid":
        sym = sym.replace("/USDT:USDT", "/USDC:USDC")
    return sym


_STATUS = {"open": OrderStatus.OPEN, "closed": OrderStatus.FILLED, "canceled": OrderStatus.CANCELED}


class CCXTExchange(ExchangeAdapter):
    def __init__(
        self,
        exchange_id: str,
        api_key: str | None = None,
        secret: str | None = None,
        passphrase: str | None = None,
        testnet: bool = False,
    ):
        if exchange_id not in SUPPORTED:
            raise ValueError(f"尚未支援的交易所：{exchange_id}")
        self.id = exchange_id
        params: dict = {"enableRateLimit": True, "options": {"defaultType": "swap"}}
        if exchange_id == "hyperliquid":
            params.update(walletAddress=api_key, privateKey=secret)
        else:
            params.update(apiKey=api_key, secret=secret)
            if passphrase:
                params["password"] = passphrase
        self.client: ccxt.Exchange = getattr(ccxt, exchange_id)(params)
        if testnet:
            self.client.set_sandbox_mode(True)
        self._markets_loaded = False

    async def _ensure_markets(self) -> None:
        if not self._markets_loaded:
            await self.client.load_markets()
            self._markets_loaded = True

    def capabilities(self, instrument: Instrument) -> Capabilities:
        info = SUPPORTED[self.id]
        caps = Capabilities(supports_short=True, max_leverage=info["max_leverage"])
        if self._markets_loaded and (m := self.client.markets.get(exchange_symbol(self.id, instrument))):
            caps.min_qty = (m.get("limits", {}).get("amount", {}) or {}).get("min") or 0.0
            caps.taker_fee = m.get("taker") or caps.taker_fee
        return caps

    def round_qty(self, instrument: Instrument, qty: float) -> float:
        if self._markets_loaded and exchange_symbol(self.id, instrument) in self.client.markets:
            qty = float(self.client.amount_to_precision(exchange_symbol(self.id, instrument), qty))
        return super().round_qty(instrument, qty)

    async def fetch_candles(self, instrument: Instrument, timeframe: str, limit: int = 200) -> list[Candle]:
        await self._ensure_markets()
        rows = await self.client.fetch_ohlcv(exchange_symbol(self.id, instrument), timeframe, limit=limit)
        return [Candle(ts=r[0], open=r[1], high=r[2], low=r[3], close=r[4], volume=r[5] or 0) for r in rows]

    async def fetch_price(self, instrument: Instrument) -> float:
        await self._ensure_markets()
        t = await self.client.fetch_ticker(exchange_symbol(self.id, instrument))
        return float(t["last"])

    async def fetch_balance(self) -> Balance:
        await self._ensure_markets()
        bal = await self.client.fetch_balance()
        usdt = bal.get("USDT") or bal.get("USDC") or {}
        return Balance(currency="USDT", total=float(usdt.get("total") or 0), free=float(usdt.get("free") or 0))

    async def fetch_positions(self) -> list[Position]:
        await self._ensure_markets()
        out: list[Position] = []
        if self.client.has.get("fetchPositions"):
            for p in await self.client.fetch_positions():
                contracts = float(p.get("contracts") or 0)
                if not contracts:
                    continue
                size = contracts * float(p.get("contractSize") or 1)
                sym = p["symbol"].split(":")[0].replace("/USDC", "/USDT")
                out.append(
                    Position(
                        instrument=Instrument(market=Market.CRYPTO, symbol=sym, type=InstrumentType.PERP),
                        quantity=size if p.get("side") == "long" else -size,
                        entry_price=float(p.get("entryPrice") or 0),
                        leverage=int(float(p.get("leverage") or 1)),
                        unrealized_pnl=float(p.get("unrealizedPnl") or 0),
                    )
                )
        return out

    async def place_order(self, req: OrderRequest) -> Order:
        await self._ensure_markets()
        sym = exchange_symbol(self.id, req.instrument)
        params: dict = {}
        if req.leverage and self.client.has.get("setLeverage"):
            try:
                await self.client.set_leverage(req.leverage, sym)
            except ccxt.BaseError:
                pass  # 部分交易所在已有持倉時不允許改槓桿，沿用原槓桿
        if req.reduce_only:
            params["reduceOnly"] = True
        price = req.price
        if self.id == "hyperliquid" and req.type == OrderType.MARKET and price is None:
            # Hyperliquid 市價單需要參考價計算滑價上限
            price = await self.fetch_price(req.instrument)
        o = await self.client.create_order(sym, req.type.value, req.side.value, req.quantity, price, params)
        return Order(
            id=str(o.get("id")),
            instrument=req.instrument,
            side=Side(req.side),
            quantity=req.quantity,
            filled=float(o.get("filled") or 0),
            avg_price=o.get("average") or o.get("price"),
            status=_STATUS.get(o.get("status") or "", OrderStatus.OPEN),
            fee=float((o.get("fee") or {}).get("cost") or 0),
            raw={k: o.get(k) for k in ("id", "status", "average", "filled", "timestamp")},
        )

    async def perp_volumes(self) -> list[tuple[str, float]]:
        await self._ensure_markets()
        quote = "USDC" if self.id == "hyperliquid" else "USDT"
        tickers = await self.client.fetch_tickers()
        out = []
        for sym, t in tickers.items():
            m = self.client.markets.get(sym) or {}
            if not m.get("swap") or m.get("settle") != quote or not m.get("active", True):
                continue
            vol = t.get("quoteVolume") or ((t.get("baseVolume") or 0) * (t.get("last") or 0))
            out.append((m.get("base") or sym.split("/")[0], float(vol or 0)))
        return out

    async def cancel_order(self, order_id: str, instrument: Instrument) -> None:
        await self.client.cancel_order(order_id, exchange_symbol(self.id, instrument))

    async def close(self) -> None:
        await self.client.close()
