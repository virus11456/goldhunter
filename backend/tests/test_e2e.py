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

    def __init__(self, candles: list[Candle], cash: float = 10_000):
        super().__init__(initial_cash=cash, fee_rate=0.0005, slippage=0.0)
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

    def fake_exchange(acc, capital=None):
        feed["ex"] = FeedExchange(make_candles(600, period=40, amp=15), capital or 10_000)
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


def test_baseline_exists_before_first_signal(client):
    bot = _setup_bot(client, {"review": True, "manage": False, "event_blackout_min": 0})
    client.post(f"/api/bots/{bot['id']}/start", headers=H)
    runner = manager.runners[bot["id"]]
    client.portal.call(runner.tick)
    assert runner.baseline is not None
    assert runner.baseline.equity() == pytest.approx(10_000)
    client.post(f"/api/bots/{bot['id']}/stop", headers=H)


def test_ai_trader_without_strategy(client):
    """AI 交易員：不需先建立策略，多幣種，AI 會看到市場情報與參考策略訊號"""
    acc = client.post("/api/accounts", headers=H, json={"name": "p", "exchange_id": "binance"}).json()
    ai = client.post("/api/ai-models", headers=H, json={"name": "c", "provider": "anthropic", "api_key": "k"}).json()
    ref = client.post("/api/strategies", headers=H, json={"name": "我的均線", "kind": "ma_cross",
                                                         "params": {"fast": 5, "slow": 15}}).json()
    body = {"name": "AI 交易員", "account_id": acc["id"], "ai_model_id": ai["id"],
            "symbols": ["crypto:BTC/USDT:perp", "crypto:ETH/USDT:perp"], "timeframe": "1h", "interval_sec": 3600,
            "ai_trader": {"instructions": "只做多，不追高", "reference_strategy_id": ref["id"]}}
    assert client.post("/api/bots", headers=H, json={**body, "ai_model_id": None}).status_code == 400
    bot = client.post("/api/bots", headers=H, json=body).json()
    assert bot["mode"] == "ai_trader" and bot["strategy_kind"] == "ai"
    assert bot["ai_trader"]["instructions"] == "只做多，不追高"
    assert bot["ai_trader"]["reference_strategy_id"] == ref["id"]

    prompts = []

    async def ai_json(system, user, schema):
        from goldhunter.ai.base import AIResult
        prompts.append((system, user))
        d = {"action": "open_long", "size_pct": 10, "leverage": 2, "stop_loss": 1, "take_profit": None,
             "confidence": 0.9, "reasoning": "趨勢向上"}
        return AIResult(decision=d, raw_text="{}", model="fake")

    client.ai.complete_json = ai_json
    assert client.post(f"/api/bots/{bot['id']}/start", headers=H).status_code == 200
    _tick(client, bot["id"], 3)
    system, user = prompts[-1]
    assert "AI 交易員" in system
    assert "只做多，不追高" in user and "恐懼貪婪指數" in user and "我的均線" in user
    trades = client.get(f"/api/trades?bot_id={bot['id']}", headers=H).json()
    assert {t["instrument"] for t in trades} == {"crypto:BTC/USDT:perp", "crypto:ETH/USDT:perp"}
    assert all(t["source"] == "ai" for t in trades)
    client.post(f"/api/bots/{bot['id']}/stop", headers=H)
    # 修改指示：更新同一個專屬策略，不會多建一個
    n_before = len(client.get("/api/strategies", headers=H).json())
    upd = client.put(f"/api/bots/{bot['id']}", headers=H,
                     json={**body, "ai_trader": {"instructions": "改成雙向交易"}}).json()
    assert upd["ai_trader"]["instructions"] == "改成雙向交易"
    assert len(client.get("/api/strategies", headers=H).json()) == n_before


