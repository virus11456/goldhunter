"""投資大師：上傳解析、費用估算、保真度評分、鏈上紀錄、AI 交易員大腦與委員會、標的範圍規則"""

import base64
import io
import zipfile

import pytest

from goldhunter.engine.universe import UniverseRules, pick
from goldhunter.personas.distill import builtin_profile, estimate_cost, fidelity, parse_upload
from goldhunter.personas.onchain import summarize_fills, summary_to_corpus
from goldhunter.strategies.ai_strategy import AIStrategy

from .test_backtest_and_ai import _ctx
from .test_custom_strategy import FakeAI

SKILL_MD = """---
name: test-trader-perspective
description: |
  測試交易員的思維框架。用途：思維顧問。
---

# 測試交易員 · 思維操作系統

## 核心心智模型
### 模型1：順勢
一句話：只做趨勢方向。
### 模型2：小虧大賺
一句話：止損要小、讓獲利奔跑。

## 決策啟發式
1. 突破才進場
2. 虧損 5% 出場

## 反模式
- 攤平

## 誠實邊界
- 盤整期表現差
- 不懂基本面
- 不做長線
""" + "補充說明。" * 60


def test_parse_upload_md_and_zip():
    p = parse_upload("SKILL.md", SKILL_MD.encode())
    assert p.name == "測試交易員" and "順勢" in p.profile and "思維框架" in p.description
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("test-trader/SKILL.md", SKILL_MD)
        z.writestr("test-trader/references/research.md", "## 調研\n一手資料：1998 年的交易紀錄")
        z.writestr("test-trader/FIDELITY.md", "舊評分，不應放進檔案")
        z.writestr("test-trader/scripts/evil.py", "import os; os.system('rm -rf /')")
    pz = parse_upload("test-trader.zip", buf.getvalue())
    assert "1998 年的交易紀錄" in pz.profile and "舊評分" not in pz.profile and "os.system" not in pz.profile
    assert not any(f.endswith(".py") for f in pz.files)
    with pytest.raises(ValueError):
        parse_upload("x.pdf", b"abc")
    with pytest.raises(ValueError):
        parse_upload("SKILL.md", b"# too short")


def test_builtin_profiles_complete():
    for slug in ("livermore", "soros", "munger", "buffett"):
        text = builtin_profile(slug)
        for section in ("核心心智模型", "決策規則", "反模式", "誠實邊界"):
            assert section in text, (slug, section)


def test_estimate_cost():
    e = estimate_cost("anthropic", "claude-opus-5", "standard")
    assert e["usd"] and e["web_searches"] == 20 and e["usd"] > 0.5
    q = estimate_cost("anthropic", "claude-opus-5", "quick")
    assert q["usd"] < e["usd"]
    assert estimate_cost("deepseek", "deepseek-chat", "quick")["usd"] is None


async def test_fidelity_three_independent_calls():
    exam = {"questions": [
        {"type": "stance", "question": "要不要攤平？", "expected": "絕不攤平"},
        {"type": "stance", "question": "盤整時做什麼？", "expected": "等待"},
        {"type": "stance", "question": "止損怎麼設？", "expected": "小止損"},
        {"type": "out_of_scope", "question": "怎麼看 AI 晶片？", "expected": "應標註為推斷"},
        {"type": "scenario", "question": "BTC 放量突破前高", "expected": "小倉位試單"},
    ]}
    answers = {"answers": ["不攤平", "等待", "5%", "這是框架推斷…", "試單"]}
    grade = {"summary": "像", "dimensions": [
        {"name": "立場一致性", "score": 26, "max": 30, "reason": ""},
        {"name": "風格辨識度", "score": 15, "max": 20, "reason": ""},
        {"name": "邊緣誠實度", "score": 18, "max": 20, "reason": ""},
        {"name": "情境合理性", "score": 12, "max": 15, "reason": ""},
        {"name": "結構完整度", "score": 99, "max": 15, "reason": "超過上限應被截斷"},
    ]}
    ai = FakeAI([exam, answers, grade])
    r = await fidelity(ai, "測試交易員", SKILL_MD)
    assert r.score == 86 and r.grade == "A" and r.passed
    assert r.questions[0]["answer"] == "不攤平"


