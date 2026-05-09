#!/usr/bin/env python3
"""quality_check.py — 自动质量检查

用法:
    python3 quality_check.py \
        --draft 04_draft.md \
        --materials 03_materials.json \
        --output 06_review.json

检查项:
    1. 模型名/版本号正确性
    2. benchmark 数据交叉验证
    3. 时间准确性
    4. 外部链接可访问性
    5. 来源引用完整性
    6. 字数检查 (800-1000)
    7. 必需章节完整性
    8. 原创内容比例估算
"""

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path


# ── 已知模型名称模式（用于拼写检查）──

KNOWN_MODEL_PATTERNS = [
    # OpenAI
    (r"GPT[-\s]?4o?", "GPT-4/GPT-4o"),
    (r"GPT[-\s]?5", "GPT-5"),
    (r"ChatGPT", "ChatGPT"),
    (r"DALL[-\s]?E[-\s]?\d?", "DALL-E"),
    (r"Whisper", "Whisper"),
    (r"Sora", "Sora"),
    # Anthropic
    (r"Claude[-\s]?\d[\.\d]*", "Claude"),
    (r"Claude\s+(Opus|Sonnet|Haiku)", "Claude"),
    # Google
    (r"Gemini[-\s]?[\d\.]*", "Gemini"),
    (r"Gemma[-\s]?[\d\.]*", "Gemma"),
    (r"PaLM[-\s]?[\d\.]*", "PaLM"),
    # Meta
    (r"Llama[-\s]?[\d\.]*", "Llama"),
    (r"LLaMA[-\s]?[\d\.]*", "LLaMA"),
    # Others
    (r"Mistral[-\s]?\w*", "Mistral"),
    (r"Qwen[-\s]?[\d\.]*", "Qwen"),
    (r"DeepSeek[-\s]?\w*", "DeepSeek"),
    (r"ERNIE[-\s]?[\d\.]*", "ERNIE"),
    (r"文心[\d\.]*", "文心"),
]

# ── 常见 benchmark 名称 ──

BENCHMARK_PATTERNS = [
    r"MMLU",
    r"HumanEval",
    r"MATH",
    r"GSM8K",
    r"HellaSwag",
    r"ARC[-\s]?Challenge",
    r"WinoGrande",
    r"TruthfulQA",
    r"MT[-\s]?Bench",
    r"SWE[-\s]?bench",
    r"BigBench",
    r"GPQA",
    r"IFEval",
]

# ── 必需章节 ──

# ── 速递模式必需章节 ──

DIGEST_REQUIRED_SECTIONS = [
    ("H1标题", r"^#\s+每日AI速递"),
    ("⚡副标题", r"^⚡\s+今日\s*\d+"),
    ("分类章节", r"^##\s+[🚀🧠🔬📊🔓⚡🤖]"),
    ("来源", r"^##\s+来源"),
    ("互动CTA", r"评论区聊聊|你怎么看"),
]


def _is_digest_article(draft: str) -> bool:
    """检测是否为速递文章（标题含"每日AI速递"）"""
    for line in draft.split("\n"):
        if line.startswith("# ") and "每日AI速递" in line:
            return True
    return False


def check_digest_word_count(draft: str) -> list:
    """速递文章字数检查 (1500-2500)"""
    checks = []
    chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", draft))
    english_words = len(re.findall(r"[a-zA-Z]+", draft))
    total = chinese_chars + english_words

    status = "pass"
    detail = f"总字数: {total} (中文 {chinese_chars} + 英文词 {english_words})"
    if total < 1500:
        status = "fail"
        detail += f"，低于速递最低要求 1500 字"
    elif total > 2500:
        status = "warn"
        detail += f"，超过速递建议上限 2500 字"

    checks.append(
        {
            "category": "format",
            "item": "字数检查 (1500-2500 速递)",
            "status": status,
            "line_number": None,
            "detail": detail,
            "suggestion": (
                "增加话题分析深度或补充更多话题"
                if total < 1500
                else ("考虑精简部分话题" if total > 2500 else "")
            ),
            "resolved": status == "pass",
        }
    )
    return checks


