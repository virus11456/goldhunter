import pytest

from goldhunter.ai.base import AIProvider, AIResult
from goldhunter.strategies.custom import TEMPLATE, StrategyCodeError, load_strategy_class, validate_code
from goldhunter.tradingview.pine_converter import convert_pine, extract_code, extract_notes


def test_template_loads():
    cls = load_strategy_class(TEMPLATE)
    assert cls.name == "my_strategy"


@pytest.mark.parametrize(
    "snippet,expect",
    [
        ("import os", "import os"),
        ("from subprocess import run", "subprocess"),
        ("x = open('/etc/passwd')", "open"),
        ("x = ().__class__.__bases__", "__class__"),
        ("x = eval('1')", "eval"),
        ("x = getattr(1, 'real')", "getattr"),
        ("x = __builtins__", "__builtins__"),
    ],
)
def test_validator_blocks_dangerous_code(snippet, expect):
    code = TEMPLATE + "\n" + snippet + "\n"
    errors = validate_code(code)
    assert errors and any(expect in e for e in errors)
    with pytest.raises(StrategyCodeError):
        load_strategy_class(code)


def test_whitelisted_import_allowed():
    code = TEMPLATE.replace("from goldhunter.strategies.sdk import Strategy, ta",
                            "from goldhunter.strategies.sdk import Strategy, ta\nimport math")
    assert load_strategy_class(code)


def test_missing_class():
    assert "找不到 class UserStrategy(Strategy)" in validate_code("x = 1\n")


PINE = """//@version=5
strategy("MA", overlay=true)
fast = ta.ema(close, 10)
slow = ta.ema(close, 30)
if ta.crossover(fast, slow)
    strategy.entry("L", strategy.long)
if ta.crossunder(fast, slow)
    strategy.close("L")
"""

BAD = "```python\nimport os\n" + TEMPLATE + "```"
GOOD = """```python
# 未轉換項目：
# - plot 繪圖
from goldhunter.strategies.sdk import Strategy, ta


class UserStrategy(Strategy):
    name = "ma"
    description = "EMA 交叉"
    default_params = {"fast": 10, "slow": 30, "size_pct": 10}
    warmup = 40

    def on_bar(self, ctx):
        f, s = ta.ema(ctx.close, self.p("fast")), ta.ema(ctx.close, self.p("slow"))
        if ta.crossover(f, s) and ctx.position_size == 0:
            return ctx.long(self.p("size_pct"), reasoning="cross up")
        if ta.crossunder(f, s) and ctx.position_size > 0:
            return ctx.close_position("cross down")
        return None
```"""


class FakeAI(AIProvider):
    provider = "fake"

    def __init__(self, replies):
        super().__init__("fake")
        self.replies = list(replies)
        self.prompts = []

    async def complete_json(self, system, user, schema):
        return AIResult(decision=self.replies.pop(0), raw_text="", model="fake")

    async def complete_text(self, system, user, max_tokens=16000):
        self.prompts.append(user)
        return self.replies.pop(0)


async def test_pine_conversion_retries_on_unsafe_code():
    ai = FakeAI([BAD, GOOD])
    r = await convert_pine(ai, PINE)
    assert r.ok and r.attempts == 2
    assert "不允許 import os" in ai.prompts[1]  # 錯誤有回饋給模型
    assert r.notes == ["plot 繪圖"]
    assert load_strategy_class(r.code).default_params["fast"] == 10


async def test_pine_conversion_gives_up():
    r = await convert_pine(FakeAI([BAD, BAD]), PINE, max_attempts=2)
    assert not r.ok and r.errors


def test_extract_helpers():
    assert extract_code("text\n```python\nx=1\n```\nmore") == "x=1\n"
    assert extract_notes("# 未轉換項目：\n# - a\n# - b\ncode") == ["a", "b"]
