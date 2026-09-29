"""讀取 nuwa-skill（女媧）蒸餾出的人物檔案，轉成 AI 交易員的「交易大腦」。

女媧輸出格式（https://github.com/alchaincyf/nuwa-skill，MIT）：
  〈人物〉-perspective/
    SKILL.md        YAML frontmatter + 「〈人名〉 · 思维操作系统」：身份卡、核心心智模型、决策启发式、表达DNA、
                    价值观与反模式、诚实边界、内在张力…；另含角色扮演規則、Agentic 研究流程等對話用段落
    FIDELITY.md     保真度評分卡（總分 NN/100 · 等級 X，五個維度）
    references/     調研底稿

交易時只需要「怎麼想、怎麼決策」的段落，所以：
- 保留：身份卡、擅長與局限 / 使用說明、核心心智模型、決策啟發式、價值觀與反模式、反例黑名單、誠實邊界、內在張力、表達 DNA
- 捨棄：角色扮演規則、回答工作流（Agentic Protocol，會叫模型上網搜尋）、CHECKPOINT、失敗模式、示例對話、調研來源
  （捨棄的段落不影響判斷，還能讓每次 AI 決策少送幾千個 token）
上傳內容只當作文字交給 AI 參考，不執行任何程式。
"""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import PurePosixPath

from pydantic import BaseModel

MAX_PROFILE_CHARS = 30_000

KEEP = ("身份卡", "擅长", "擅長", "局限", "使用说明", "使用說明", "心智模型", "决策启发", "決策啟發", "决策规则", "決策規則",
        "价值观", "價值觀", "反模式", "反例", "诚实边界", "誠實邊界", "内在张力", "內在張力", "表达DNA", "表達DNA",
        "交易", "投资", "投資", "风险", "風險")
DROP = ("角色扮演", "回答工作流", "Agentic", "CHECKPOINT", "失败模式", "失敗模式", "Fallback", "示例", "範例",
        "调研", "調研", "来源", "來源", "附录", "附錄", "更新", "智识谱系", "智識譜系")


class FidelityInfo(BaseModel):
    score: int
    grade: str
    dimensions: list[dict] = []  # {name, score, max, reason}
    tested_at: str | None = None
    summary: str = ""


class ParsedPersona(BaseModel):
    name: str
    description: str
    profile: str  # 交易用的精簡檔案
    raw_skill: str  # 原始 SKILL.md（供檢視）
    kept_sections: list[str]
    dropped_sections: list[str]
    fidelity: FidelityInfo | None
    files: list[str]
    truncated: bool = False


def _frontmatter(text: str) -> tuple[dict, str]:
    m = re.match(r"^﻿?---\s*\n(.*?)\n---\s*\n", text, re.S)
    if not m:
        return {}, text
    meta: dict[str, str] = {}
    key = None
    for line in m.group(1).splitlines():
        kv = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if kv:
            key = kv.group(1)
            meta[key] = kv.group(2).strip().lstrip("|>").strip()
        elif key:
            meta[key] = (meta[key] + " " + line.strip()).strip()
    return meta, text[m.end():]


def _split_sections(body: str) -> tuple[str, list[tuple[str, str]]]:
    """回傳（標題行與前言, [(二級標題, 內容)]）"""
    parts = re.split(r"(?m)^## ", body)
    head = parts[0]
    sections = []
    for p in parts[1:]:
        title, _, content = p.partition("\n")
        sections.append((title.strip(), content))
    return head, sections


def _keep(title: str) -> bool:
    if any(k.lower() in title.lower() for k in DROP):
        return False
    return any(k.lower() in title.lower() for k in KEEP)


