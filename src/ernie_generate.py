#!/usr/bin/env python3
"""ernie_generate.py -- Generate article draft using ERNIE 4.0 API.

Handles article writing via ERNIE API call.
Produces 04_draft.md in the same format as the existing pipeline.

Usage:
    python3 ernie_generate.py \
        --materials 03_materials.json \
        --topic 02_topic_selected.json \
        --output 04_draft.md \
        [--correction "修改意见"]

Environment:
    ERNIE_API_KEY       百度智能云 AK
    ERNIE_SECRET_KEY    百度智能云 SK
"""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path


ERNIE_TOKEN_URL = "https://aip.baidubce.com/oauth/2.0/token"
ERNIE_CHAT_URL = (
    "https://aip.baidubce.com/rpc/2.0/ai_custom/v1/"
    "wenxinworkshop/chat/ernie-4.0-8k"
)

# Token cache
_TOKEN_CACHE = {"token": None, "expires_at": 0}


def get_ernie_token(api_key: str, secret_key: str) -> str:
    """Get ERNIE access_token via OAuth.

    POST https://aip.baidubce.com/oauth/2.0/token
        ?grant_type=client_credentials&client_id={AK}&client_secret={SK}
    """
    import time

    now = time.time()
    if _TOKEN_CACHE["token"] and now < _TOKEN_CACHE["expires_at"] - 60:
        return _TOKEN_CACHE["token"]

    url = (
        f"{ERNIE_TOKEN_URL}"
        f"?grant_type=client_credentials"
        f"&client_id={api_key}"
        f"&client_secret={secret_key}"
    )

    req = urllib.request.Request(url, method="POST", data=b"")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"ERNIE token request failed: {e}", file=sys.stderr)
        raise

    token = data.get("access_token", "")
    if not token:
        raise RuntimeError(f"ERNIE token error: {data}")

    expires_in = data.get("expires_in", 86400)
    _TOKEN_CACHE["token"] = token
    _TOKEN_CACHE["expires_at"] = now + expires_in
    print(f"[ernie] token obtained, expires in {expires_in}s", file=sys.stderr)
    return token


def call_ernie(access_token: str, system_prompt: str, user_prompt: str) -> str:
    """Call ERNIE 4.0 chat API.

    Returns the generated text content.
    """
    url = f"{ERNIE_CHAT_URL}?access_token={access_token}"

    payload = json.dumps({
        "messages": [
            {"role": "user", "content": user_prompt},
        ],
        "system": system_prompt,
        "temperature": 0.7,
        "top_p": 0.9,
    }, ensure_ascii=False).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"ERNIE API HTTP {e.code}: {body}", file=sys.stderr)
        raise
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"ERNIE API request failed: {e}", file=sys.stderr)
        raise

    if "error_code" in data:
        raise RuntimeError(f"ERNIE API error: {data}")

    return data.get("result", "")


def build_system_prompt() -> str:
    """Build the system prompt for ERNIE article generation."""
    return (
        '你是 "AI 每日 10 分钟" 公众号的技术编辑。'
        "请基于以下素材撰写一篇面向 AI 从业者的科技新闻文章。\n\n"
        "## 写作要求\n\n"
        "1. 目标读者：AI 从业者（工程师、研究者、产品经理）\n"
        "2. 阅读时间：3 分钟快读（800-1000 字）\n"
        "3. 语言风格：专业但不枯燥，数据驱动，避免过度营销语言\n"
        "4. 原创要求：原创内容 >= 60%\n"
        "5. 引用规范：每个数据点标注来源，论文引用 arXiv ID，所有外部链接列入来源章节\n"
        "6. 模型名称/版本号必须准确，benchmark 数据必须与原始来源一致\n\n"
        "## 文章结构（严格按此输出）\n\n"
        "1. 标题（不超过 30 字，用 # 开头）\n"
        "2. TL;DR（2 句话，用 > 引用块，不要写 TL;DR 字样）\n"
        "3. ## 背景（~100 字）\n"
        "4. ## 核心内容（~400 字）\n"
        "5. ## 影响分析（~150 字）\n"
        "6. ## 你可以做什么（2-3 条建议，~100 字）\n"
        "7. ## 来源（所有引用链接）\n\n"
        "## 格式规则（必须遵守）\n\n"
        "1. 引用块（blockquote > ）中不要包含 TL;DR 字样，直接写摘要内容\n"
        "2. 所有链接必须以纯文本展示 URL，不要用 [text](url) 或 <a href> 格式。"
        "正确格式：名称 (https://example.com) 或 名称: https://example.com\n"
        "3. 使用 **text** 格式加粗关键词\n"
    )