def test_pine_auto_review_lifecycle(client, monkeypatch):
    from goldhunter.api import settings_routes

    from .test_custom_strategy import GOOD
    from .test_review import EQUIV

    async def fetch_history(exchange_id, inst, tf, s, e):
        return make_candles(600, period=40, amp=15)

    monkeypatch.setattr(settings_routes, "fetch_history", fetch_history)
    monkeypatch.setattr(settings_routes, "provider_from_config", lambda cfg: client.ai)
    client.ai.replies = [GOOD, EQUIV]
    ai = client.post("/api/ai-models", headers=H, json={"name": "c", "provider": "anthropic", "api_key": "k"}).json()
    r = client.post("/api/strategies/convert-pine", headers=H,
                    json={"name": "Pine 策略", "pine": "//@version=5", "ai_model_id": ai["id"]}).json()
    st = r["strategy"]
    assert r["review"]["passed"], r["review"]
    assert st["status"] == "paper_only" and st["metrics"]["sharpe"] is not None
    assert st["paper_progress"]["days_required"] == 7

    paper = client.post("/api/accounts", headers=H, json={"name": "p", "exchange_id": "binance"}).json()
    live = client.post("/api/accounts", headers=H, json={"name": "l", "exchange_id": "binance", "paper": False,
                                                         "api_key": "k", "secret": "s"}).json()
    base = {"strategy_id": st["id"], "symbols": ["crypto:BTC/USDT:perp"], "timeframe": "1h", "interval_sec": 3600,
            "copilot": {"review": False, "manage": False}}
    b_live = client.post("/api/bots", headers=H, json={**base, "name": "live", "account_id": live["id"]}).json()
    b_paper = client.post("/api/bots", headers=H, json={**base, "name": "paper", "account_id": paper["id"]}).json()
    r_live = client.post(f"/api/bots/{b_live['id']}/start", headers=H)
    assert r_live.status_code == 400 and "模擬期" in r_live.json()["detail"]
    assert client.post(f"/api/bots/{b_paper['id']}/start", headers=H).status_code == 200
    client.post(f"/api/bots/{b_paper['id']}/stop", headers=H)

    # 手動開放實盤
    assert client.post(f"/api/strategies/{st['id']}/promote", headers=H).json()["status"] == "active"
    assert client.post(f"/api/bots/{b_live['id']}/start", headers=H).status_code == 200
    client.post(f"/api/bots/{b_live['id']}/stop", headers=H)

    # 審查沒過 → 讓 AI 修正後重審
    from .test_review import NOT_EQUIV

    client.ai.replies = [GOOD, NOT_EQUIV]
    r2 = client.post("/api/strategies/convert-pine", headers=H,
                     json={"name": "Pine 2", "pine": "//@version=5", "ai_model_id": ai["id"]}).json()
    assert r2["strategy"]["status"] == "pending_review" and not r2["review"]["passed"]
    client.ai.replies = [GOOD, EQUIV]
    fixed = client.post(f"/api/strategies/{r2['strategy']['id']}/fix", headers=H, json={"ai_model_id": ai["id"]}).json()
    assert fixed["review"]["passed"] and fixed["strategy"]["status"] == "paper_only"


