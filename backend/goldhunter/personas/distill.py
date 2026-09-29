"""投資大師（Persona）：內建、上傳、系統內蒸餾、保真度評分。

方法論參考 nuwa-skill（https://github.com/alchaincyf/nuwa-skill，MIT）：
把一個人的公開資料提煉成「思維作業系統」——心智模型、決策規則、反模式、誠實邊界、表達方式，
再用獨立的出題 / 作答 / 評分三次 AI 呼叫檢驗保真度（不自評自證）。
本檔案不執行 nuwa-skill 的任何程式，只採用其方法與檔案格式（SKILL.md）。
"""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

from pydantic import BaseModel

from goldhunter.ai.base import AIProvider

BUILTIN_DIR = Path(__file__).parent / "builtin"
MAX_PROFILE_CHARS = 40_000  # 上傳檔案上限（約 1～2 萬 tokens），太長會讓每次 AI 決策變貴

BUILTIN = {
    "livermore": {"name": "傑西·李佛摩", "role": "trader", "markets": ["crypto", "us", "tw"],
                  "summary": "投機、順勢、關鍵點突破、試單加碼、嚴格止損"},
    "soros": {"name": "喬治·索羅斯", "role": "trader", "markets": ["crypto", "us", "tw"],
              "summary": "反身性、總經與流動性、辨識泡沫階段、不對稱下注"},
    "munger": {"name": "查理·芒格", "role": "reviewer", "markets": ["us", "tw"],
               "summary": "逆向思考、人類誤判心理學；在加密市場擔任風險審查員"},
    "buffett": {"name": "華倫·巴菲特", "role": "reviewer", "markets": ["us", "tw"],
                "summary": "價值投資、安全邊際、不用槓桿；在加密市場擔任審查員，美股台股為主力"},
}


def builtin_profile(slug: str) -> str:
    return (BUILTIN_DIR / f"{slug}.md").read_text(encoding="utf-8")


# ---------------------------------------------------------------- 上傳
class ParsedUpload(BaseModel):
    name: str
    description: str
    profile: str
    files: list[str]
    truncated: bool = False


def _frontmatter(text: str) -> tuple[dict, str]:
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.S)
    if not m:
        return {}, text
    meta: dict[str, str] = {}
    key = None
    for line in m.group(1).splitlines():
        kv = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if kv:
            key = kv.group(1)
            meta[key] = kv.group(2).strip().lstrip("|").strip()
        elif key:
            meta[key] = (meta[key] + " " + line.strip()).strip()
    return meta, text[m.end():]


def _title(text: str) -> str | None:
    m = re.search(r"^#\s+(.+)$", text, re.M)
    return m.group(1).strip() if m else None


def parse_upload(filename: str, data: bytes) -> ParsedUpload:
    """接受 nuwa-skill 產出的 SKILL.md，或整個 skill 資料夾壓成的 zip（會一併讀取 references/*.md）。

    上傳內容只當作文字交給 AI 參考，不執行任何程式碼。
    """
    files: list[tuple[str, str]] = []
    if filename.lower().endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for info in zf.infolist():
                n = info.filename
                if info.is_dir() or not n.lower().endswith((".md", ".txt")) or "/." in n or n.startswith("."):
                    continue
                if info.file_size > 2_000_000:
                    continue
                files.append((n, zf.read(info).decode("utf-8", errors="replace")))
        if not files:
            raise ValueError("zip 裡沒有找到 .md 檔")
    else:
        if not filename.lower().endswith((".md", ".txt")):
            raise ValueError("請上傳 SKILL.md（或 .txt），或整個 skill 資料夾壓成的 .zip")
        files.append((filename, data.decode("utf-8", errors="replace")))

    main = next((f for f in files if f[0].split("/")[-1].upper() == "SKILL.MD"), files[0])
    meta, body = _frontmatter(main[1])
    name = _title(body) or meta.get("name") or Path(main[0]).stem
    name = re.split(r"[·|｜:：]", name)[0].strip() or name
    # 主檔全文 + 參考資料（依序附加，直到上限）
    parts = [body.strip()]
    for fname, text in files:
        if fname == main[0] or "FIDELITY" in fname.upper():
            continue
        parts.append(f"\n\n## 參考資料：{fname.split('/')[-1]}\n{text.strip()}")
    profile = ""
    truncated = False
    for p in parts:
        if len(profile) + len(p) > MAX_PROFILE_CHARS:
            truncated = True
            remain = MAX_PROFILE_CHARS - len(profile)
            if remain > 2000 and p is parts[0]:
                profile += p[:remain]
            break
        profile += p
    if len(profile) < 300:
        raise ValueError("內容太短，不像是完整的蒸餾檔（至少需要心智模型與決策規則）")
    return ParsedUpload(name=name, description=meta.get("description", "")[:500], profile=profile,
                        files=[f[0] for f in files], truncated=truncated)