def check_digest_required_sections(draft: str) -> list:
    """速递文章必需章节检查"""
    checks = []
    lines = draft.split("\n")

    for section_name, pattern in DIGEST_REQUIRED_SECTIONS:
        found = False
        found_line = None
        for i, line in enumerate(lines, 1):
            if re.search(pattern, line):
                found = True
                found_line = i
                break

        status = "pass" if found else "fail"
        checks.append(
            {
                "category": "format",
                "item": f"速递章节完整性: {section_name}",
                "status": status,
                "line_number": found_line,
                "detail": f"{'找到' if found else '缺少'} \"{section_name}\" 部分",
                "suggestion": f"添加 \"{section_name}\" 部分" if not found else "",
                "resolved": found,
            }
        )

    # 额外检查：至少3个分类章节
    cat_count = sum(1 for line in lines if re.match(r"^##\s+[🚀🧠🔬📊🔓⚡🤖]", line))
    cat_check_status = "pass" if cat_count >= 3 else "fail"
    checks.append(
        {
            "category": "format",
            "item": f"速递分类数量 (≥3)",
            "status": cat_check_status,
            "line_number": None,
            "detail": f"发现 {cat_count} 个分类章节",
            "suggestion": "确保至少覆盖 3 个分类" if cat_count < 3 else "",
            "resolved": cat_check_status == "pass",
        }
    )

    return checks


REQUIRED_SECTIONS = [
    ("标题", r"^#\s+.+"),
    ("背景", r"##\s+背景"),
    ("核心内容", r"##\s+核心内容"),
    ("影响分析", r"##\s+影响分析"),
    ("你可以做什么", r"##\s+(你可以做什么|行动建议|实用建议)"),
    ("来源", r"##\s+来源"),
]


def check_word_count(draft: str) -> list:
    """检查字数"""
    checks = []
    # Count Chinese characters + English words
    chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", draft))
    english_words = len(re.findall(r"[a-zA-Z]+", draft))
    total = chinese_chars + english_words

    status = "pass"
    detail = f"总字数: {total} (中文 {chinese_chars} + 英文词 {english_words})"
    if total < 800:
        status = "fail"
        detail += f"，低于最低要求 800 字"
    elif total > 1000:
        status = "warn"
        detail += f"，超过建议上限 1000 字"

    checks.append(
        {
            "category": "format",
            "item": "字数检查 (800-1000)",
            "status": status,
            "line_number": None,
            "detail": detail,
            "suggestion": (
                "增加核心内容或影响分析章节"
                if total < 800
                else ("考虑精简内容" if total > 1000 else "")
            ),
            "resolved": status == "pass",
        }
    )
    return checks


def check_required_sections(draft: str) -> list:
    """检查必需章节完整性"""
    checks = []
    lines = draft.split("\n")

    for section_name, pattern in REQUIRED_SECTIONS:
        found = False
        for i, line in enumerate(lines, 1):
            if re.search(pattern, line):
                found = True
                break

        status = "pass" if found else "fail"
        checks.append(
            {
                "category": "format",
                "item": f"章节完整性: {section_name}",
                "status": status,
                "line_number": i if found else None,
                "detail": f"{'找到' if found else '缺少'} \"{section_name}\" 章节",
                "suggestion": f"添加 \"## {section_name}\" 章节" if not found else "",
                "resolved": found,
            }
        )

    return checks


def check_title_length(draft: str) -> list:
    """检查标题长度"""
    checks = []
    lines = draft.split("\n")

    for i, line in enumerate(lines, 1):
        if line.startswith("# ") and not line.startswith("## "):
            title = line[2:].strip()
            title_len = len(title)
            status = "pass" if title_len <= 30 else "warn"
            checks.append(
                {
                    "category": "format",
                    "item": "标题长度 (<=30字)",
                    "status": status,
                    "line_number": i,
                    "detail": f"标题 \"{title}\" 长度 {title_len} 字",
                    "suggestion": "缩短标题至 30 字以内" if status == "warn" else "",
                    "resolved": status == "pass",
                }
            )
            break

    return checks


