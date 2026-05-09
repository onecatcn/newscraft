#!/usr/bin/env python3
"""topic_parse.py — 解析、排名、去重多源热点话题

用法:
    python3 topic_parse.py \
        --input raw_topics.json \
        --content-log content_log.md \
        --output 01_topics.json \
        [--top-n 10] \
        [--dedup-days 7]

输入:
    raw_topics.json   — 多源热点抓取原始响应
    content_log.md    — 历史发布日志（用于去重）

输出:
    01_topics.json    — 排名去重后的候选主题列表
"""

import argparse
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path


def load_raw_topics(input_path: str) -> list:
    """加载 多源热点抓取原始响应"""
    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Handle both direct array and nested response format
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        if "data" in data and "topics" in data["data"]:
            return data["data"]["topics"]
        if "topics" in data:
            return data["topics"]
    return []


def load_published_keywords(content_log_path: str, dedup_days: int) -> set:
    """从 content_log.md 提取最近 N 天已发布主题的关键词

    兼容三种 content_log 格式：
    1. - **标题**: xxx / - **主题关键词**: a, b, c
    2. - **T1**: xxx /   - **主题关键词**: a, b, c  (缩进+T编号)
    3. - 一行式描述（关键词：a, b, c）
    """
    keywords = set()
    if not Path(content_log_path).exists():
        return keywords

    with open(content_log_path, "r", encoding="utf-8") as f:
        content = f.read()

    cutoff = datetime.now() - timedelta(days=dedup_days)

    # 日期行可能是 ## 2026-04-25 或 ### 2026-04-25 - 后缀
    current_date = None
    for line in content.split("\n"):
        date_match = re.match(r"^#{1,3}\s+(\d{4}-\d{2}-\d{2})", line)
        if date_match:
            try:
                current_date = datetime.strptime(date_match.group(1), "%Y-%m-%d")
            except ValueError:
                current_date = None
            continue

        if current_date and current_date >= cutoff:
            stripped = line.strip()

            # 格式1: - **标题**: xxx  或  格式2: - **T1**: xxx
            title_match = re.match(
                r"^-\s+\*\*(?:标题|T\d+)\*\*:\s*(.+)", stripped
            )
            if title_match:
                title = title_match.group(1).strip()
                for word in re.split(r"[：:，,\s、/]+", title):
                    word = word.strip().lower()
                    if len(word) >= 2:
                        keywords.add(word)

            # 格式1/2: - **主题关键词**: a, b, c  (可能缩进)
            kw_match = re.match(
                r"^-\s+\*\*主题关键词\*\*:\s*(.+)", stripped
            )
            if kw_match:
                for kw in kw_match.group(1).split(","):
                    kw = kw.strip().lower()
                    if kw:
                        keywords.add(kw)

            # 格式3: - 描述文字（关键词：a, b, c）
            inline_kw = re.search(r"（关键词[：:]\s*(.+?)）", stripped)
            if inline_kw:
                for kw in inline_kw.group(1).split(","):
                    kw = kw.strip().lower()
                    if kw:
                        keywords.add(kw)

    return keywords


