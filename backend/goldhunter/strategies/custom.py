"""自訂 Python 策略（手寫或由 Pine Script 轉換而來）的載入與安全檢查。

安全檢查（AST 白名單）會擋掉：非白名單 import、檔案/網路/系統存取、eval/exec、
以及任何「_」開頭的屬性存取（避免透過 __class__ / __globals__ 逃逸）。
注意：這是防呆與防誤用，不是作業系統等級的沙箱；啟用前請務必自行檢視程式碼。
"""

from __future__ import annotations

import ast
import builtins
import math
import statistics

from goldhunter.strategies import ta
from goldhunter.strategies.base import Strategy, StrategyContext

ALLOWED_IMPORTS = {"math", "statistics", "goldhunter.strategies.sdk"}
FORBIDDEN_NAMES = {
    "eval", "exec", "compile", "open", "__import__", "globals", "locals", "vars", "getattr", "setattr",
    "delattr", "input", "breakpoint", "help", "memoryview", "exit", "quit", "type", "object", "super",
    "classmethod", "staticmethod", "property", "dir", "id",
}
SAFE_BUILTINS = {
    k: getattr(builtins, k)
    for k in (
        "abs all any bool dict enumerate filter float int isinstance len list map max min pow range "
        "reversed round set sorted str sum tuple zip None True False ValueError ZeroDivisionError"
    ).split()
    if hasattr(builtins, k)
}


class StrategyCodeError(ValueError):
    pass


def validate_code(code: str) -> list[str]:
    """回傳錯誤訊息清單；空清單代表通過。"""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return [f"語法錯誤（第 {e.lineno} 行）：{e.msg}"]
    errors: list[str] = []
    has_strategy = False
    for node in ast.walk(tree):
        line = getattr(node, "lineno", "?")
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name not in ALLOWED_IMPORTS:
                    errors.append(f"第 {line} 行：不允許 import {a.name}")
        elif isinstance(node, ast.ImportFrom):
            if node.module not in ALLOWED_IMPORTS or node.level:
                errors.append(f"第 {line} 行：不允許 from {node.module} import")
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            errors.append(f"第 {line} 行：不允許使用 {node.id}")
        elif isinstance(node, ast.Name) and node.id.startswith("__"):
            errors.append(f"第 {line} 行：不允許使用 {node.id}")
        elif isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            errors.append(f"第 {line} 行：不允許存取私有屬性 .{node.attr}")
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            errors.append(f"第 {line} 行：不允許 global / nonlocal")
        elif isinstance(node, ast.ClassDef) and node.name == "UserStrategy":
            has_strategy = True
    if not has_strategy:
        errors.append("找不到 class UserStrategy(Strategy)")
    return errors


def _safe_import(name, globals=None, locals=None, fromlist=(), level=0):
    if name not in ALLOWED_IMPORTS:
        raise ImportError(f"不允許 import {name}")
    return __import__(name, globals, locals, fromlist, level)


def load_strategy_class(code: str) -> type[Strategy]:
    errors = validate_code(code)
    if errors:
        raise StrategyCodeError("；".join(errors))
    env_builtins = dict(SAFE_BUILTINS)
    env_builtins["__import__"] = _safe_import
    env_builtins["__build_class__"] = builtins.__build_class__
    namespace: dict = {
        "__builtins__": env_builtins,
        "__name__": "user_strategy",
        "Strategy": Strategy,
        "StrategyContext": StrategyContext,
        "ta": ta,
        "math": math,
        "statistics": statistics,
    }
    exec(compile(code, "<user_strategy>", "exec"), namespace)  # noqa: S102 — 已通過 AST 白名單檢查
    cls = namespace.get("UserStrategy")
    if not (isinstance(cls, type) and issubclass(cls, Strategy)):
        raise StrategyCodeError("UserStrategy 必須繼承 Strategy")
    return cls


TEMPLATE = '''from goldhunter.strategies.sdk import Strategy, ta


class UserStrategy(Strategy):
    name = "my_strategy"
    description = "範例：價格突破 20 根高點做多"
    default_params = {"length": 20, "size_pct": 10, "stop_pct": 2.0}
    warmup = 25

    def on_bar(self, ctx):
        hh = ta.highest(ctx.high, self.p("length"))
        if ctx.position_size == 0 and ctx.price > hh[-2]:
            return ctx.long(self.p("size_pct"), stop_loss=ctx.price * (1 - self.p("stop_pct") / 100),
                            reasoning="突破前高")
        return None
'''