def test_personas_upload_and_master_portfolio(client):
    """上傳女媧檔案 → 保真度達標進模擬期 → AI 設計組合計畫 → 建立並啟動大師組合 → 大師組合總覽"""
    from .test_personas import FIDELITY_MD, OPEN, SKILL_MD, _zip, b64

    assert client.get("/api/personas", headers=H).json() == []  # 沒有內建大師
    low = client.post("/api/personas/upload", headers=H,
                      json={"filename": "SKILL.md", "content_base64": b64(SKILL_MD),
                            "fidelity_filename": "FIDELITY.md",
                            "fidelity_base64": b64(FIDELITY_MD.replace("82/100 · 等级 B", "60/100 · 等级 C"))}).json()
    assert low["status"] == "draft" and low["fidelity"]["score"] == 60
    assert client.post(f"/api/personas/{low['id']}/promote", headers=H).status_code == 400
    up = client.post("/api/personas/upload", headers=H, json={
        "filename": "t.zip", "content_base64": b64(_zip({"t/SKILL.md": SKILL_MD, "t/FIDELITY.md": FIDELITY_MD}))}).json()
    assert up["name"] == "测试交易员" and up["source"] == "nuwa" and up["status"] == "paper_only"
    assert up["fidelity"]["score"] == 82 and "核心心智模型" in up["meta"]["kept_sections"]
    assert "raw_skill" not in client.get("/api/personas", headers=H).json()[1]["meta"]
    assert client.post("/api/personas/upload", headers=H,
                       json={"filename": "x.exe", "content_base64": b64("MZ")}).status_code == 400

    ai = client.post("/api/ai-models", headers=H, json={"name": "c", "provider": "anthropic", "api_key": "k"}).json()
    from goldhunter.api import persona_routes
    persona_routes.provider_from_config = lambda cfg: client.ai
    client.ai.replies = [{"suitable": True, "style_summary": "順勢", "reason": "r", "timeframe": "1h",
                          "holding_period": "數天", "universe": {"mode": "rules", "top_n": 2, "exclude_meme": True,
                                                              "include_only": [], "symbols": []},
                          "max_positions": 1, "position_pct": 10, "max_leverage": 2, "allow_short": False,
                          "entry_mode": "market", "instructions": "只做突破"}]
    plan = client.post(f"/api/personas/{up['id']}/portfolio-plan", headers=H, json={"ai_model_id": ai["id"]}).json()
    assert plan["timeframe"] == "1h" and plan["max_positions"] == 1

    live = client.post("/api/accounts", headers=H, json={"name": "l", "exchange_id": "binance", "paper": False,
                                                         "api_key": "k", "secret": "s"}).json()
    paper = client.post("/api/accounts", headers=H, json={"name": "p", "exchange_id": "binance"}).json()
    r = client.post(f"/api/personas/{up['id']}/portfolio", headers=H,
                    json={"ai_model_id": ai["id"], "account_id": live["id"], "plan": plan}).json()
    assert not r["started"] and "模擬期" in r["error"]  # 大師還在模擬期 → 實盤不可啟動

    captured = []

    async def ai_json(system, user, schema):
        from goldhunter.ai.base import AIResult
        captured.append(system)
        return AIResult(decision=OPEN, raw_text="{}", model="fake")

    client.ai.complete_json = ai_json
    r = client.post(f"/api/personas/{up['id']}/portfolio", headers=H,
                    json={"ai_model_id": ai["id"], "account_id": paper["id"], "plan": plan, "capital": 5000}).json()
    assert r["started"], r
    bid = r["bot_id"]
    bot = next(b for b in client.get("/api/bots", headers=H).json() if b["id"] == bid)
    assert bot["ai_trader"]["persona_id"] == up["id"] and bot["universe"]["mode"] == "rules"
    assert bot["risk"]["long_only"] and bot["risk"]["max_positions"] == 1

    async def vols():
        return [("DOGE", 9e9), ("BTC", 5e9), ("ETH", 4e9), ("SOL", 1e9)]

    runner = manager.runners[bid]
    runner.exchange.perp_volumes = vols
    runner.universe_at = None
    _tick(client, bid, 3)
    assert [i.symbol for i in runner.instruments] == ["BTC/USDT", "ETH/USDT"]  # 規則挑選、排除迷因幣
    assert any("你的交易大腦：测试交易员" in sp for sp in captured)
    trades = client.get(f"/api/trades?bot_id={bid}", headers=H).json()
    assert len({t["instrument"] for t in trades}) == 1  # 組合最多持有 1 個標的
    dec = client.get(f"/api/decisions?bot_id={bid}", headers=H).json()
    assert any("組合上限" in " ".join(d.get("reasons") or []) for d in dec)

    masters = client.get("/api/masters", headers=H).json()
    m = next(x for x in masters if x["bot_id"] == bid)
    assert m["persona"]["name"] == "测试交易员" and m["running"] and m["start_equity"] == pytest.approx(5000, rel=0.01)
    assert m["plan"]["style_summary"] == "順勢" and m["curve"]
    client.post(f"/api/bots/{bid}/stop", headers=H)

    assert client.delete(f"/api/personas/{up['id']}", headers=H).status_code == 400  # 仍有組合在用
    assert client.post(f"/api/personas/{up['id']}/promote", headers=H).json()["status"] == "active"
    assert client.delete(f"/api/personas/{low['id']}", headers=H).json() == {"ok": True}