def ai_relevance_score(topic: dict) -> float:
    """评估主题与 AI/技术领域的相关性，返回 0-100 分。

    - 核心 AI 关键词命中 → 高分（80-100）
    - 泛科技/硬件/开源等相关 → 中分（40-70）
    - 纯商业/金融/政策等非 AI 话题 → 低分（0-20）
    """
    # 核心 AI / ML 关键词（命中任意 1 个即高度相关）
    core_ai_kw = {
        "ai", "ml", "llm", "gpt", "claude", "gemini", "llama", "mistral",
        "sora", "diffusion", "stable diffusion", "transformer", "neural",
        "deep learning", "machine learning", "artificial intelligence",
        "language model", "multimodal", "embedding", "fine-tun", "rlhf",
        "training", "benchmark", "agent",
        "copilot", "chatgpt", "openai", "anthropic", "deepmind",
        "hugging face", "huggingface", "pytorch", "tensorflow",
        "大模型", "语言模型", "人工智能", "机器学习", "深度学习", "神经网络",
        "生成式", "多模态", "推理", "训练", "微调", "向量",
    }
    # 泛科技相关关键词（中等加分）
    tech_kw = {
        "open source", "github", "developer", "api", "hardware", "chip",
        "gpu", "npu", "robotics", "automation", "startup", "research",
        "paper", "arxiv", "dataset", "benchmark", "software", "cloud",
        "开源", "芯片", "研究", "论文", "数据集", "云计算", "机器人",
    }
    # 明确非 AI 的商业/金融关键词（命中则降分）
    non_ai_kw = {
        "stock", "ipo", "earnings", "revenue", "acquisition", "merger",
        "quarterly", "fiscal", "wall street", "market cap", "valuation",
        "lawsuit", "regulation", "antitrust", "gdpr", "policy brief",
        "上市", "财报", "并购", "营收", "监管", "诉讼", "股价",
    }
    # 教程/教学型关键词（命中则适度降分——有AI相关性但非新闻热点）
    tutorial_kw = {
        "教程", "入门", "实战", "完全解析", "指南", "手册", "从零开始",
        "tutorial", "getting started", "how to", "guide", "handbook",
        "最佳实践", "详解", "一文读懂", "一文看懂", "面试", "刷题",
    }

    # 仅基于内容（标题 + 摘要 + 标签）匹配，不含 category/subcategory
    # 避免 TechCrunch AI / The Verge 等 RSS 源的 category="ai" 导致所有文章虚高
    content_text = " ".join([
        topic.get("title", ""),
        topic.get("summary", ""),
        " ".join(topic.get("tags", [])),
    ]).lower()

    def kw_match(kw: str, text: str) -> bool:
        """全词匹配：避免 'ai' 匹配到 'pair'/'available'/'chairman' 等"""
        if len(kw) <= 3 or kw in {"llm", "gpt", "ml", "ai"}:
            return bool(re.search(r'\b' + re.escape(kw) + r'\b', text))
        return kw in text

    # 计算命中数（基于内容文本）
    core_hits = sum(1 for kw in core_ai_kw if kw_match(kw, content_text))
    tech_hits = sum(1 for kw in tech_kw if kw_match(kw, content_text))
    non_ai_hits = sum(1 for kw in non_ai_kw if kw_match(kw, content_text))

    if core_hits >= 2:
        base = 100
    elif core_hits == 1:
        base = 80
    elif tech_hits >= 2:
        base = 60
    elif tech_hits == 1:
        base = 45
    else:
        base = 20

    # 若同时命中非 AI 关键词且核心 AI 命中不足，降低得分
    if non_ai_hits >= 2 and core_hits == 0:
        base = min(base, 15)
    elif non_ai_hits >= 1 and core_hits == 0:
        base = min(base, 30)

    # 教程/教学型内容：AI 相关但非新闻热点，大幅降权
    tutorial_hits = sum(1 for kw in tutorial_kw if kw in content_text)
    if tutorial_hits >= 1:
        base = max(base - 40, 45)  # 降40分但不低于45（仍过40分门槛，但很难进Top10）

    return float(base)


def score_topic(topic: dict) -> float:
    """计算主题综合评分

    评分维度:
    - heat_score:    热度          (权重 0.20)
    - freshness:     新鲜度        (权重 0.25)
    - source_count:  来源数量      (权重 0.05)
    - ai_relevance:  AI 领域相关性 (权重 0.50)  ← 核心权重，防止非AI热文冲榜
    """
    heat = topic.get("heat_score", 0)
    source_count = min(topic.get("source_count", 1), 50)  # cap at 50

    # Freshness: hours since first seen (newer = higher score)
    first_seen = topic.get("first_seen", "")
    freshness = 100
    if first_seen:
        try:
            seen_time = datetime.fromisoformat(first_seen.replace("Z", "+00:00"))
            hours_ago = (datetime.now().astimezone() - seen_time).total_seconds() / 3600
            freshness = max(0, 100 - hours_ago * 4)  # Lose ~4 points per hour
        except (ValueError, TypeError):
            pass

    # Normalize source_count to 0-100 scale
    source_score = min(source_count / 50 * 100, 100)

    # AI relevance score (0-100)
    ai_rel = ai_relevance_score(topic)

    return heat * 0.20 + freshness * 0.25 + source_score * 0.05 + ai_rel * 0.50


