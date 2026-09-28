"""端對端：透過 REST API 建立帳戶 / 策略 / Bot，啟動後用假行情跑，驗證 AI 副駕駛、TradingView、紀錄與對照組。"""

from datetime import UTC

import pytest
from fastapi.testclient import TestClient

from goldhunter.core.models import Candle
from goldhunter.engine import manager as manager_mod
from goldhunter.engine.manager import manager
from goldhunter.exchanges.paper import PaperExchange
from goldhunter.intel.hub import IntelSnapshot, hub
from goldhunter.main import app

from .conftest import make_candles
from .test_custom_strategy import FakeAI

H = {"Authorization": "Bearer test-token"}


class FeedExchange(PaperExchange):
    """假行情：每次 fetch_candles 前進一根 K 線"""

    def __init__(self, candles: list[Candle]):
        super().__init__(initial_cash=10_000, fee_rate=0.0005, slippage=0.0)
        self.all = candles
        self.i = 120

    async def fetch_candles(self, instrument, timeframe, limit=200):
        self.i += 1
        window = self.all[max(0, self.i - limit): self.i]
        self.set_price(instrument, window[-1].close)
        return window


@pytest.fixture
def client(monkeypatch):
    feed = {"ex": None}

    def fake_exchange(acc):
        feed["ex"] = FeedExchange(make_candles(600, period=40, amp=15))
        return feed["ex"]

    ai = FakeAI([])
    monkeypatch.setattr(manager_mod, "exchange_from_account", fake_exchange)
    monkeypatch.setattr(manager_mod, "provider_from_config", lambda cfg: ai)

    async def fake_snapshot(exchange_id, inst, settings=None):
        return IntelSnapshot(instrument=str(inst), fetched_at="now", fear_greed={"value": 50, "label": "Neutral"})

    monkeypatch.setattr(hub, "snapshot", fake_snapshot)
    with TestClient(app) as c:
        c.feed, c.ai = feed, ai
        yield c


def test_auth_required(client):
    assert client.get("/api/meta").status_code == 401
    assert client.get("/api/meta", headers={"Authorization": "Bearer wrong"}).status_code == 401
    meta = client.get("/api/meta", headers=H).json()
    assert "copilot_defaults" in meta and meta["copilot_defaults"]["review"] is True


def test_secrets_are_masked(client):
    r = client.post("/api/accounts", headers=H, json={"name": "live", "exchange_id": "binance", "paper": False,
                                                      "api_key": "abcd1234SECRETKEY", "secret": "s3cr3t"})
    body = r.json()
    assert body["api_key"] == "****TKEY"
    assert "s3cr3t" not in r.text and "abcd1234SECRETKEY" not in r.text
    listing = client.get("/api/accounts", headers=H).text
    assert "abcd1234SECRETKEY" not in listing
    assert client.post("/api/accounts", headers=H, json={"name": "x", "exchange_id": "binance",
                                                         "paper": False}).status_code == 400


def _setup_bot(client, copilot: dict, strategy=None):
    acc = client.post("/api/accounts", headers=H, json={"name": "paper", "exchange_id": "binance"}).json()
    ai = client.post("/api/ai-models", headers=H, json={"name": "c", "provider": "anthropic", "api_key": "k"}).json()
    st = client.post("/api/strategies", headers=H, json=strategy or {
        "name": "ma", "kind": "ma_cross", "params": {"fast": 5, "slow": 15, "size_pct": 10}}).json()
    bot = client.post("/api/bots", headers=H, json={
        "name": "b", "account_id": acc["id"], "strategy_id": st["id"], "ai_model_id": ai["id"],
        "symbols": ["crypto:BTC/USDT:perp"], "timeframe": "1h", "interval_sec": 3600,
        "risk": {"max_leverage": 3}, "copilot": copilot}).json()
    assert "id" in bot, bot
    return bot


def _tick(client, bot_id, n):
    runner = manager.runners[bot_id]
    for _ in range(n):
        client.portal.call(runner.tick)
    return runner


def test_copilot_review_veto_and_baseline(client):
    bot = _setup_bot(client, {"review": True, "manage": False, "event_blackout_min": 0})
    assert client.post(f"/api/bots/{bot['id']}/start", headers=H).status_code == 200
    veto = {"verdict": "veto", "size_multiplier": 1, "stop_loss": None, "take_profit": None, "confidence": 0.9,
            "reasoning": "重大利空新聞"}
    client.ai.replies = [veto] * 50
    runner = _tick(client, bot["id"], 80)
    decisions = client.get(f"/api/decisions?bot_id={bot['id']}", headers=H).json()
    vetoed = [d for d in decisions if any("AI 否決" in r for r in d["reasons"])]
    assert vetoed, "AI 應該否決開倉訊號"
    assert vetoed[0]["decision"]["copilot"]["verdict"] == "veto"
    # AI 全部否決 → 真實帳戶沒有開倉，但對照組（不經 AI）有交易
    trades = client.get(f"/api/trades?bot_id={bot['id']}", headers=H).json()
    assert not [t for t in trades if not t["reduce_only"]]
    assert runner.baseline is not None and runner.baseline.fills
    eq = client.get(f"/api/equity?bot_id={bot['id']}", headers=H).json()
    assert eq and eq[-1]["baseline_equity"] is not None
    dash = client.get("/api/dashboard", headers=H).json()
    assert dash["bots_running"] >= 1 and dash["bots"][-1]["copilot_active"]
    client.post(f"/api/bots/{bot['id']}/stop", headers=H)