def parse_fidelity(text: str) -> FidelityInfo | None:
    m = re.search(r"(?:总分|總分|Total)[^0-9]{0,6}(\d{1,3})\s*/\s*100[^A-Da-d]{0,12}([A-Da-d])?", text)
    if not m:
        return None
    score = int(m.group(1))
    grade = (m.group(2) or ("A" if score >= 85 else "B" if score >= 70 else "C" if score >= 55 else "D")).upper()
    dims = []
    for row in re.finditer(r"^\|\s*([^|\n]+?)\s*\|\s*(\d+)\s*/\s*(\d+)\s*\|\s*([^|\n]*)\|", text, re.M):
        dims.append({"name": row.group(1).strip(), "score": int(row.group(2)), "max": int(row.group(3)),
                     "reason": row.group(4).strip()})
    date = re.search(r"(?:测试日期|測試日期)[：:]\s*([0-9-]{8,10})", text)
    judge = re.search(r"^>\s*(.+)$", text, re.M)
    return FidelityInfo(score=score, grade=grade, dimensions=dims, tested_at=date.group(1) if date else None,
                        summary=judge.group(1).strip() if judge else "")


def _read_files(filename: str, data: bytes) -> list[tuple[str, str]]:
    if filename.lower().endswith(".zip"):
        out = []
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for info in zf.infolist():
                n = info.filename
                name = PurePosixPath(n).name
                if info.is_dir() or name.startswith(".") or "__MACOSX" in n:
                    continue
                if not name.lower().endswith((".md", ".txt")) or info.file_size > 3_000_000:
                    continue  # 只讀文字檔；程式、圖片一律忽略
                out.append((n, zf.read(info).decode("utf-8", errors="replace")))
        if not out:
            raise ValueError("zip 裡沒有找到 .md 檔")
        return out
    if not filename.lower().endswith((".md", ".txt")):
        raise ValueError("請上傳女媧產出的 SKILL.md，或整個人物資料夾壓成的 .zip")
    return [(filename, data.decode("utf-8", errors="replace"))]


def parse_nuwa(filename: str, data: bytes, fidelity_text: str | None = None) -> ParsedPersona:
    files = _read_files(filename, data)
    skill = next((f for f in files if PurePosixPath(f[0]).name.upper() == "SKILL.MD"), None)
    if skill is None:
        skill = next((f for f in files if "FIDELITY" not in f[0].upper()), files[0])
    fid_file = next((f for f in files if PurePosixPath(f[0]).name.upper() == "FIDELITY.MD"), None)
    fidelity = parse_fidelity(fidelity_text or (fid_file[1] if fid_file else ""))

    meta, body = _frontmatter(skill[1])
    head, sections = _split_sections(body)
    title = re.search(r"^#\s+(.+)$", head, re.M)
    raw_name = (title.group(1) if title else meta.get("name", PurePosixPath(skill[0]).stem)).strip()
    name = re.split(r"\s*[·|｜:：]\s*", raw_name)[0].strip() or raw_name
    if name.endswith("-perspective"):
        name = name[: -len("-perspective")]

    kept, dropped, chunks = [], [], []
    quote = re.search(r"^>\s*.+$", head, re.M)
    header = f"# {name} · 交易思維檔案（由女媧 nuwa-skill 蒸餾）\n"
    if quote:
        header += quote.group(0) + "\n"
    for t, content in sections:
        if _keep(t):
            kept.append(t)
            chunks.append(f"## {t}\n{content.strip()}\n")
        else:
            dropped.append(t)
    if not kept:  # 不是女媧格式：整份當作思維檔案
        chunks = [body]
    profile = header + "\n" + "\n".join(chunks)
    truncated = len(profile) > MAX_PROFILE_CHARS
    if truncated:
        profile = profile[:MAX_PROFILE_CHARS] + "\n…（內容過長已截斷）"
    if len(profile) < 300:
        raise ValueError("內容太短，不像女媧蒸餾出的完整人物檔案（至少需要心智模型與決策啟發式）")
    return ParsedPersona(name=name, description=meta.get("description", "")[:600], profile=profile,
                         raw_skill=skill[1][:200_000], kept_sections=kept, dropped_sections=dropped,
                         fidelity=fidelity, files=[f[0] for f in files][:100], truncated=truncated)
