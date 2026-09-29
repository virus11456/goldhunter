"""投資大師：讀取女媧（nuwa-skill）蒸餾檔案、保真度評分卡、大師組合計畫、AI 交易員大腦、標的範圍規則"""

import base64
import io
import zipfile

import pytest

from goldhunter.engine.universe import UniverseRules, pick
from goldhunter.personas.nuwa import parse_fidelity, parse_nuwa
from goldhunter.personas.portfolio import clamp_plan, design_plan, plan_to_bot_fields
from goldhunter.strategies.ai_strategy import AIStrategy

from .test_backtest_and_ai import _ctx
from .test_custom_strategy import FakeAI

# 女媧輸出格式（簡體），含對話用段落（應被捨棄）
SKILL_MD = """---
name: test-trader-perspective
description: |
  测试交易员的思维框架。用途：思维顾问。
---

# 测试交易员 · 思维操作系统

> 「趋势是你唯一的朋友。」

## 角色扮演规则
用第一人称回答，不要跳出角色。

## 回答工作流（Agentic Protocol）
遇到事实问题先上网搜索。

## 身份卡
一个只做趋势的投机者。""" + "补充说明。" * 60 + """

## 核心心智模型
### 模型1：顺势
一句话：只做趋势方向。
### 模型2：小亏大赚
一句话：止损要小、让获利奔跑。

## 决策启发式
1. 突破才进场
2. 亏损 5% 出场

## 价值观与反模式
- 摊平是大忌

## 诚实边界
- 盘整期表现差
- 不懂基本面

## 调研来源
- 1998 年的访谈
"""

FIDELITY_MD = """# 测试交易员 · 保真度评分卡

测试日期：2026-09-01

**总分：82/100 · 等级 B**

| 维度 | 得分 | 说明 |
|---|---|---|
| 立场一致性 | 26/30 | 大部分一致 |
| 风格辨识度 | 16/20 | 口吻接近 |
| 边缘诚实度 | 15/20 | 偶尔过度自信 |

> 整体像本人，但对冷门问题略显武断。
"""


