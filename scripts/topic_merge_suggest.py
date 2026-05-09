#!/usr/bin/env python3
"""Analyze Top-N topics and suggest merge candidates.

Usage:
    python3 topic_merge_suggest.py --input 01_topics.json [--output 02_merge_suggestions.json]

Logic:
    1. Extract entities (company names, products, tech terms) from titles + summaries
    2. Compute pairwise overlap scores
    3. Group topics with high overlap into merge suggestions
    4. Classify merge type (same_event / same_trend / supply_chain / opposing_view)
"""

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

# ── Entity dictionaries ──

# Company / product names that should be treated as single entities
ENTITY_PATTERNS = [
    # Big tech
    r"OpenAI", r"Anthropic", r"Google", r"Amazon", r"Microsoft", r"Apple",
    r"Meta", r"Nvidia", r"Samsung", r"Tesla", r"xAI",
    # AI products/models
    r"ChatGPT", r"GPT-?\d", r"Claude", r"Gemini", r"LLaMA", r"DeepSeek",
    r"Tank OS", r"Podman", r"Red Hat",
    r"Bedrock", r"AWS", r"Azure", r"Copilot",
    r"Rufus", r"Codex",
    # AI concepts (lowercased for matching)
    r"AI Agent", r"Agent", r"LLM", r"MoE", r"RAG",
    r"MCP", r"DSML",
    # Military/gov
    r"Pentagon", r"DoD", r"NSA", r"Five Eyes",
    # People
    r"Musk", r"Altman", r"Sam Altman", r"Elon Musk",
]

# Merge type classification rules
MERGE_TYPE_RULES = {
    "same_event": {
        "keywords": ["trial", "court", "lawsuit", "testify", "testimony",
                      "诉讼", "审判", "法庭", "出庭", "作证"],
        "description": "同一事件的不同报道",
    },
    "same_company": {
        "keywords": [],  # Detected by entity overlap
        "description": "同一公司/产品的多条新闻",
    },
    "same_trend": {
        "keywords": ["agent", "replace", "alternative", "shift",
                      "智能体", "替代", "转型", "路线"],
        "description": "不同公司指向同一技术趋势",
    },
    "opposing_view": {
        "keywords": ["refuse", "reject", "accept", "sign", "vs",
                      "拒绝", "接受", "签署", "反对"],
        "description": "同一话题的对立视角",
    },
    "supply_chain": {
        "keywords": ["chip", "supply", "shortage", "cost", "investment",
                      "芯片", "供应", "短缺", "成本", "投资"],
        "description": "产业链上下游关联",
    },
}


def extract_entities(text: str) -> set:
    """Extract known entities from text."""
    entities = set()
    text_lower = text.lower()
    for pattern in ENTITY_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            # Normalize: keep original case for display
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                entities.add(match.group().lower())
    return entities


def extract_keywords(text: str) -> set:
    """Extract general keywords from title + summary."""
    # Simple tokenization: split on non-alphanumeric, keep tokens >= 3 chars
    tokens = set()
    for word in re.split(r"[^a-zA-Z0-9\u4e00-\u9fff]+", text.lower()):
        word = word.strip()
        if len(word) >= 3:
            tokens.add(word)
    return tokens


def compute_overlap(set_a: set, set_b: set) -> float:
    """Jaccard-like overlap score with bonus for entity matches."""
    if not set_a or not set_b:
        return 0.0
    intersection = set_a & set_b
    if not intersection:
        return 0.0
    # Weighted: entity matches count more
    return len(intersection) / min(len(set_a), len(set_b))


def classify_merge_type(topic_a: dict, topic_b: dict, shared_entities: set) -> str:
    """Determine the merge type based on content and shared entities."""
    combined = " ".join([
        topic_a.get("title", ""), topic_a.get("summary", ""),
        topic_b.get("title", ""), topic_b.get("summary", ""),
    ]).lower()

    # Check same_event first (highest priority)
    for kw in MERGE_TYPE_RULES["same_event"]["keywords"]:
        if kw.lower() in combined:
            return "same_event"

    # Check if same company
    company_entities = {"openai", "anthropic", "google", "amazon", "microsoft",
                        "apple", "meta", "nvidia", "tesla", "xaI"}
    if shared_entities & company_entities:
        return "same_company"

    # Check opposing view
    has_refuse = any(kw in combined for kw in ["refuse", "reject", "拒绝", "反对"])
    has_accept = any(kw in combined for kw in ["accept", "sign", "deal", "接受", "签署"])
    if has_refuse and has_accept:
        return "opposing_view"

    # Check supply chain
    for kw in MERGE_TYPE_RULES["supply_chain"]["keywords"]:
        if kw.lower() in combined:
            return "supply_chain"

    # Check same trend
    for kw in MERGE_TYPE_RULES["same_trend"]["keywords"]:
        if kw.lower() in combined:
            return "same_trend"

    return "related"