def build_user_prompt(
    topic: dict, materials: list, correction: str = ""
) -> str:
    """Build the user prompt with topic and materials context."""
    today = datetime.now().strftime("%Y-%m-%d")

    parts = [f"发布日期: {today}\n"]

    # Topic
    parts.append("## 选定主题\n")
    parts.append(f"标题: {topic.get('title', '')}")
    parts.append(f"摘要: {topic.get('summary', '')}")
    tags = topic.get("tags", [])
    if tags:
        parts.append(f"关键词: {', '.join(tags)}")
    source_urls = topic.get("source_urls", [])
    if source_urls:
        parts.append(f"来源 URL: {', '.join(source_urls)}")
    parts.append("")

    # Materials
    parts.append("## 素材\n")
    for i, m in enumerate(materials[:10], 1):
        parts.append(f"### 素材 {i}: {m.get('title', '')}")
        parts.append(f"类型: {m.get('type', '')}")
        parts.append(f"来源: {m.get('source', '')}")
        if m.get("url"):
            parts.append(f"URL: {m['url']}")
        if m.get("summary"):
            parts.append(f"摘要: {m['summary'][:300]}")
        if m.get("arxiv_id"):
            parts.append(f"arXiv: {m['arxiv_id']}")
        parts.append("")

    # Correction feedback
    if correction:
        parts.append("## 修改意见\n")
        parts.append(f"请根据以下意见重新生成文章：\n{correction}\n")

    parts.append("请按照系统提示中的文章结构和格式规则撰写文章。")
    return "\n".join(parts)


def post_process_draft(text: str) -> str:
    """Post-process ERNIE output to enforce format rules.

    1. Remove TL;DR from blockquotes
    2. Convert [text](url) to plain text format
    3. Ensure **text** is used (not other bold formats)
    """
    lines = text.split("\n")
    processed = []
    for line in lines:
        # Rule 1: Remove "TL;DR" from blockquote lines
        if line.strip().startswith(">"):
            line = re.sub(r"\*?\*?TL;DR\*?\*?\s*[:：]?\s*", "", line)
            line = re.sub(r"TL;DR\s*[:：]?\s*", "", line)

        # Rule 2: Convert [text](url) to text (url)
        line = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", line)

        processed.append(line)

    return "\n".join(processed)


def generate_draft(
    materials_path: str,
    topic_path: str,
    output_path: str,
    correction: str = "",
):
    """Main entry: generate article draft using ERNIE API."""
    # Load inputs
    with open(topic_path, "r", encoding="utf-8") as f:
        topic = json.load(f)
    with open(materials_path, "r", encoding="utf-8") as f:
        materials_data = json.load(f)
    materials = materials_data.get("materials", [])

    # Get API credentials
    api_key = os.environ.get("ERNIE_API_KEY", "")
    secret_key = os.environ.get("ERNIE_SECRET_KEY", "")
    if not api_key or not secret_key:
        print("ERNIE_API_KEY or ERNIE_SECRET_KEY not set", file=sys.stderr)
        print("Falling back to skeleton generator", file=sys.stderr)
        _fallback_generate(topic, materials, output_path)
        return

    # Get token
    print("[ernie] obtaining access token...", file=sys.stderr)
    access_token = get_ernie_token(api_key, secret_key)

    # Build prompts
    system_prompt = build_system_prompt()
    user_prompt = build_user_prompt(topic, materials, correction)

    # Call ERNIE
    mode = "regeneration (with correction)" if correction else "initial generation"
    print(f"[ernie] calling ERNIE 4.0 for {mode}...", file=sys.stderr)
    raw_output = call_ernie(access_token, system_prompt, user_prompt)

    if not raw_output.strip():
        print("[ernie] empty response, falling back to skeleton", file=sys.stderr)
        _fallback_generate(topic, materials, output_path)
        return

    # Post-process
    draft = post_process_draft(raw_output)

    # Write output
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(draft)

    word_count = len(re.findall(r"[\u4e00-\u9fff]", draft)) + len(
        re.findall(r"[a-zA-Z]+", draft)
    )
    print(f"[ernie] draft generated: {output_path} (~{word_count} words)",
          file=sys.stderr)


def _fallback_generate(topic: dict, materials: list, output_path: str):
    """Fallback: generate a skeleton draft when ERNIE is unavailable."""
    title = topic.get("title", "AI 热点")
    summary = topic.get("summary", "")
    today = datetime.now().strftime("%Y-%m-%d")

    source_lines = []
    for m in materials:
        url = m.get("url", "")
        if url:
            source_lines.append(f"- {m.get('title', url)} ({url})")

    sources = "\n".join(source_lines) if source_lines else "- (sources pending)"

    draft = f"""# {title}

> {summary if summary else '(summary pending)'}

---

## 背景

**{title}** 是近期 AI 领域的热门话题。

(background pending - please edit manually)

## 核心内容

(core content pending - please edit manually)

## 影响分析

(impact analysis pending - please edit manually)

## 你可以做什么

1. (action item 1)
2. (action item 2)

---

## 来源

{sources}

---

*本文由 "AI 每日 10 分钟" 自动化流水线辅助生成，经人工审核后发布。*
*发布日期: {today}*
"""

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(draft)
    print(f"[ernie] fallback skeleton: {output_path}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="ERNIE 4.0 article generator")
    parser.add_argument("--materials", required=True, help="Materials JSON path")
    parser.add_argument("--topic", required=True, help="Selected topic JSON path")
    parser.add_argument("--output", required=True, help="Output draft MD path")
    parser.add_argument("--correction", default="", help="Correction feedback for regeneration")
    args = parser.parse_args()

    generate_draft(args.materials, args.topic, args.output, args.correction)


if __name__ == "__main__":
    main()
