"""連線檢查：用假的交易所 / HTTP / AI，確認每一項的狀態與說明（不連網）"""

import ccxt.async_support as ccxt
import httpx

from goldhunter import diagnostics
from goldhunter.ai.base import AIResult

from .test_e2e import H, client  # noqa: F401  （共用 TestClient fixture）


class FakeCCXT:
    def __init__(self, exchange_id, **kw):
        self.id = exchange_id
        self.kw = kw
        self.client = type("C", (), {})()
        self.client.markets = {}

    async def _ensure_markets(self):
        if self.id == "bybit":
            raise ccxt.ExchangeNotAvailable("bybit GET https://api.bybit.com")
        quote = "USDC" if self.id == "hyperliquid" else "USDT"
        bases = ["BTC", "ETH"] + (["PAXG", "XAUT"] if self.id == "okx" else [])
        self.client.markets = {f"{b}/{quote}:{quote}": {"swap": True, "settle": quote, "base": b} for b in bases}

    async def fetch_price(self, inst):
        await self._ensure_markets()
        return 112_345.6

    async def fetch_balance(self):
        if self.kw.get("api_key") == "bad":
            raise ccxt.AuthenticationError("invalid api key")
        from goldhunter.core.models import Balance
        return Balance(currency="USDT", total=1500, free=1200)

    async def fetch_positions(self):
        return []

    async def close(self):
        pass


def test_diagnostics_reports_each_item(client, monkeypatch):  # noqa: F811
    monkeypatch.setattr(diagnostics, "CCXTExchange", FakeCCXT)

    async def fake_get(url, **params):
        req = httpx.Request("GET", url)
        if "alternative.me" in url:
            return httpx.Response(200, json={"data": [{"value": "73", "value_classification": "Greed"}]}, request=req)
        if "faireconomy" in url:
            return httpx.Response(200, json=[{"country": "USD", "impact": "High"}], request=req)
        if "coindesk" in url:
            raise httpx.ConnectError("blocked", request=req)
        return httpx.Response(200, text="<rss><channel><item><title>BTC 新高</title></item></channel></rss>",
                              request=req)

    async def fake_deriv(eid, inst):
        return {"funding_rate_pct": 0.01, "open_interest": 1e9}

    monkeypatch.setattr(diagnostics, "_get", fake_get)
    monkeypatch.setattr(diagnostics.sources, "derivatives", fake_deriv)

    async def ai_json(system, user, schema):
        return AIResult(decision={"ok": True}, raw_text="{}", model="fake-model", input_tokens=10, output_tokens=3)

    client.ai.complete_json = ai_json
    client.post("/api/accounts", headers=H, json={"name": "模擬", "exchange_id": "binance"})
    client.post("/api/accounts", headers=H, json={"name": "OKX", "exchange_id": "okx", "paper": False,
                                                  "api_key": "good", "secret": "s", "passphrase": "p"})
    client.post("/api/accounts", headers=H, json={"name": "壞 Key", "exchange_id": "binance", "paper": False,
                                                  "api_key": "bad", "secret": "s"})
    client.post("/api/ai-models", headers=H, json={"name": "Claude", "provider": "anthropic", "api_key": "k"})
    r = client.post("/api/diagnostics", headers=H, json={"include_ai": True}).json()
    items = {(g["label"], i["name"]): i for g in r["groups"] for i in g["items"]}
    get = lambda label, name: next(v for (g, n), v in items.items() if label in g and name in n)  # noqa: E731

    assert get("公開行情", "OKX・黃金合約")["status"] == "ok" and "PAXG" in get("公開行情", "OKX・黃金合約")["detail"]
    assert get("公開行情", "Binance・黃金合約")["status"] == "warn"
    bybit = get("公開行情", "Bybit・市場清單")
    assert bybit["status"] == "fail" and "地區" in bybit["detail"]  # 附上中文排查提示
    assert get("公開行情", "Bybit・黃金合約")["status"] == "skip"
    assert get("OKX", "API Key")["status"] == "ok" and "1,500.00" in get("OKX", "API Key")["detail"]
    bad = get("壞 Key", "API Key")
    assert bad["status"] == "fail" and "Secret" in bad["detail"]
    assert get("模擬", "模擬帳戶")["status"] == "ok"
    assert get("AI 模型", "Claude")["status"] == "ok"
    assert get("資料來源", "恐懼貪婪")["detail"].startswith("目前 73")
    assert get("資料來源", "coindesk")["status"] == "fail"
    assert get("資料來源", "FRED")["status"] == "skip"
    assert r["summary"]["fail"] >= 3 and r["summary"]["ok"] >= 5

    r2 = client.post("/api/diagnostics", headers=H, json={"include_ai": False}).json()
    ai = next(g for g in r2["groups"] if g["key"] == "ai")
    assert ai["items"][0]["status"] == "skip"