def test_smart_entry_waits_then_fills_or_expires(client):
    """智慧進場：訊號出現先掛單等待，碰到點位才進場；逾時則取消"""
    from goldhunter.analysis.entry import EntryAnalysis, EntryCandidate
    from goldhunter.engine import bot as bot_mod

    bot = _setup_bot(client, {"review": False, "manage": False, "event_blackout_min": 0})
    client.put(f"/api/bots/{bot['id']}", headers=H, json={
        "name": "b", "account_id": bot["account_id"], "strategy_id": bot["strategy_id"], "ai_model_id": bot["ai_model_id"],
        "symbols": bot["symbols"], "timeframe": "1h", "interval_sec": 3600, "risk": {"max_leverage": 3},
        "copilot": {"review": False, "manage": False, "event_blackout_min": 0},
        "entry": {"mode": "smart", "max_wait_bars": 3}})
    assert client.get("/api/bots", headers=H).json()[-1]["entry"]["mode"] == "smart"

    level = {"v": None}

    def fake_analyze(candles, timeframe, direction, stop=None, target=None, signal_idx=None):
        p = candles[-1].close
        lv = p + 50 if direction == "long" else p - 50  # 測試用：下一根一定碰得到，專門驗證成交流程
        if level["v"] == "far":
            lv = p * 0.5 if direction == "long" else p * 1.5  # 永遠碰不到
        return EntryAnalysis(direction=direction, price=p, atr=1, stop=p * (0.9 if direction == "long" else 1.1),
                             target=p * (1.2 if direction == "long" else 0.8), stop_source="x", target_source="x",
                             candidates=[EntryCandidate(key="market", label="市價", price=p, rr=2, fill_prob=1,
                                                        win_prob=0.4, ev_r=0.2, ev_per_signal=0.2),
                                         EntryCandidate(key="ema20", label="等回檔到 EMA20", price=lv, rr=3,
                                                        fill_prob=0.7, win_prob=0.4, ev_r=0.6, ev_per_signal=0.42)],
                             recommended="ema20", recommendation="等回檔", wait_bars=12, signal_samples=20,
                             sample_basis="測試", high_frequency=False)

    orig = bot_mod.analyze_entry
    bot_mod.analyze_entry = fake_analyze
    try:
        client.post(f"/api/bots/{bot['id']}/start", headers=H)
        runner = _tick(client, bot["id"], 60)
        decisions = client.get(f"/api/decisions?bot_id={bot['id']}&limit=500", headers=H).json()
        waits = [d for d in decisions if any(r.startswith("等待進場") for r in d["reasons"])]
        fills = [d for d in decisions if d["approved"] and any(r.startswith("掛單成交") for r in d["reasons"])]
        assert waits and fills, "應先等待、再掛單成交"
        assert "entry_analysis" in fills[0]["decision"]
        client.post(f"/api/bots/{bot['id']}/stop", headers=H)

        # 永遠碰不到 → 逾時取消
        level["v"] = "far"
        client.post(f"/api/bots/{bot['id']}/start", headers=H)
        runner = manager.runners[bot["id"]]
        runner.exchange.i = 300
        _tick(client, bot["id"], 60)
        decisions = client.get(f"/api/decisions?bot_id={bot['id']}&limit=1000", headers=H).json()
        assert any(any("逾時未成交" in r for r in d["reasons"]) for d in decisions)
        assert runner.pending is not None
        client.post(f"/api/bots/{bot['id']}/stop", headers=H)
    finally:
        bot_mod.analyze_entry = orig


def test_entry_analysis_endpoints(client, monkeypatch):
    from goldhunter.api import analysis_routes

    async def fetch_history(exchange_id, inst, tf, s, e):
        return make_candles(800, period=40, amp=15)

    monkeypatch.setattr(analysis_routes, "fetch_history", fetch_history)
    st = client.post("/api/strategies", headers=H, json={"name": "ma", "kind": "ma_cross",
                                                        "params": {"fast": 5, "slow": 15}}).json()
    r = client.post("/api/analysis/entry", headers=H, json={"strategy_id": st["id"], "direction": "long"}).json()
    assert r["analysis"]["candidates"][0]["key"] == "market"
    assert r["analysis"]["recommendation"]

    bot = _setup_bot(client, {"review": False, "manage": False, "event_blackout_min": 0})
    client.post(f"/api/bots/{bot['id']}/start", headers=H)
    _tick(client, bot["id"], 2)
    e = client.get(f"/api/bots/{bot['id']}/entry-analysis?direction=short", headers=H).json()
    assert e["entry"]["mode"] == "market" and e["items"][0]["analysis"]["direction"] in ("long", "short")
    client.post(f"/api/bots/{bot['id']}/stop", headers=H)


class StopFeed(FeedExchange):
    """支援交易所端條件止損單的假交易所：記錄掛單 / 撤單"""

    supports_stop_orders = True

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.stop_orders: dict[str, tuple] = {}
        self.cancelled: list[str] = []
        self.n = 0

    async def fetch_candles(self, instrument, timeframe, limit=200):
        window = self.all[max(0, self.i - limit): self.i]  # 不前進，價格固定，方便檢查
        self.set_price(instrument, window[-1].close)
        return window

    async def place_stop_order(self, instrument, side, quantity, stop_price):
        self.n += 1
        oid = f"stop-{self.n}"
        self.stop_orders[oid] = (str(instrument), side.value, quantity, stop_price)
        return oid

    async def cancel_stop_order(self, instrument, order_id):
        self.stop_orders.pop(order_id, None)
        self.cancelled.append(order_id)


