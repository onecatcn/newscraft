#!/usr/bin/env python3
"""gems_prepare.py — 生成 ernie-image-turbo 配图提示词

用法:
    python3 gems_prepare.py \
        --draft 04_draft.md \
        --output-dir 05_images/

输出:
    05_images/cover_prompt.txt    — 封面图提示词
    05_images/inline_prompts.txt  — 正文配图提示词

⚠️  重要：ernie-image-turbo 的 prompt 必须全英文，不能含任何中文字符，
   否则中文文字会直接渲染到图片上。
"""

import argparse
import re
import sys
from pathlib import Path


# Default image style config
STYLE_PREFIX = (
    "Tech illustration style, clean minimal design, "
    "dark navy blue (#1a1a2e) background with white and cyan accents, "
    "subtle grid pattern, professional and modern feel, "
    "no text overlays, no text, no letters, no words, "
    "high quality digital art"
)

COVER_SUFFIX = ", 1:1 aspect ratio, suitable for WeChat article cover"
INLINE_SUFFIX = ", 16:9 aspect ratio, suitable for inline article illustration"

# 话题关键词 → 英文视觉元素映射（避免中文进 prompt）
TOPIC_VISUAL_MAP = [
    # (中文关键词列表, 英文视觉描述)
    (["figma", "设计", "design", "designer", "figma"], "UI design interface, wireframes, design tools, cursor arrow, creative workspace"),
    (["ernie", "文心", "wenxin", "paddleocr", "paddle", "百度"], "neural network, AI brain, glowing circuit pathways, language model visualization"),
    (["factory", "coding", "编程", "code", "agent", "developer"], "code editor, AI coding assistant, software architecture, flowing code streams, developer tools"),
    (["luma", "film", "movie", "电影", "hollywood", "cinema", "video"], "cinematic film strip, AI-generated movie frames, Hollywood clapperboard, digital cinema production"),
    (["robot", "机器人", "physical intelligence", "embodied", "π0", "pi0", "manipulation"], "robotic arm, robot hand grasping objects, mechanical joints, autonomous robot, dexterous manipulation"),
    (["upscale", "gpu", "cluster", "interconnect", "infrastructure", "算力"], "GPU cluster network, high-speed interconnect cables, data center servers, computing infrastructure"),
    (["llm", "model", "language model", "transformer", "reasoning"], "transformer architecture, attention mechanism visualization, neural network layers, language model"),
    (["startup", "funding", "valuation", "unicorn", "融资", "独角兽"], "startup growth chart, rocket trajectory, investment graph, tech unicorn"),
    (["arxiv", "research", "paper", "benchmark"], "scientific paper, research graph, academic visualization, data charts"),
]

DEFAULT_VISUAL = "AI brain network, glowing neural pathways, abstract technology visualization, data streams"


def extract_title(draft: str) -> str:
    """Extract article title from markdown"""
    for line in draft.split("\n"):
        line = line.strip()
        if line.startswith("# ") and not line.startswith("## "):
            return line[2:].strip()
    return "AI Technology"


def extract_sections(draft: str) -> list:
    """Extract H2 section titles"""
    sections = []
    for line in draft.split("\n"):
        line = line.strip()
        if line.startswith("## "):
            sections.append(line[3:].strip())
    return sections


def detect_visual_elements(title: str, draft: str) -> str:
    """根据文章标题和内容，推断最合适的英文视觉元素描述。

    ⚠️ 必须返回纯英文字符串，不含任何中文。
    """
    combined = (title + " " + draft[:500]).lower()
    for keywords, visual in TOPIC_VISUAL_MAP:
        if any(kw.lower() in combined for kw in keywords):
            return visual
    return DEFAULT_VISUAL


def generate_cover_prompt(title: str, draft: str) -> str:
    """Generate cover image prompt — 全英文，无中文。"""
    visual = detect_visual_elements(title, draft)
    prompt = (
        f"{STYLE_PREFIX}{COVER_SUFFIX}. "
        f"Scene: {visual}. "
        f"Futuristic and clean composition, abstract and symbolic, "
        f"no human faces, no text characters, no Chinese characters."
    )
    return prompt


def generate_inline_prompts(title: str, sections: list, draft: str) -> list:
    """Generate inline image prompt — 全英文，只生成1张核心内容图。"""
    prompts = []
    visual = detect_visual_elements(title, draft)

    for section in sections:
        if "核心内容" in section:
            prompt = (
                f"{STYLE_PREFIX}{INLINE_SUFFIX}. "
                f"Technical visualization: {visual}. "
                f"Detailed infographic style, clean data flow diagram, "
                f"no text overlays, no Chinese characters."
            )
            prompts.append({"section": section, "prompt": prompt})
            break

    return prompts


def is_digest_article(title: str) -> bool:
    """判断是否为每日AI速递文章。"""
    return "每日AI速递" in title or "每日速递" in title or "daily brief" in title.lower()