def test_onchain_summary():
    base = 1_750_000_000_000
    fills = [
        {"coin": "BTC", "dir": "Open Long", "px": "100000", "sz": "0.1", "time": base, "startPosition": "0", "closedPnl": "0"},
        {"coin": "BTC", "dir": "Close Long", "px": "102000", "sz": "0.1", "time": base + 7_200_000,
         "startPosition": "0.1", "closedPnl": "200"},
        {"coin": "ETH", "dir": "Open Short", "px": "4000", "sz": "1", "time": base + 86_400_000, "startPosition": "0", "closedPnl": "0"},
        {"coin": "ETH", "dir": "Close Short", "px": "4100", "sz": "1", "time": base + 90_000_000,
         "startPosition": "-1", "closedPnl": "-100"},
    ]
    s = summarize_fills(fills)
    assert s["open_trades"] == 2 and s["long_ratio_pct"] == 50 and s["win_rate_pct"] == 50
    assert s["avg_hold_hours"] == 1.5 and s["total_closed_pnl"] == 100
    assert "勝率 50.0%" in summary_to_corpus("0x" + "a" * 40, s)


def test_universe_pick():
    vols = [("BTC", 9e9), ("ETH", 5e9), ("DOGE", 3e9), ("SOL", 2e9), ("PEPE", 1e9), ("XRP", 8e8), ("LUNA", 7e8)]
    assert pick(vols, UniverseRules(mode="rules", top_n=3)) == ["BTC", "ETH", "SOL"]
    assert pick(vols, UniverseRules(mode="rules", top_n=3, exclude_meme=False)) == ["BTC", "ETH", "DOGE"]
    assert pick(vols, UniverseRules(mode="rules", top_n=5, exclude=["ETH", "luna"])) == ["BTC", "SOL", "XRP"]
    assert pick(vols, UniverseRules(mode="rules", top_n=5, include_only=["SOL", "BTC"])) == ["BTC", "SOL"]


OPEN = {"action": "open_long", "size_pct": 10, "leverage": 2, "stop_loss": 90, "take_profit": 130,
        "confidence": 0.9, "reasoning": "以李佛摩的角度，放量突破關鍵點"}


async def test_ai_trader_persona_and_committee(candles):
    prompts = []

    class RecAI(FakeAI):
        async def complete_json(self, system, user, schema):
            prompts.append(system)
            return await super().complete_json(system, user, schema)

    # 任一否決
    ai = RecAI([OPEN, {"verdict": "approve", "reasoning": "理由清楚"}, {"verdict": "veto", "reasoning": "這是蠢事"}])
    s = AIStrategy({"veto_rule": "any"}, ai=ai)
    s.persona = ("李佛摩", builtin_profile("livermore"))
    s.reviewers = [("巴菲特", builtin_profile("buffett")), ("芒格", builtin_profile("munger"))]
    d = await s.run(_ctx(candles))
    assert "最小阻力線" in prompts[0] and "你的交易大腦：李佛摩" in prompts[0]
    assert "審查委員：巴菲特" in prompts[1] and "審查委員：芒格" in prompts[2]
    assert d.action.value == "hold" and "委員會否決：芒格" in d.reasoning
    c = d.meta["committee"]
    assert c["vetoed"] and c["proposed"] == "open_long" and d.meta["persona"] == "李佛摩"

    # 多數決：1 票否決 / 2 票 → 放行
    ai2 = FakeAI([OPEN, {"verdict": "approve", "reasoning": "可"}, {"verdict": "veto", "reasoning": "不可"}])
    s2 = AIStrategy({"veto_rule": "majority"}, ai=ai2)
    s2.reviewers = [("A", "x" * 10), ("B", "y" * 10)]
    d2 = await s2.run(_ctx(candles))
    assert d2.action.value == "open_long" and not d2.meta["committee"]["vetoed"]


async def test_committee_failure_is_veto(candles):
    class Boom(FakeAI):
        calls = 0

        async def complete_json(self, system, user, schema):
            Boom.calls += 1
            if Boom.calls > 1:
                raise RuntimeError("timeout")
            return await super().complete_json(system, user, schema)

    s = AIStrategy({}, ai=Boom([OPEN]))
    s.reviewers = [("芒格", "x")]
    d = await s.run(_ctx(candles))
    assert d.action.value == "hold" and "審查失敗" in d.meta["committee"]["opinions"][0]["reasoning"]


def b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()