def _normalize_keywords(words: set) -> set:
    """将英文关键词翻译为中文，实现跨语言去重匹配。

    映射覆盖 AI/科技领域高频事件词（投资/融资/裁员/发布/收购/估值等），
    以及常见数量表达（$40B → 400亿、$500M → 5亿）。
    """
    _EN_ZH = {
        # 动作类
        "invests": "投资", "invest": "投资", "investment": "投资",
        "investing": "投资", "invested": "投资",
        "funding": "融资", "funded": "融资", "raise": "融资", "raises": "融资",
        "raised": "融资",
        "acquire": "收购", "acquires": "收购", "acquired": "收购",
        "acquisition": "收购", "buy": "收购", "buys": "收购", "bought": "收购",
        "launch": "发布", "launches": "发布", "launched": "发布",
        "release": "发布", "releases": "发布", "released": "发布",
        "announce": "宣布", "announces": "宣布", "announced": "宣布",
        "fire": "裁员", "fires": "裁员", "fired": "裁员", "firing": "裁员",
        "layoff": "裁员", "layoffs": "裁员", "laid": "裁员",
        "cut": "裁减", "cuts": "裁减", "cutting": "裁减",
        "replace": "替代", "replaces": "替代", "replacing": "替代",
        "valuation": "估值", "valued": "估值",
        "merge": "合并", "merger": "合并", "merged": "合并",
        "partner": "合作", "partnership": "合作",
        "shutdown": "关闭", "shut": "关闭", "close": "关闭", "closes": "关闭",
        "ban": "禁令", "banned": "禁令", "bans": "禁令",
        "sue": "起诉", "sues": "起诉", "sued": "起诉", "lawsuit": "起诉",
        "approve": "批准", "approved": "批准", "approves": "批准",
        "reject": "拒绝", "rejected": "拒绝", "rejects": "拒绝",
        # 金额缩写
        "$40b": "400亿", "$400b": "4000亿", "$40bn": "400亿",
        "$500m": "5亿", "$5b": "50亿", "$50b": "500亿",
        "$1b": "10亿", "$10b": "100亿", "$100b": "1000亿",
        "$1m": "1百万", "$2b": "20亿", "$20b": "200亿",
        "$15b": "150亿", "$30b": "300亿",
        "40b": "400亿", "400b": "4000亿", "500m": "5亿",
        # 事件类名词
        "agent-commerce": "智能体交易", "agent": "智能体", "agents": "智能体",
        "marketplace": "交易市场", "market": "市场",
        "energy": "能耗", "efficiency": "效率",
        "military": "军事", "defense": "国防", "pentagon": "五角大楼",
        "resign": "辞职", "resigns": "辞职", "resigned": "辞职",
        "step": "退位", "stepping": "退位",
        "valuation": "估值",
        "startup": "创业公司", "startups": "创业公司",
    }
    normalized = set()
    for w in words:
        wl = w.lower().strip()
        if wl in _EN_ZH:
            normalized.add(_EN_ZH[wl])
        else:
            normalized.add(wl)
    return normalized


def check_duplicate(topic: dict, published_keywords: set) -> tuple:
    """检查是否与已发布主题重复

    Returns:
        (is_duplicate: bool, matched_keywords: list)
    """
    if not published_keywords:
        return False, []

    # 品牌/公司/产品通名：出现在已发布文章里的这些词不代表「同一事件」
    # 同一公司可以有多个不同事件，不能因为都含 "anthropic"/"claude" 就互相去重
    _BRAND_NOISE = {
        "anthropic", "claude", "opus", "sonnet", "haiku",
        "openai", "gpt", "chatgpt", "o1", "o3", "o4",
        "google", "gemini", "deepmind",
        "microsoft", "azure", "copilot",
        "meta", "llama",
        "nvidia", "amazon", "aws",
        "apple", "samsung",
        "deepseek", "qwen", "kimi", "moonshot",
        "ai", "llm", "ml",
    }

    # Extract keywords from topic
    topic_words = set()
    title = topic.get("title", "")
    for word in re.split(r"[：:，,\s、/]+", title):
        word = word.strip().lower()
        if len(word) >= 2:
            topic_words.add(word)

    tags = topic.get("tags", [])
    for tag in tags:
        topic_words.add(tag.strip().lower())

    # 去掉品牌噪音词
    effective_topic = topic_words - _BRAND_NOISE
    effective_published = published_keywords - _BRAND_NOISE

    # 跨语言归一化：英文→中文映射后再做交集
    norm_topic = _normalize_keywords(effective_topic)
    norm_published = _normalize_keywords(effective_published)

    matched = norm_topic & norm_published
    # Consider duplicate if >= 2 non-brand keywords match
    is_dup = len(matched) >= 2
    return is_dup, list(matched)