def _zip(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n, t in files.items():
            z.writestr(n, t)
    return buf.getvalue()


def test_parse_nuwa_skill_md_keeps_thinking_drops_dialogue():
    p = parse_nuwa("SKILL.md", SKILL_MD.encode())
    assert p.name == "测试交易员" and "思维框架" in p.description
    assert "顺势" in p.profile and "摊平是大忌" in p.profile and "盘整期表现差" in p.profile
    assert "趋势是你唯一的朋友" in p.profile
    assert "第一人称" not in p.profile and "上网搜索" not in p.profile and "1998" not in p.profile
    assert "核心心智模型" in p.kept_sections and any("角色扮演" in d for d in p.dropped_sections)
    assert p.fidelity is None


def test_parse_nuwa_zip_reads_fidelity_and_ignores_code():
    data = _zip({"test-trader-perspective/SKILL.md": SKILL_MD,
                 "test-trader-perspective/FIDELITY.md": FIDELITY_MD,
                 "test-trader-perspective/references/research.md": "## 调研\n资料",
                 "test-trader-perspective/scripts/evil.py": "import os; os.system('rm -rf /')",
                 "__MACOSX/._SKILL.md": "junk"})
    p = parse_nuwa("test-trader.zip", data)
    assert p.name == "测试交易员" and "os.system" not in p.profile
    assert not any(f.endswith(".py") or "__MACOSX" in f for f in p.files)
    f = p.fidelity
    assert f and f.score == 82 and f.grade == "B" and f.tested_at == "2026-09-01"
    assert f.dimensions[0] == {"name": "立场一致性", "score": 26, "max": 30, "reason": "大部分一致"}
    assert "略显武断" in f.summary


def test_parse_nuwa_errors_and_fallback():
    with pytest.raises(ValueError):
        parse_nuwa("x.pdf", b"abc")
    with pytest.raises(ValueError):
        parse_nuwa("SKILL.md", b"# too short")
    with pytest.raises(ValueError):
        parse_nuwa("a.zip", _zip({"a.py": "print(1)"}))
    # 非女媧格式：整份當作思維檔案
    p = parse_nuwa("notes.md", ("# 我的筆記\n## 隨想\n" + "趨勢交易心得。" * 80).encode())
    assert "趨勢交易心得" in p.profile and p.name == "我的筆記"


def test_name_with_middle_dot():
    text = SKILL_MD.replace("# 测试交易员 · 思维操作系统", "# 傑西·李佛摩 · 思维操作系统")
    assert parse_nuwa("SKILL.md", text.encode()).name == "傑西·李佛摩"
    text = SKILL_MD.replace("# 测试交易员 · 思维操作系统", "# 喬治·索羅斯思維操作系統")
    assert parse_nuwa("SKILL.md", text.encode()).name == "喬治·索羅斯"


def test_parse_fidelity_variants():
    assert parse_fidelity("總分 91 / 100").grade == "A"
    assert parse_fidelity("Total: 64/100 · Grade C").score == 64
    assert parse_fidelity("沒有分數") is None


def test_clamp_plan_limits():
    p = clamp_plan({"timeframe": "3m", "max_leverage": 50, "position_pct": 90, "max_positions": 0,
                    "universe": {"mode": "list", "symbols": ["btc/usdt", "eth"], "top_n": 999},
                    "entry_mode": "weird", "allow_short": False, "instructions": "x" * 2000})
    assert p.timeframe == "4h" and p.max_leverage == 10 and p.position_pct == 50 and p.max_positions == 3
    assert p.universe.mode == "list" and p.universe.symbols == ["BTC", "ETH"] and p.universe.top_n == 50
    assert p.entry_mode == "market" and len(p.instructions) == 600
    f = plan_to_bot_fields(p)
    assert f["symbols"] == ["crypto:BTC/USDT:perp", "crypto:ETH/USDT:perp"] and f["universe"] == {"mode": "list"}
    assert f["risk"]["long_only"] and f["risk"]["max_leverage"] == 10 and f["risk"]["max_positions"] == 3
    # list 模式但沒給幣 → 改用規則
    assert clamp_plan({"universe": {"mode": "list", "symbols": []}}).universe.mode == "rules"


async def test_design_plan_uses_profile():
    seen = []

    class Rec(FakeAI):
        async def complete_json(self, system, user, schema):
            seen.append(system)
            return await super().complete_json(system, user, schema)

    ai = Rec([{"suitable": True, "style_summary": "順勢突破", "reason": "最小阻力線", "timeframe": "1d",
               "holding_period": "數週", "universe": {"mode": "rules", "top_n": 5, "exclude_meme": True,
                                                    "include_only": [], "symbols": []},
               "max_positions": 2, "position_pct": 20, "max_leverage": 3, "allow_short": True,
               "entry_mode": "smart", "instructions": "只在突破時進場"}])
    p = await design_plan(ai, "测试交易员", parse_nuwa("SKILL.md", SKILL_MD.encode()).profile)
    assert p.timeframe == "1d" and p.entry_mode == "smart" and "只做趋势方向" in seen[0]
    f = plan_to_bot_fields(p)
    assert f["universe"]["top_n"] == 5 and f["entry"] == {"mode": "smart"} and f["interval_sec"] == 600


def test_universe_pick():
    vols = [("BTC", 9e9), ("ETH", 5e9), ("DOGE", 3e9), ("SOL", 2e9), ("PEPE", 1e9), ("XRP", 8e8), ("LUNA", 7e8)]
    assert pick(vols, UniverseRules(mode="rules", top_n=3)) == ["BTC", "ETH", "SOL"]
    assert pick(vols, UniverseRules(mode="rules", top_n=3, exclude_meme=False)) == ["BTC", "ETH", "DOGE"]
    assert pick(vols, UniverseRules(mode="rules", top_n=5, exclude=["ETH", "luna"])) == ["BTC", "SOL", "XRP"]
    assert pick(vols, UniverseRules(mode="rules", top_n=5, include_only=["SOL", "BTC"])) == ["BTC", "SOL"]


OPEN = {"action": "open_long", "size_pct": 10, "leverage": 2, "stop_loss": 90, "take_profit": 130,
        "confidence": 0.9, "reasoning": "放量突破關鍵點"}


async def test_ai_trader_uses_persona_as_brain(candles):
    prompts = []

    class RecAI(FakeAI):
        async def complete_json(self, system, user, schema):
            prompts.append(system)
            return await super().complete_json(system, user, schema)

    s = AIStrategy({}, ai=RecAI([OPEN]))
    s.persona = ("测试交易员", parse_nuwa("SKILL.md", SKILL_MD.encode()).profile)
    d = await s.run(_ctx(candles))
    assert len(prompts) == 1  # 沒有委員會，只問一次
    assert "你的交易大腦：测试交易员" in prompts[0] and "小亏大赚" in prompts[0]
    assert d.action.value == "open_long" and d.meta["persona"] == "测试交易员"


def b64(text: str | bytes) -> str:
    return base64.b64encode(text.encode() if isinstance(text, str) else text).decode()