def test_copilot_adjust_and_manage_close(client):
    bot = _setup_bot(client, {"review": True, "manage": True, "manage_interval_min": 5, "event_blackout_min": 0})
    client.post(f"/api/bots/{bot['id']}/start", headers=H)
    runner = manager.runners[bot["id"]]
    approve = {"verdict": "adjust", "size_multiplier": 0.5, "stop_loss": None, "take_profit": None,
               "confidence": 0.8, "reasoning": "訊號可行但縮倉"}
    close = {"action": "close", "reduce_pct": None, "new_stop": None, "confidence": 0.8, "reasoning": "新聞轉空"}

    async def ai_json(system, user, schema):
        from goldhunter.ai.base import AIResult
        data = close if "持倉管理員" in system else approve
        return AIResult(decision=data, raw_text=str(data), model="fake")

    client.ai.complete_json = ai_json
    for _ in range(80):
        runner.last_manage.clear()
        client.portal.call(runner.tick)
    trades = client.get(f"/api/trades?bot_id={bot['id']}", headers=H).json()
    opens = [t for t in trades if not t["reduce_only"]]
    closes = [t for t in trades if t["reduce_only"] and t["source"] == "copilot"]
    assert opens and all(t["source"] == "strategy+ai" for t in opens)
    assert closes, "AI 持倉管理應該平倉"
    client.post(f"/api/bots/{bot['id']}/stop", headers=H)


def test_event_blackout_blocks_opening(client, monkeypatch):
    from datetime import datetime

    async def snap_with_event(exchange_id, inst, settings=None):
        return IntelSnapshot(instrument=str(inst), fetched_at="now",
                             events=[{"title": "FOMC", "time": datetime.now(UTC).isoformat(), "country": "USD"}])

    monkeypatch.setattr(hub, "snapshot", snap_with_event)
    bot = _setup_bot(client, {"review": False, "manage": False, "event_blackout_min": 60})
    client.post(f"/api/bots/{bot['id']}/start", headers=H)
    _tick(client, bot["id"], 60)
    decisions = client.get(f"/api/decisions?bot_id={bot['id']}", headers=H).json()
    assert any("事件避險" in r for d in decisions for r in d["reasons"])
    assert not client.get(f"/api/trades?bot_id={bot['id']}", headers=H).json()
    client.post(f"/api/bots/{bot['id']}/stop", headers=H)


def test_tradingview_webhook_flow(client):
    bot = _setup_bot(client, {"review": False, "manage": False, "event_blackout_min": 0},
                     strategy={"name": "tv", "kind": "tradingview"})
    url = f"/api/tradingview/webhook/{bot['id']}"
    payload = {"passphrase": bot["webhook_secret"], "action": "buy", "market_position": "long", "size_pct": 10}
    assert client.post(url, json=payload).status_code == 409  # Bot 未啟動
    client.post(f"/api/bots/{bot['id']}/start", headers=H)
    _tick(client, bot["id"], 1)
    assert client.post(url, json={**payload, "passphrase": "wrong"}).status_code == 401
    r = client.post(url, json=payload).json()
    assert r["approved"], r
    r2 = client.post(url, json={**payload, "action": "sell", "market_position": "flat"}).json()
    assert r2["approved"]
    trades = client.get(f"/api/trades?bot_id={bot['id']}", headers=H).json()
    assert len(trades) == 2 and {t["source"] for t in trades} == {"tradingview"}
    client.post(f"/api/bots/{bot['id']}/stop", headers=H)


def test_tuning_endpoint(client):
    bot = _setup_bot(client, {"review": False, "manage": False, "tune": True, "event_blackout_min": 0,
                              "tune_ranges": {"fast": [3, 10], "slow": [12, 30]}})
    client.post(f"/api/bots/{bot['id']}/start", headers=H)
    runner = manager.runners[bot["id"]]
    runner.last_tune = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)  # 避免 tick 自動觸發
    client.ai.replies = [{"params": {"fast": 4, "slow": 20}, "reasoning": "測試"}]
    runner.exchange.i = 590
    run = client.post(f"/api/bots/{bot['id']}/tune-now", headers=H).json()
    assert run["status"] in ("proposed", "rejected"), run
    assert run["proposed_params"]["fast"] == 4
    applied = client.post(f"/api/tuning/{run['id']}/apply", headers=H).json()
    assert applied.get("status") == "applied", applied
    assert runner.strategy.params["fast"] == 4
    bots = client.get("/api/bots", headers=H).json()
    assert next(b for b in bots if b["id"] == bot["id"])["params_override"]["fast"] == 4
    client.post(f"/api/bots/{bot['id']}/stop", headers=H)
    assert client.delete(f"/api/bots/{bot['id']}", headers=H).json()["ok"]
    assert not client.get(f"/api/tuning?bot_id={bot['id']}", headers=H).json()


def test_pending_strategy_cannot_start(client):
    from goldhunter.strategies.custom import TEMPLATE

    bot = _setup_bot(client, {"review": False, "manage": False},
                     strategy={"name": "py", "kind": "python", "code": TEMPLATE})
    # 直接建立的 python 策略為 active；修改程式碼後變成待審核
    st_id = bot["strategy_id"]
    client.put(f"/api/strategies/{st_id}", headers=H, json={"name": "py", "kind": "python",
                                                             "code": TEMPLATE + "\n# changed\n"})
    r = client.post(f"/api/bots/{bot['id']}/start", headers=H)
    assert r.status_code == 400 and "審核" in r.json()["detail"]
    assert client.post(f"/api/strategies/{st_id}/activate", headers=H).json()["status"] == "active"
    assert client.post(f"/api/bots/{bot['id']}/start", headers=H).status_code == 200
    client.post(f"/api/bots/{bot['id']}/stop", headers=H)