def parse_and_rank(
    input_path: str,
    content_log_path: str,
    output_path: str,
    top_n: int = 10,
    dedup_days: int = 7,
):
    """主处理流程"""
    # 1. Load raw topics
    topics = load_raw_topics(input_path)
    if not topics:
        print("⚠️  未获取到任何热点话题", file=sys.stderr)
        result = {
            "fetch_time": datetime.now().isoformat(),
            "source": "multisource",
            "period": "24h",
            "topics": [],
        }
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        return

    print(f"📊 获取到 {len(topics)} 个原始话题", file=sys.stderr)

    # 2. Load published keywords for dedup
    published_kw = load_published_keywords(content_log_path, dedup_days)
    if published_kw:
        print(
            f"📖 已加载 {len(published_kw)} 个已发布关键词（近 {dedup_days} 天）",
            file=sys.stderr,
        )

    # 3. Score and annotate
    for topic in topics:
        topic["_ai_relevance"] = ai_relevance_score(topic)
        topic["_score"] = score_topic(topic)
        is_dup, matched = check_duplicate(topic, published_kw)
        topic["is_duplicate"] = is_dup
        topic["duplicate_keywords"] = matched

    # 3.5 硬过滤：ai_relevance < 40 的话题直接排除（无论热度多高）
    before = len(topics)
    topics = [t for t in topics if t["_ai_relevance"] >= 40]
    filtered = before - len(topics)
    if filtered:
        print(f"🚫 过滤掉 {filtered} 个非AI相关话题（ai_relevance < 40）", file=sys.stderr)

    # 4. Sort by score (non-duplicates first, then by score)
    topics.sort(key=lambda t: (not t["is_duplicate"], t["_score"]), reverse=True)

    # 5. Build output
    ranked_topics = []
    for i, topic in enumerate(topics[:top_n], 1):
        ranked_topics.append(
            {
                "id": topic.get("id", f"topic_{i}"),
                "rank": i,
                "title": topic.get("title", ""),
                "summary": topic.get("summary", ""),
                "heat_score": topic.get("heat_score", 0),
                "source_urls": topic.get("source_urls", []),
                "category": topic.get("subcategory", topic.get("category", "")),
                "tags": topic.get("tags", []),
                "trend": topic.get("trend", ""),
                "is_duplicate": topic.get("is_duplicate", False),
                "duplicate_keywords": topic.get("duplicate_keywords", []),
                "ai_relevance": round(topic.get("_ai_relevance", 0), 1),
                "composite_score": round(topic["_score"], 2),
            }
        )

    result = {
        "fetch_time": datetime.now().isoformat(),
        "source": "multisource",
        "period": "24h",
        "total_raw": len(topics),
        "topics": ranked_topics,
    }

    # 6. Write output
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"✅ 输出 Top {len(ranked_topics)} 候选主题到 {output_path}", file=sys.stderr)
    dup_count = sum(1 for t in ranked_topics if t["is_duplicate"])
    if dup_count:
        print(f"⚠️  其中 {dup_count} 个可能与近期已发布主题重复", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="解析、排名、去重多源热点话题")
    parser.add_argument("--input", required=True, help="多源热点抓取原始响应 JSON")
    parser.add_argument(
        "--content-log", default="content_log.md", help="发布日志路径 (默认: content_log.md)"
    )
    parser.add_argument("--output", required=True, help="输出文件路径")
    parser.add_argument("--top-n", type=int, default=10, help="输出 Top N 主题 (默认: 10)")
    parser.add_argument(
        "--dedup-days", type=int, default=7, help="去重时间窗口天数 (默认: 7)"
    )
    args = parser.parse_args()

    parse_and_rank(args.input, args.content_log, args.output, args.top_n, args.dedup_days)


if __name__ == "__main__":
    main()