# ---------------------------------------------------------------- 系統內蒸餾
DEPTHS = {
    # 搜尋次數上限、預估輸入 / 輸出 tokens
    "quick": {"label": "快速", "searches": 8, "input": 60_000, "output": 6_000},
    "standard": {"label": "標準", "searches": 20, "input": 160_000, "output": 12_000},
}
# 每百萬 tokens 美元（輸入, 輸出）；web search 每 1,000 次 10 美元
PRICES = {
    "claude-fable-5-1": (10, 50), "claude-fable-5": (10, 50), "claude-opus-5-5": (4, 20), "claude-opus-5": (5, 25),
    "claude-opus-4-8": (5, 25), "claude-sonnet-5": (2, 10), "claude-sonnet-4-6": (3, 15), "claude-haiku-4-5": (1, 5),
}
FIDELITY_TOKENS = (30_000, 6_000)  # 保真度評分三次呼叫合計的粗估


def estimate_cost(provider: str, model: str, depth: str) -> dict:
    d = DEPTHS.get(depth, DEPTHS["standard"])
    price = PRICES.get(model) if provider == "anthropic" else None
    searches = d["searches"] if provider == "anthropic" else 0
    tin, tout = d["input"] + FIDELITY_TOKENS[0], d["output"] + FIDELITY_TOKENS[1]
    usd = None
    if price:
        usd = round(tin / 1e6 * price[0] + tout / 1e6 * price[1] + searches / 1000 * 10, 2)
    return {
        "depth": depth, "depth_label": d["label"], "input_tokens": tin, "output_tokens": tout,
        "web_searches": searches, "usd": usd,
        "note": ("含網路搜尋調研與保真度評分的粗估，實際依來源長度而定（可能 ±50%）"
                 if provider == "anthropic" else
                 "此供應商不支援網路搜尋，只能用模型本身的知識蒸餾；費用依該供應商價格計算"),
    }


DISTILL_SYSTEM = """你是投資與交易領域的人物研究員，採用「思維蒸餾」方法：捕捉此人「怎麼想」，而不是「說過什麼」。
輸出一份給 AI 交易員使用的「交易思維作業系統」Markdown，章節固定如下：
# 〈人名〉· 交易思維作業系統
> 一句說明：依哪些公開資料提煉、非本人觀點
## 身份卡（第一人稱，50 字內）
## 核心心智模型（3～6 個，每個含：一句話、應用、局限）
## 決策規則（5～8 條，具體可執行）
## 在加密貨幣永續合約上的應用（此人若不適合交易加密貨幣或槓桿商品，要明說，並建議改當審查員）
## 反模式（絕不做）
## 誠實邊界（至少 3 條：他不懂什麼、方法在什麼情況失效）
## 決策表達（句式、常用語）
要求：只根據可查證的公開資料；沒有公開表態的領域要標註「框架推斷」；用繁體中文。"""


async def distill(ai: AIProvider, name: str, depth: str = "standard", corpus: str | None = None) -> str:
    d = DEPTHS.get(depth, DEPTHS["standard"])
    user = f"請蒸餾：{name}\n聚焦在他的投資 / 交易 / 風險管理思維。"
    if corpus:
        user += f"\n\n以下是使用者提供的一手資料，請優先使用：\n{corpus[:60_000]}"
    text = await ai.research_text(DISTILL_SYSTEM, user, max_searches=d["searches"])
    m = re.search(r"(#\s+.+)", text, re.S)
    return (m.group(1) if m else text).strip()


# ---------------------------------------------------------------- 保真度評分
class FidelityReport(BaseModel):
    score: int
    grade: str
    passed: bool
    dimensions: list[dict]  # {name, score, max, reason}
    questions: list[dict]  # {question, type, expected, answer}
    summary: str