def test_exchange_stop_orders_follow_position(client, monkeypatch):
    from goldhunter.core.models import Instrument
    from goldhunter.store.db import Bot, get_engine

    ex_holder = {}

    def fake(acc, capital=None):
        ex_holder["ex"] = StopFeed(make_candles(600, period=40, amp=15))
        return ex_holder["ex"]

    monkeypatch.setattr(manager_mod, "exchange_from_account", fake)
    acc = client.post("/api/accounts", headers=H, json={"name": "p", "exchange_id": "binance"}).json()
    st = client.post("/api/strategies", headers=H, json={"name": "tv", "kind": "tradingview"}).json()
    bot = client.post("/api/bots", headers=H, json={
        "name": "止損", "account_id": acc["id"], "strategy_id": st["id"], "symbols": ["crypto:BTC/USDT:perp"],
        "timeframe": "1h", "interval_sec": 3600, "risk": {"allow_pyramiding": True, "max_total_exposure_pct": 500}}).json()
    assert client.post(f"/api/bots/{bot['id']}/start", headers=H).status_code == 200
    ex = ex_holder["ex"]
    btc = "crypto:BTC/USDT:perp"
    price = ex.all[ex.i - 1].close

    def sig(**kw):
        r = client.post(f"/api/bots/{bot['id']}/signal", headers=H, json={"instrument": btc, **kw})
        assert r.status_code == 200, r.text
        return r.json()

    # 開倉 → 交易所掛一張「只減倉」止損單，數量＝持倉
    sig(action="open_long", size_pct=10, leverage=2, stop_loss=round(price * 0.95, 2))
    pos = ex.positions[Instrument.parse(btc)]
    (oid, (_, side, qty, sp)), = ex.stop_orders.items()
    assert side == "sell" and qty == pytest.approx(pos.quantity) and sp == pytest.approx(round(price * 0.95, 2))
    # 加碼 → 撤掉舊單、依新數量重掛
    sig(action="open_long", size_pct=10, leverage=2, stop_loss=round(price * 0.96, 2))
    assert oid in ex.cancelled and len(ex.stop_orders) == 1
    assert next(iter(ex.stop_orders.values()))[2] == pytest.approx(ex.positions[Instrument.parse(btc)].quantity)
    positions = client.get(f"/api/bots/{bot['id']}/positions", headers=H).json()["positions"]
    assert positions[0]["exchange_stop"]["price"] == pytest.approx(round(price * 0.96, 2))

    # 重啟 Bot：止損狀態從資料庫接回，不會重複掛單
    client.post(f"/api/bots/{bot['id']}/stop", headers=H)
    from sqlmodel import Session
    with Session(get_engine()) as s:
        saved = s.get(Bot, bot["id"]).stop_state[btc]
    assert saved["order_id"] in ex.stop_orders and saved["stop_loss"] == pytest.approx(round(price * 0.96, 2))
    old = ex
    monkeypatch.setattr(manager_mod, "exchange_from_account", lambda acc, capital=None: old)
    assert client.post(f"/api/bots/{bot['id']}/start", headers=H).status_code == 200
    runner = _tick(client, bot["id"], 1)
    assert runner.stops[Instrument.parse(btc)].stop_loss == pytest.approx(round(price * 0.96, 2))
    assert len(old.stop_orders) == 1 and old.n == 2

    # 手動平倉 → 撤掉交易所止損單
    sig(action="close")
    assert not old.stop_orders

    # 交易所止損單觸發（持倉在交易所消失）→ 記錄並停止盯盤
    sig(action="open_long", size_pct=10, leverage=2, stop_loss=round(price * 0.95, 2))
    old.positions.clear()
    old.stop_orders.clear()
    _tick(client, bot["id"], 1)
    dec = client.get(f"/api/decisions?bot_id={bot['id']}", headers=H).json()
    assert any(d["source"] == "exchange_stop" and "已觸發" in d["reasons"][0] for d in dec)
    assert not runner.stops and not runner.xstops
    client.post(f"/api/bots/{bot['id']}/stop", headers=H)