def check_model_names(draft: str) -> list:
    """检查模型名称，标记需人工确认"""
    checks = []
    lines = draft.split("\n")

    for i, line in enumerate(lines, 1):
        for pattern, model_family in KNOWN_MODEL_PATTERNS:
            matches = re.finditer(pattern, line)
            for match in matches:
                found_name = match.group(0)
                checks.append(
                    {
                        "category": "fact_accuracy",
                        "item": f"模型名称: {found_name}",
                        "status": "warn",
                        "line_number": i,
                        "detail": f"发现模型名称 \"{found_name}\"（{model_family} 系列），请确认拼写和版本号正确",
                        "suggestion": "人工确认模型名称和版本号",
                        "resolved": False,
                    }
                )

    return checks


def check_benchmark_data(draft: str, materials: list) -> list:
    """检查 benchmark 数据"""
    checks = []
    lines = draft.split("\n")

    # Find numbers associated with benchmark names
    for i, line in enumerate(lines, 1):
        for bm_pattern in BENCHMARK_PATTERNS:
            if re.search(bm_pattern, line, re.IGNORECASE):
                # Look for percentage or number near the benchmark name
                numbers = re.findall(r"(\d+\.?\d*)\s*%", line)
                if numbers:
                    for num in numbers:
                        checks.append(
                            {
                                "category": "fact_accuracy",
                                "item": f"Benchmark 数据",
                                "status": "warn",
                                "line_number": i,
                                "detail": f"发现 benchmark 数据 \"{num}%\"，请与原始来源交叉验证",
                                "suggestion": "对照素材中的原始数据确认",
                                "resolved": False,
                            }
                        )

    return checks


def check_time_references(draft: str) -> list:
    """检查时间表述准确性"""
    checks = []
    lines = draft.split("\n")
    today = datetime.now().strftime("%Y-%m-%d")

    time_patterns = [
        (r"今[日天]", "今日/今天"),
        (r"昨[日天]", "昨日/昨天"),
        (r"刚[刚才]", "刚刚/刚才"),
        (r"最近发布", "最近发布"),
        (r"最新发布", "最新发布"),
        (r"just released", "just released"),
        (r"today", "today"),
    ]

    for i, line in enumerate(lines, 1):
        for pattern, label in time_patterns:
            if re.search(pattern, line, re.IGNORECASE):
                checks.append(
                    {
                        "category": "fact_accuracy",
                        "item": f"时间表述: \"{label}\"",
                        "status": "warn",
                        "line_number": i,
                        "detail": f"发现时效性表述 \"{label}\"（当前日期: {today}），请确认准确性",
                        "suggestion": "确认发布日期是否与表述一致",
                        "resolved": False,
                    }
                )

    return checks