EXAM_SYSTEM = """你是出題官，要測試一份「人物交易思維檔案」像不像本人。根據你對此人公開資料的了解出 5 題：
- 3 題 stance：此人公開反覆表態過的交易 / 投資問題，附上他真實的立場（expected）
- 1 題 out_of_scope：此人從未公開談過的領域問題（expected 寫「應標註為推斷並保留不確定性」）
- 1 題 scenario：一個加密貨幣永續合約的具體行情情境，問他會怎麼做（expected 寫此人框架下合理的做法）
只輸出 JSON。"""
EXAM_SCHEMA = {
    "type": "object",
    "properties": {"questions": {"type": "array", "items": {
        "type": "object",
        "properties": {"type": {"type": "string", "enum": ["stance", "out_of_scope", "scenario"]},
                       "question": {"type": "string"}, "expected": {"type": "string"}},
        "required": ["type", "question", "expected"], "additionalProperties": False}}},
    "required": ["questions"], "additionalProperties": False,
}
ANSWER_SCHEMA = {
    "type": "object",
    "properties": {"answers": {"type": "array", "items": {"type": "string"}}},
    "required": ["answers"], "additionalProperties": False,
}
GRADE_SYSTEM = """你是獨立評分官，依評分卡評估「人物交易思維檔案」的保真度（總分 100）：
1. 立場一致性（30）：3 題 stance，回答方向與此人真實立場是否一致（每題 10：方向與細節都對 10、方向對細節偏 6、偏離 0）
2. 風格辨識度（20）：不看名字能否從表達認出是誰
3. 邊緣誠實度（20）：超範圍題是否標註推斷、保留不確定性（斬釘截鐵編造＝0）
4. 情境合理性（15）：交易情境題的做法是否符合此人框架與風險原則
5. 結構完整度（15）：檔案是否有心智模型 3～6、決策規則、反模式、誠實邊界 ≥3
逐維給分並說明理由（繁體中文）。只輸出 JSON。"""
GRADE_SCHEMA = {
    "type": "object",
    "properties": {
        "dimensions": {"type": "array", "items": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "score": {"type": "number"}, "max": {"type": "number"},
                           "reason": {"type": "string"}},
            "required": ["name", "score", "max", "reason"], "additionalProperties": False}},
        "summary": {"type": "string"},
    },
    "required": ["dimensions", "summary"], "additionalProperties": False,
}
PASS_SCORE = 70


def _grade(score: int) -> str:
    return "A" if score >= 85 else "B" if score >= 70 else "C" if score >= 55 else "D"


async def fidelity(ai: AIProvider, name: str, profile: str) -> FidelityReport:
    """出題 / 作答 / 評分 三次獨立呼叫；作答時只看得到檔案內容"""
    exam = (await ai.complete_json(EXAM_SYSTEM, f"人物：{name}", EXAM_SCHEMA)).decision.get("questions", [])[:5]
    qs = "\n".join(f"{i + 1}. {q['question']}" for i, q in enumerate(exam))
    answer_system = f"以下是你的思維檔案。以此人第一人稱、依檔案中的框架回答每一題；檔案沒涵蓋的要標註為推斷。\n\n{profile}"
    answers = (await ai.complete_json(answer_system, f"請依序回答，每題一段：\n{qs}", ANSWER_SCHEMA)
               ).decision.get("answers", [])
    items = [{**q, "answer": answers[i] if i < len(answers) else ""} for i, q in enumerate(exam)]
    grade_input = (f"人物：{name}\n\n## 題目、真實立場與作答\n"
                   + "\n\n".join(f"[{q['type']}] {q['question']}\n真實立場：{q['expected']}\n作答：{q['answer']}"
                                 for q in items)
                   + f"\n\n## 檔案全文（供結構檢查）\n{profile[:20_000]}")
    g = (await ai.complete_json(GRADE_SYSTEM, grade_input, GRADE_SCHEMA)).decision
    dims = g.get("dimensions", [])
    total = int(round(sum(min(float(d.get("score", 0)), float(d.get("max", 0))) for d in dims)))
    total = max(0, min(100, total))
    return FidelityReport(score=total, grade=_grade(total), passed=total >= PASS_SCORE, dimensions=dims,
                          questions=items, summary=g.get("summary", ""))