def prepare_gems(draft_path: str, output_dir: str):
    """主流程"""
    # Read draft
    with open(draft_path, "r", encoding="utf-8") as f:
        draft = f.read()

    title = extract_title(draft)

    # 速递文章：跳过通用配图，由 digest_cover.py 单独处理
    if is_digest_article(title):
        print(f"📰 检测到速递文章: {title}", file=sys.stderr)
        print(f"⚠️  速递封面请使用 digest_cover.py 生成（ernie-image 背景 + Pillow 叠字）", file=sys.stderr)
        print(f"   命令: python3 scripts/digest_cover.py --topics 01_topics.json --output cover.png", file=sys.stderr)

        # 仍生成正文配图提示词（如果有的话）
        sections = extract_sections(draft)
        Path(output_dir).mkdir(parents=True, exist_ok=True)

        inline_prompts = generate_inline_prompts(title, sections, draft)
        inline_path = Path(output_dir) / "inline_prompts.txt"
        with open(inline_path, "w", encoding="utf-8") as f:
            f.write("# 正文配图提示词\n\n")
            f.write(f"## 文章: {title}\n\n")
            if inline_prompts:
                for i, p in enumerate(inline_prompts, 1):
                    f.write(f"### 配图 {i}: {p['section']}\n\n")
                    f.write(f"**Prompt:**\n{p['prompt']}\n\n")
                    f.write(f"**规格:** 16:9 (900x506), PNG, 文件名: inline_{i}.png\n\n")
                    f.write("---\n\n")
            else:
                f.write("速递文章无正文配图\n")

        print(f"✅ 正文提示词 → {inline_path} ({len(inline_prompts)} 张)", file=sys.stderr)
        print(f"ℹ️  封面图需单独通过 digest_cover.py 生成", file=sys.stderr)
        return

    sections = extract_sections(draft)
    visual = detect_visual_elements(title, draft)

    print(f"📰 文章标题: {title}", file=sys.stderr)
    print(f"📑 章节数: {len(sections)}", file=sys.stderr)
    print(f"🎨 视觉元素: {visual[:60]}...", file=sys.stderr)

    # Ensure output directory exists
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # Generate cover prompt
    cover_prompt = generate_cover_prompt(title, draft)
    cover_path = Path(output_dir) / "cover_prompt.txt"
    with open(cover_path, "w", encoding="utf-8") as f:
        f.write("# 封面图提示词 (ernie-image-turbo)\n\n")
        f.write(f"## 文章: {title}\n\n")
        f.write("## Prompt:\n\n")
        f.write(cover_prompt)
        f.write("\n\n## 规格:\n")
        f.write("- 尺寸: 1:1 (900x900)\n")
        f.write("- 格式: PNG\n")
        f.write("- 文件名: cover.png\n")
        f.write("- ⚠️ Prompt 为纯英文，不含中文\n")

    print(f"\n✅ 封面提示词 → {cover_path}", file=sys.stderr)

    # Generate inline prompts
    inline_prompts = generate_inline_prompts(title, sections, draft)
    inline_path = Path(output_dir) / "inline_prompts.txt"
    with open(inline_path, "w", encoding="utf-8") as f:
        f.write("# 正文配图提示词 (Gemini Visual Gems)\n\n")
        f.write(f"## 文章: {title}\n\n")

        if inline_prompts:
            for i, p in enumerate(inline_prompts, 1):
                f.write(f"### 配图 {i}: {p['section']}\n\n")
                f.write(f"**Prompt:**\n{p['prompt']}\n\n")
                f.write(f"**规格:** 16:9 (900x506), PNG, 文件名: inline_{i}.png\n\n")
                f.write("---\n\n")
        else:
            f.write("无需正文配图（文章结构简单）\n")

    print(f"✅ 正文提示词 → {inline_path} ({len(inline_prompts)} 张)", file=sys.stderr)

    # Summary
    print("\n📋 配图清单:", file=sys.stderr)
    print("  1. cover.png — 封面图 (必需)", file=sys.stderr)
    for i, p in enumerate(inline_prompts, 1):
        print(f"  {i+1}. inline_{i}.png — {p['section']} (可选)", file=sys.stderr)

    print(
        "\n💡 使用方法: 将上述 prompt 复制到 Gemini Visual Gems，"
        "生成后保存到 05_images/ 目录。",
        file=sys.stderr,
    )


def main():
    parser = argparse.ArgumentParser(description="生成 Gemini Visual Gems 配图提示词")
    parser.add_argument("--draft", required=True, help="初稿 Markdown 文件路径")
    parser.add_argument("--output-dir", required=True, help="输出目录路径")
    args = parser.parse_args()

    prepare_gems(args.draft, args.output_dir)


if __name__ == "__main__":
    main()