def generate_merge_title(topics: list, merge_type: str, shared_entities: set) -> str:
    """Generate a suggested title for the merged article."""
    # Extract key entities for title
    entities_str = "、".join(e.title() for e in sorted(shared_entities)[:3])

    type_templates = {
        "same_event": f"{{entities}}事件全追踪",
        "same_company": f"{{entities}}的AI双线布局",
        "same_trend": f"AI {{trend}}：从{entities_str}看行业走向",
        "opposing_view": f"{{entities}}的分歧：AI伦理的十字路口",
        "supply_chain": f"AI产业链变局：{entities_str}",
        "related": f"AI日报：{entities_str}",
    }

    template = type_templates.get(merge_type, type_templates["related"])
    return template.format(entities=entities_str, trend="趋势")


def suggest_merges(topics: list, min_overlap: float = 0.15) -> list:
    """Analyze topics and return merge suggestions."""
    # Build feature sets for each topic
    topic_features = []
    for t in topics:
        text = " ".join([t.get("title", ""), t.get("summary", "")])
        entities = extract_entities(text)
        keywords = extract_keywords(text)
        topic_features.append({
            "topic": t,
            "entities": entities,
            "keywords": keywords,
        })

    # Compute pairwise overlaps
    merge_pairs = []
    n = len(topic_features)
    for i in range(n):
        for j in range(i + 1, n):
            a, b = topic_features[i], topic_features[j]
            entity_overlap = compute_overlap(a["entities"], b["entities"])
            keyword_overlap = compute_overlap(a["keywords"], b["keywords"])

            # Combined score: entity overlap weighted higher
            combined_score = entity_overlap * 0.7 + keyword_overlap * 0.3

            if combined_score >= min_overlap:
                shared = a["entities"] & b["entities"]
                merge_type = classify_merge_type(a["topic"], b["topic"], shared)
                merge_pairs.append({
                    "topic_ids": [a["topic"]["id"], b["topic"]["id"]],
                    "topic_ranks": [a["topic"]["rank"], b["topic"]["rank"]],
                    "titles": [a["topic"]["title"], b["topic"]["title"]],
                    "shared_entities": sorted(shared),
                    "merge_type": merge_type,
                    "merge_type_desc": MERGE_TYPE_RULES.get(merge_type, {}).get("description", "关联话题"),
                    "score": round(combined_score, 3),
                })

    # Sort by score descending
    merge_pairs.sort(key=lambda x: x["score"], reverse=True)

    # Build non-overlapping groups (greedy)
    used_ids = set()
    suggestions = []
    standalone = []

    for pair in merge_pairs:
        ids = set(pair["topic_ids"])
        if ids & used_ids:
            continue
        used_ids |= ids
        suggestions.append(pair)

    # Topics not in any merge group → standalone
    for tf in topic_features:
        if tf["topic"]["id"] not in used_ids:
            standalone.append({
                "id": tf["topic"]["id"],
                "rank": tf["topic"]["rank"],
                "title": tf["topic"]["title"],
            })

    return suggestions, standalone


def main():
    parser = argparse.ArgumentParser(description="分析主题相关性，建议合并方向")
    parser.add_argument("--input", required=True, help="01_topics.json 路径")
    parser.add_argument("--output", help="输出文件路径（默认 stdout）")
    parser.add_argument("--min-overlap", type=float, default=0.15,
                        help="最小重叠分数阈值（默认 0.15）")
    args = parser.parse_args()

    with open(args.input, "r", encoding="utf-8") as f:
        data = json.load(f)

    topics = data.get("topics", [])
    if not topics:
        print("⚠️  无候选主题", file=sys.stderr)
        return

    suggestions, standalone = suggest_merges(topics, args.min_overlap)

    result = {
        "total_topics": len(topics),
        "merge_suggestions": suggestions,
        "standalone_topics": standalone,
        "analysis_note": (
            "以上为基于关键词/实体重叠的初步分析。"
            "Agent 应在此基础上补充语义分析（技术趋势、对立视角、产业链关联），"
            "给出更丰富的合并建议。"
        ),
    }

    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"✅ 合并建议已输出到 {args.output}", file=sys.stderr)
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))

    # Summary to stderr
    print(f"\n📊 分析结果：{len(suggestions)} 组可合并 | {len(standalone)} 个独立选题",
          file=sys.stderr)
    for i, s in enumerate(suggestions, 1):
        ranks = "+".join(f"T{r}" for r in s["topic_ranks"])
        print(f"  建议{i}: {ranks} ({s['merge_type_desc']}, score={s['score']})",
              file=sys.stderr)


if __name__ == "__main__":
    main()
