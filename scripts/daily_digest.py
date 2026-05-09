#!/usr/bin/env python3
"""daily_digest.py — 每日AI速递提示词生成器

将 01_topics.json 中的 8-10 条 AI 热点分类整理，生成 03_digest_prompt.md
供 ERNIE 执行写作。Python 准备数据，ERNIE 执行写作——与现有架构一致。

用法:
    python3 daily_digest.py \
        --input 01_topics.json \
        --output 03_digest_prompt.md \
        [--web-search-topics 3] \
        [--min-topics 8] \
        [--max-topics 10]
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

# ── 分类规则 ──

CATEGORY_RULES = {
    "产品": {
        "emoji": "🚀",
        "en_keywords": [
            "launch", "release", "update", "app", "platform", "copilot",
            "chatgpt", "feature", "product", "tool", "beta", "preview",
        ],
        "cn_keywords": ["发布", "上线", "更新", "产品", "功能", "平台"],
    },
    "模型": {
        "emoji": "🧠",
        "en_keywords": [
            "new model", "model release", "open-source model", "open source model",
            "model launch", "model weights", "sota", "benchmark leaderboard",
            "llm release", "model architecture", "parameter count",
        ],
        "cn_keywords": [
            "新模型", "开源模型", "模型开源", "模型发布", "打榜", "登顶",
            "SOTA", "权重开源", "权重发布", "首发模型", "模型上线",
            "模型评测", "基准测试", "参数量",
        ],
    },
    "研究": {
        "emoji": "🔬",
        "en_keywords": [
            "arxiv", "paper", "research", "study", "university",
            "neurips", "icml", "iclr",
        ],
        "cn_keywords": ["论文", "研究", "学术", "预印本"],
    },
    "行业": {
        "emoji": "📊",
        "en_keywords": [
            "funding", "raises", "valuation", "ipo", "revenue", "market",
            "invest", "acqui", "billion", "startup", "partnership",
        ],
        "cn_keywords": ["融资", "投资", "收购", "营收", "市场", "裁员", "合作"],
    },
    "开源": {
        "emoji": "🔓",
        "en_keywords": [
            "open source", "open-source", "github", "apache",
            "mit license", "huggingface", "repo",
        ],
        "cn_keywords": ["开源", "代码库", "许可证", "社区"],
    },
    "硬件": {
        "emoji": "⚡",
        "en_keywords": [
            "chip", "gpu", "npu", "tpu", "nvidia", "amd", "intel",
            "server", "datacenter",
        ],
        "cn_keywords": ["芯片", "算力", "服务器", "硬件", "GPU"],
    },
    "机器人": {
        "emoji": "🤖",
        "en_keywords": [
            "robot", "robotic", "embodied", "autonomous", "figure", "drone",
        ],
        "cn_keywords": ["机器人", "无人驾驶", "具身", "机械臂"],
    },
}

# 分类优先级（高→低）
CATEGORY_PRIORITY = ["产品", "模型", "研究", "行业", "开源", "硬件", "机器人"]


def classify_topic(topic: dict) -> str:
    """根据关键词规则将话题分类到7大类别。

    优先级：产品 > 模型 > 研究 > 行业 > 开源 > 硬件 > 机器人。
    无匹配时按 ai_relevance 回退(≥80→模型, ≥60→行业, 否则→行业)。
    """
    title = topic.get("title", "").lower()
    summary = topic.get("summary", "").lower()
    tags = " ".join(topic.get("tags", [])).lower()
    text = f"{title} {summary} {tags}"

    for cat in CATEGORY_PRIORITY:
        rules = CATEGORY_RULES[cat]
        for kw in rules["en_keywords"]:
            if kw in text:
                return cat
        for kw in rules["cn_keywords"]:
            if kw in text:
                return cat

    # 回退：按 ai_relevance 分配
    # 未匹配任何分类关键词的话题，高相关度归入"行业"（行业新闻最通用）
    ai_rel = topic.get("ai_relevance", 0)
    if ai_rel >= 60:
        return "行业"
    return "行业"


def enrich_topics(topics: list, search_count: int = 3) -> list:
    """对 top N 话题调用 web_search.py 补充素材，失败静默跳过。

    按 composite_score 降序取前 search_count 个话题进行搜索补充。
    搜索结果附加到话题的 enrichment 字段。
    """
    if search_count <= 0:
        return topics

    # 按分数排序取 top N
    sorted_topics = sorted(
        topics, key=lambda t: t.get("composite_score", t.get("heat_score", 0)),
        reverse=True,
    )
    top_indices = [topics.index(t) for t in sorted_topics[:search_count]]

    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from web_search import search
    except ImportError:
        print("[daily_digest] web_search module not found, skipping enrichment",
              file=sys.stderr)
        return topics

    for idx in top_indices:
        topic = topics[idx]
        query = topic.get("title", "")
        if not query:
            continue

        # 提取英文关键词搜索效果更好
        tags = topic.get("tags", [])
        if tags:
            query = " ".join(tags[:3]) + " " + query

        try:
            results = search(query, max_results=3, engine="brave")
            if results:
                topics[idx]["enrichment"] = results
                print(f"  [enrich] {topic.get('title', '?')[:40]}: "
                      f"{len(results)} results", file=sys.stderr)
        except Exception as e:
            print(f"  [enrich] search failed for '{query[:40]}': {e}",
                  file=sys.stderr)

    return topics


def is_major_topic(topic: dict) -> bool:
    """判断是否需要深度补充文章。

    条件：ai_relevance > 90 AND composite_score > 80 AND source_count >= 3
    """
    ai_rel = topic.get("ai_relevance", 0)
    comp_score = topic.get("composite_score", 0)
    source_count = len(topic.get("source_urls", []))
    return ai_rel > 90 and comp_score > 80 and source_count >= 3


def generate_digest(topics: list, output_path: str) -> str:
    """主函数：分类 → 补充 → 标记重大 → 生成 03_digest_prompt.md。

    Returns the generated prompt content.
    """
    # 1. 分类
    classified = {}
    for topic in topics:
        cat = classify_topic(topic)
        if cat not in classified:
            classified[cat] = []
        classified[cat].append(topic)

    # 2. 统计
    total = len(topics)
    cat_counts = {cat: len(items) for cat, items in classified.items()}

    # 3. 构建 ⚡ 副标题行
    count_parts = []
    for cat in CATEGORY_PRIORITY:
        if cat in cat_counts and cat_counts[cat] > 0:
            count_parts.append(f"{cat} {cat_counts[cat]}")
    count_str = " · ".join(count_parts)

    # 4. Top3 关键词
    sorted_topics = sorted(
        topics,
        key=lambda t: t.get("composite_score", t.get("heat_score", 0)),
        reverse=True,
    )
    top3_keywords = []
    for t in sorted_topics[:3]:
        tags = t.get("tags", [])
        if tags:
            top3_keywords.append(tags[0])
        else:
            # 从标题提取关键词
            title = t.get("title", "")
            words = re.findall(r"[\u4e00-\u9fff]+|[A-Z][a-zA-Z]+", title)
            top3_keywords.append(words[0] if words else title[:6])
    top3_str = "·".join(top3_keywords[:3])

    # 5. 生成提示词
    prompt_lines = []

    # 写作指令
    prompt_lines.append("# 每日AI速递 — 写作提示词")
    prompt_lines.append("")
    prompt_lines.append("请根据以下分类整理的 AI 热点数据，撰写一篇「每日AI速递」文章。")
    prompt_lines.append("")
    prompt_lines.append("## 写作规则")
    prompt_lines.append("")
    prompt_lines.append("1. **字数**：1500–2500 字")
    prompt_lines.append("2. **结构**：严格遵循下方模板格式")
    prompt_lines.append("3. **风格**：科技媒体，面向开发者，简明有见地")
    prompt_lines.append("4. **禁止** `<a href>` 超链接（微信不支持外链）")
    prompt_lines.append("5. **禁止** 任何英文缩写章节标题，如 TL;DR、TLDR")
    prompt_lines.append("6. **加粗** 使用 `**加粗**` Markdown 语法（后续 wechat_draft.py 会转为 `<strong>`）")
    prompt_lines.append("7. **链接** 以纯文本展示：`名称 (https://example.com)`")
    prompt_lines.append("8. **首行** 必须为 `# 每日AI速递：{关键词}` （H1），用于微信草稿标题提取")
    prompt_lines.append("9. **章节间** 使用 `---` 分隔")
    prompt_lines.append("10. **空分类不展示**：如果没有该分类的话题，不输出该分类标题")
    prompt_lines.append("11. **重大话题标记**：话题标注了 `is_major: true` 时，摘要末尾加「→ 详见今日深度解读」")
    prompt_lines.append("12. **每个话题** 2-3 句摘要 + 1-2 句 💡 深度解读分析")
    prompt_lines.append("13. **互动 CTA**：末尾固定引导语，不使用 emoji bomb")
    prompt_lines.append("14. **参考链接**：用 `<!-- refs -->...<!-- /refs -->` 包裹，每行一条，自动小字渲染")
    prompt_lines.append("")
    prompt_lines.append("## 文章模板")
    prompt_lines.append("")
    prompt_lines.append("```markdown")
    prompt_lines.append("# 每日AI速递：{top3话题关键词}")
    prompt_lines.append("")
    prompt_lines.append(f"⚡ 今日 {total} 条　{count_str}")
    prompt_lines.append("")
    prompt_lines.append("---")
    prompt_lines.append("")

    # 每个分类的模板示例
    for cat in CATEGORY_PRIORITY:
        if cat in classified:
            emoji = CATEGORY_RULES[cat]["emoji"]
            prompt_lines.append(f"## {emoji} {cat}")
            prompt_lines.append("")
            prompt_lines.append(f"**{{话题标题}}** · {len(classified[cat])}源")
            prompt_lines.append("")
            prompt_lines.append("{2-3句摘要}")
            prompt_lines.append("")
            prompt_lines.append("💡 深度解读：{1-2句分析}")
            prompt_lines.append("")
            prompt_lines.append("---")
            prompt_lines.append("")

    prompt_lines.append("<!-- refs -->")
    prompt_lines.append("[1] https://...")
    prompt_lines.append("[2] https://...")
    prompt_lines.append("<!-- /refs -->")
    prompt_lines.append("")
    prompt_lines.append("---")
    prompt_lines.append("")
    prompt_lines.append("恭喜你完成今日份的 AI 进化！里程碑已达成：🚩")
    prompt_lines.append('别忘了顺手解锁 "点赞+在看+转发" 隐藏成就。')
    prompt_lines.append('记得点亮 星标，防止由于算法调皮导致咱们"走散"。')
    prompt_lines.append("撤了，明天同一时间见！👋")
    prompt_lines.append("```")
    prompt_lines.append("")

    # 数据区
    prompt_lines.append("## 话题数据")
    prompt_lines.append("")
    prompt_lines.append(f"Top3 关键词：{top3_str}")
    prompt_lines.append(f"总话题数：{total}")
    prompt_lines.append(f"分类统计：{count_str}")
    prompt_lines.append("")

    for cat in CATEGORY_PRIORITY:
        if cat not in classified:
            continue
        emoji = CATEGORY_RULES[cat]["emoji"]
        prompt_lines.append(f"### {emoji} {cat} ({len(classified[cat])} 条)")
        prompt_lines.append("")

        for i, topic in enumerate(classified[cat], 1):
            title = topic.get("title", "")
            summary = topic.get("summary", "")
            source_count = len(topic.get("source_urls", []))
            comp_score = topic.get("composite_score", topic.get("heat_score", 0))
            ai_rel = topic.get("ai_relevance", 0)
            is_major = is_major_topic(topic)
            tags = topic.get("tags", [])

            prompt_lines.append(f"**话题 {i}**: {title}")
            prompt_lines.append(f"- 摘要：{summary}")
            prompt_lines.append(f"- 标签：{', '.join(tags)}")
            prompt_lines.append(f"- 来源数：{source_count}")
            prompt_lines.append(f"- 综合分：{comp_score}，AI相关度：{ai_rel}")
            if is_major:
                prompt_lines.append("- **is_major: true** → 重大话题，摘要末尾加「→ 详见今日深度解读」")

            # 补充素材
            enrichment = topic.get("enrichment", [])
            if enrichment:
                prompt_lines.append("- 补充素材：")
                for r in enrichment[:2]:
                    r_title = r.get("title", "")
                    r_desc = r.get("description", "")
                    r_url = r.get("url", "")
                    if r_title:
                        prompt_lines.append(f"  - {r_title}: {r_desc[:80]} ({r_url})")

            # 来源链接
            source_urls = topic.get("source_urls", [])
            if source_urls:
                prompt_lines.append("- 来源链接：")
                for url in source_urls[:3]:
                    prompt_lines.append(f"  - {url}")

            prompt_lines.append("")

    prompt_content = "\n".join(prompt_lines)

    # 写入文件
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(prompt_content)

    return prompt_content


def main():
    parser = argparse.ArgumentParser(description="每日AI速递提示词生成器")
    parser.add_argument(
        "--input", required=True,
        help="01_topics.json 文件路径",
    )
    parser.add_argument(
        "--output", required=True,
        help="03_digest_prompt.md 输出路径",
    )
    parser.add_argument(
        "--web-search-topics", type=int, default=3,
        help="对 top N 话题调用 web_search 补充素材（默认 3，0=禁用）",
    )
    parser.add_argument(
        "--min-topics", type=int, default=8,
        help="最少话题数（默认 8）",
    )
    parser.add_argument(
        "--max-topics", type=int, default=10,
        help="最多话题数（默认 10）",
    )
    args = parser.parse_args()

    # 1. 加载话题数据
    with open(args.input, "r", encoding="utf-8") as f:
        data = json.load(f)

    topics = data.get("topics", [])
    if not topics:
        print("❌ 没有话题数据", file=sys.stderr)
        sys.exit(1)

    print(f"📰 加载 {len(topics)} 条话题", file=sys.stderr)

    # 2. 过滤低质量话题（ai_relevance < 40 已由 topic_parse.py 过滤）
    # 按 composite_score 排序，取 top max_topics
    topics.sort(
        key=lambda t: t.get("composite_score", t.get("heat_score", 0)),
        reverse=True,
    )

    # 如果话题数不足 min_topics，降低要求
    if len(topics) < args.min_topics:
        print(f"⚠️  话题数 {len(topics)} 不足 {args.min_topics}，继续处理",
              file=sys.stderr)

    # 截取 max_topics
    topics = topics[:args.max_topics]
    print(f"📊 选取 {len(topics)} 条话题", file=sys.stderr)

    # 3. 补充素材
    if args.web_search_topics > 0:
        print(f"🔍 对 top {args.web_search_topics} 话题补充搜索素材...",
              file=sys.stderr)
        topics = enrich_topics(topics, search_count=args.web_search_topics)

    # 4. 生成提示词
    print("📝 生成速递提示词...", file=sys.stderr)
    content = generate_digest(topics, args.output)

    # 5. 输出摘要
    classified = {}
    for topic in topics:
        cat = classify_topic(topic)
        classified[cat] = classified.get(cat, 0) + 1

    major_count = sum(1 for t in topics if is_major_topic(t))

    cat_summary = " · ".join(
        f"{cat} {classified[cat]}" for cat in CATEGORY_PRIORITY
        if cat in classified
    )

    print(f"\n✅ 速递提示词已生成: {args.output}", file=sys.stderr)
    print(f"   话题: {len(topics)} 条 ({cat_summary})", file=sys.stderr)
    print(f"   重大话题: {major_count} 条（需深度补充）", file=sys.stderr)
    print(f"   字数预计: 1500-2500 字", file=sys.stderr)


if __name__ == "__main__":
    main()