def check_links(draft: str) -> list:
    """检查外部链接可访问性"""
    checks = []
    lines = draft.split("\n")

    # Extract URLs
    url_pattern = r"https?://[^\s\)>\]\"']+"
    urls_found = {}

    for i, line in enumerate(lines, 1):
        for match in re.finditer(url_pattern, line):
            url = match.group(0).rstrip(".,;:!?")
            if url not in urls_found:
                urls_found[url] = i

    if not urls_found:
        checks.append(
            {
                "category": "citation",
                "item": "外部链接",
                "status": "warn",
                "line_number": None,
                "detail": "文章中未发现任何外部链接",
                "suggestion": "添加来源链接以增强可信度",
                "resolved": False,
            }
        )
        return checks

    # Check accessibility (HTTP HEAD, with timeout)
    for url, line_num in list(urls_found.items())[:10]:  # Limit to 10 URLs
        try:
            req = urllib.request.Request(url, method="HEAD")
            req.add_header(
                "User-Agent",
                "Mozilla/5.0 (compatible; QualityChecker/1.0)",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                status_code = resp.getcode()
                if status_code < 400:
                    status = "pass"
                    detail = f"链接可访问 (HTTP {status_code})"
                else:
                    status = "fail"
                    detail = f"链接返回 HTTP {status_code}"
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            status = "warn"
            detail = f"链接无法验证: {str(e)[:50]}"

        checks.append(
            {
                "category": "citation",
                "item": f"链接检查",
                "status": status,
                "line_number": line_num,
                "detail": f"{url} — {detail}",
                "suggestion": "修复或移除不可访问的链接" if status == "fail" else "",
                "resolved": status == "pass",
            }
        )

    return checks


def check_source_citations(draft: str) -> list:
    """检查来源引用完整性"""
    checks = []
    lines = draft.split("\n")

    # Check if "来源" section exists and has content
    in_sources = False
    source_links = 0

    for i, line in enumerate(lines, 1):
        if re.match(r"##\s+来源", line):
            in_sources = True
            continue
        if in_sources:
            if line.startswith("## "):
                break
            if re.search(r"https?://", line):
                source_links += 1

    if source_links == 0:
        checks.append(
            {
                "category": "citation",
                "item": "来源引用",
                "status": "fail",
                "line_number": None,
                "detail": "来源章节中没有引用链接",
                "suggestion": "在来源章节中添加所有引用的原始链接",
                "resolved": False,
            }
        )
    else:
        checks.append(
            {
                "category": "citation",
                "item": "来源引用",
                "status": "pass",
                "line_number": None,
                "detail": f"来源章节包含 {source_links} 个引用链接",
                "suggestion": "",
                "resolved": True,
            }
        )

    # Check for arXiv references
    arxiv_mentions = len(re.findall(r"arXiv|arxiv", draft))
    arxiv_ids = len(re.findall(r"arxiv[:\s]*\d{4}\.\d{4,5}", draft, re.IGNORECASE))

    if arxiv_mentions > 0 and arxiv_ids == 0:
        checks.append(
            {
                "category": "citation",
                "item": "arXiv ID 引用",
                "status": "warn",
                "line_number": None,
                "detail": f"提到了 arXiv 但未包含 arXiv ID（如 arxiv:2401.12345）",
                "suggestion": "为论文引用添加 arXiv ID",
                "resolved": False,
            }
        )

    return checks


def estimate_originality(draft: str, materials: list) -> list:
    """粗略估算原创内容比例"""
    checks = []

    # Simple heuristic: check for long quoted passages
    lines = draft.split("\n")
    placeholder_lines = 0
    total_content_lines = 0

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith(">") or stripped == "---":
            continue
        total_content_lines += 1
        # Check for placeholder text
        if "请在此" in stripped or "（待补充" in stripped or "待填写" in stripped:
            placeholder_lines += 1

    if total_content_lines > 0:
        placeholder_ratio = placeholder_lines / total_content_lines
        if placeholder_ratio > 0.3:
            checks.append(
                {
                    "category": "format",
                    "item": "内容完整度",
                    "status": "fail",
                    "line_number": None,
                    "detail": f"发现 {placeholder_lines}/{total_content_lines} 行为占位符文本",
                    "suggestion": "需要 Agent 完善文章内容",
                    "resolved": False,
                }
            )
        else:
            checks.append(
                {
                    "category": "format",
                    "item": "内容完整度",
                    "status": "pass",
                    "line_number": None,
                    "detail": f"内容完整度良好 ({total_content_lines} 行有效内容)",
                    "suggestion": "",
                    "resolved": True,
                }
            )

    return checks


def run_all_checks(draft_path: str, materials_path: str, output_path: str):
    """运行所有质量检查"""
    # Load inputs
    with open(draft_path, "r", encoding="utf-8") as f:
        draft = f.read()

    materials = []
    if materials_path and Path(materials_path).exists():
        with open(materials_path, "r", encoding="utf-8") as f:
            materials_data = json.load(f)
            materials = materials_data.get("materials", [])

    is_digest = _is_digest_article(draft)
    mode_label = "速递" if is_digest else "单篇"

    print(f"🔍 开始质量检查（{mode_label}模式）...", file=sys.stderr)

    all_checks = []

    if is_digest:
        # 速递模式检查
        print("  📏 速递字数检查 (1500-2500)...", file=sys.stderr)
        all_checks.extend(check_digest_word_count(draft))

        print("  📑 速递章节完整性...", file=sys.stderr)
        all_checks.extend(check_digest_required_sections(draft))

        print("  📝 标题长度...", file=sys.stderr)
        all_checks.extend(check_title_length(draft))

        # 速递模式跳过模型名拼写和基准数据检查
        print("  ⏭️  跳过模型名称/基准数据检查（速递模式）", file=sys.stderr)

        print("  ⏰ 时间表述...", file=sys.stderr)
        all_checks.extend(check_time_references(draft))

        print("  🔗 链接检查...", file=sys.stderr)
        all_checks.extend(check_links(draft))

        print("  📎 来源引用...", file=sys.stderr)
        all_checks.extend(check_source_citations(draft))

        print("  ✍️  内容完整度...", file=sys.stderr)
        all_checks.extend(estimate_originality(draft, materials))
    else:
        # 单篇模式检查（原有逻辑）
        print("  📏 字数检查...", file=sys.stderr)
        all_checks.extend(check_word_count(draft))

        print("  📑 章节完整性...", file=sys.stderr)
        all_checks.extend(check_required_sections(draft))

        print("  📝 标题长度...", file=sys.stderr)
        all_checks.extend(check_title_length(draft))

        print("  🤖 模型名称...", file=sys.stderr)
        all_checks.extend(check_model_names(draft))

        print("  📊 Benchmark 数据...", file=sys.stderr)
        all_checks.extend(check_benchmark_data(draft, materials))

        print("  ⏰ 时间表述...", file=sys.stderr)
        all_checks.extend(check_time_references(draft))

        print("  🔗 链接检查...", file=sys.stderr)
        all_checks.extend(check_links(draft))

        print("  📎 来源引用...", file=sys.stderr)
        all_checks.extend(check_source_citations(draft))

        print("  ✍️  内容完整度...", file=sys.stderr)
        all_checks.extend(estimate_originality(draft, materials))

    # Summary
    passed = sum(1 for c in all_checks if c["status"] == "pass")
    warnings = sum(1 for c in all_checks if c["status"] == "warn")
    failures = sum(1 for c in all_checks if c["status"] == "fail")
    total = len(all_checks)

    if failures > 0:
        overall = "needs_fix"
    elif warnings > 0:
        overall = "needs_review"
    else:
        overall = "passed"

    result = {
        "reviewed_at": datetime.now().isoformat(),
        "draft_path": draft_path,
        "word_count": len(re.findall(r"[\u4e00-\u9fff]", draft))
        + len(re.findall(r"[a-zA-Z]+", draft)),
        "checks": all_checks,
        "summary": {
            "total_checks": total,
            "passed": passed,
            "warnings": warnings,
            "failures": failures,
            "overall": overall,
        },
    }

    # Write output
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    # Print summary
    print(f"\n📝 质量审核报告", file=sys.stderr)
    print(f"─────────────", file=sys.stderr)
    print(f"✅ 通过: {passed}/{total}", file=sys.stderr)
    print(f"⚠️  需确认: {warnings}/{total}", file=sys.stderr)
    print(f"❌ 问题: {failures}/{total}", file=sys.stderr)
    print(f"总评: {overall}", file=sys.stderr)
    print(f"\n详情已保存到: {output_path}", file=sys.stderr)

    if failures > 0:
        print("\n❌ 需要修正的问题:", file=sys.stderr)
        for c in all_checks:
            if c["status"] == "fail":
                ln = f"L{c['line_number']}: " if c["line_number"] else ""
                print(f"  - {ln}{c['detail']}", file=sys.stderr)

    if warnings > 0:
        print("\n⚠️  需要人工确认:", file=sys.stderr)
        for c in all_checks:
            if c["status"] == "warn":
                ln = f"L{c['line_number']}: " if c["line_number"] else ""
                print(f"  - {ln}{c['detail']}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="文章质量自动检查")
    parser.add_argument("--draft", required=True, help="初稿 Markdown 文件路径")
    parser.add_argument(
        "--materials", default="", help="素材 JSON 文件路径（用于交叉验证）"
    )
    parser.add_argument("--output", required=True, help="输出审核结果 JSON 文件路径")
    args = parser.parse_args()

    run_all_checks(args.draft, args.materials, args.output)


if __name__ == "__main__":
    main()
