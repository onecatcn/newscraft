#!/usr/bin/env python3
"""multisource_fetch.py — 从多个免费数据源抓取 AI 领域热点

数据源:
  - Hacker News  (Algolia Search API, 无需 Key)
  - Reddit       (r/MachineLearning, r/artificial, r/LocalLLaMA, JSON API)
  - arXiv        (cs.AI / cs.LG / cs.CL, 官方 API)
  - RSS 聚合     (TechCrunch AI, VentureBeat AI, The Verge AI)
  - HuggingFace  (Daily Papers RSS)
  - Y Combinator (Blog RSS)
  - 国内热榜      (IT之家/掘金/极客公园/36氪/虎嗅/CSDN/少数派/HelloGitHub, daily-hot-news skill)


无需任何 API Key，全部使用 Python 标准库。

用法:
    python3 multisource_fetch.py [--limit 30] [--period 24h] [--category ai]

输出:
    JSON 数组到 stdout（兼容 topic_parse.py）
    进度日志到 stderr
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

TIMEOUT = 20  # seconds per request


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Heat scoring helpers ───────────────────────────────────────────────────────

# 高价值关键词：核心 AI 公司 + 发布/融资/收购事件
_HIGH_KW = [
    # 核心 AI 公司与模型（+20）
    "anthropic", "claude",
    "openai", "gpt", "o1", "o3", "o4",
    "google", "gemini", "google deepmind",
    # 其他主流模型与厂商
    "llama", "mistral", "grok", "xai", "sora",
    "deepseek", "qwen", "kimi", "moonshot", "zhipu", "baidu ernie",
    # 高价值事件词
    "raises", "raised", "funding", "billion", "million", "$",
    "launch", "launched", "releases", "released", "announces", "announced",
    "acqui", "acquires", "merger", "partnership",
    "open source", "open-source", "breakthrough",
]
# 中等价值关键词：科技巨头 + 技术方向（+10）
_MED_KW = [
    # 科技巨头（关注但非纯 AI 公司）
    "amazon", "aws", "microsoft", "azure", "nvidia",
    "apple", "meta",
    # 技术方向与更新事件
    "update", "upgrade", "major", "new model", "new ai",
    "api", "agent", "multimodal", "reasoning", "benchmark",
    "alignment", "rlhf", "vision language",
    "fine-tun", "finetun", "lora", "rag", "inference", "quantiz",
    "open weights", "weights", "model card",
]

def _keyword_bonus(title: str) -> float:
    """根据标题关键词给出 0-20 的加分。"""
    t = title.lower()
    for kw in _HIGH_KW:
        if kw in t:
            return 20.0
    for kw in _MED_KW:
        if kw in t:
            return 10.0
    return 0.0

def _recency_bonus(published_iso: str, base_decay_hours: int = 24) -> float:
    """根据发布时间距现在的小时数给出 0-25 的加分。"""
    try:
        # 兼容 'Z' 结尾和带时区的格式
        ts = published_iso.replace("Z", "+00:00")
        pub_dt = datetime.fromisoformat(ts)
        now_dt = datetime.now(timezone.utc)
        hours_old = (now_dt - pub_dt).total_seconds() / 3600
        if hours_old < 4:
            return 25.0
        elif hours_old < 8:
            return 18.0
        elif hours_old < 16:
            return 10.0
        elif hours_old < 24:
            return 5.0
        else:
            return 0.0
    except Exception:
        return 0.0

def _rss_heat_score(title: str, published_iso: str) -> float:
    """RSS/arXiv 条目的动态热度：基础分 + 时效性 + 关键词。"""
    base = 50.0
    return min(base + _recency_bonus(published_iso) + _keyword_bonus(title), 95.0)


class _Redirect308Handler(urllib.request.HTTPRedirectHandler):
    """urllib does not follow 308 by default; add it."""
    def http_error_308(self, req, fp, code, msg, headers):
        return self.http_error_302(req, fp, code, msg, headers)


_OPENER = urllib.request.build_opener(_Redirect308Handler())


def fetch_url(url: str, extra_headers: dict = None) -> str | None:
    """HTTP GET with User-Agent. Follows 301/302/307/308. Respects HTTP_PROXY env var."""
    headers = {"User-Agent": "Mozilla/5.0 (compatible; AutopubBot/1.0)"}
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(url, headers=headers)
    try:
        with _OPENER.open(req, timeout=TIMEOUT) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        print(f"  ⚠️  HTTP {e.code} — {url[:70]}", file=sys.stderr)
    except Exception as e:
        print(f"  ⚠️  fetch error — {url[:70]} — {e}", file=sys.stderr)
    return None


# ── Source: Hacker News (via Algolia) ─────────────────────────────────────────

def fetch_hackernews(limit: int = 15) -> list:
    print("🔍 Hacker News (Algolia)...", file=sys.stderr)
    topics = []
    try:
        since = int(time.time()) - 86400
        url = (
            "https://hn.algolia.com/api/v1/search"
            "?query=AI+LLM+machine+learning+GPT+Claude+model"
            f"&tags=story&hitsPerPage={limit}"
            f"&numericFilters=created_at_i>{since},points>5"
        )
        raw = fetch_url(url)
        if not raw:
            return []
        data = json.loads(raw)
        for item in data.get("hits", []):
            oid = item.get("objectID", "")
            topics.append({
                "id": f"hn_{oid}",
                "title": item.get("title", ""),
                "summary": item.get("title", ""),
                "heat_score": min(item.get("points", 0) / 5.0, 100.0),
                "source_count": item.get("num_comments", 0),
                "category": "ai",
                "tags": ["HackerNews"],
                "source_urls": [item.get("url") or f"https://news.ycombinator.com/item?id={oid}"],
                "first_seen": item.get("created_at", now_iso()),
                "updated_at": now_iso(),
                "trend": "rising",
                "source": "hackernews",
            })
        print(f"  ✅ HN: {len(topics)} items", file=sys.stderr)
    except Exception as e:
        print(f"  ❌ HN error: {e}", file=sys.stderr)
    return topics


# ── Source: Reddit ─────────────────────────────────────────────────────────────

def fetch_reddit(limit_per_sub: int = 10) -> list:
    print("🔍 Reddit...", file=sys.stderr)
    subreddits = ["MachineLearning", "artificial", "LocalLLaMA"]
    topics = []
    for sub in subreddits:
        try:
            url = f"https://www.reddit.com/r/{sub}/top.json?t=day&limit={limit_per_sub}"
            raw = fetch_url(url)
            if not raw:
                continue
            data = json.loads(raw)
            for post in data.get("data", {}).get("children", []):
                p = post.get("data", {})
                if p.get("score", 0) < 10:
                    continue
                created = p.get("created_utc", time.time())
                topics.append({
                    "id": f"reddit_{p.get('id', '')}",
                    "title": p.get("title", ""),
                    "summary": (p.get("selftext") or p.get("title", ""))[:250],
                    "heat_score": min(p.get("score", 0) / 20.0, 100.0),
                    "source_count": p.get("num_comments", 0),
                    "category": "ai",
                    "tags": ["Reddit", f"r/{sub}"],
                    "source_urls": [f"https://reddit.com{p.get('permalink', '')}"],
                    "first_seen": datetime.fromtimestamp(created, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "updated_at": now_iso(),
                    "trend": "rising",
                    "source": f"reddit_{sub}",
                })
            print(f"  ✅ r/{sub}: {len([t for t in topics if t['source'] == f'reddit_{sub}'])} items", file=sys.stderr)
        except Exception as e:
            print(f"  ❌ Reddit r/{sub}: {e}", file=sys.stderr)
    return topics


# ── Source: arXiv ──────────────────────────────────────────────────────────────

def fetch_arxiv(limit: int = 10) -> list:
    print("🔍 arXiv...", file=sys.stderr)
    topics = []
    try:
        url = (
            "https://export.arxiv.org/api/query"
            "?search_query=cat:cs.AI+OR+cat:cs.LG+OR+cat:cs.CL"
            "&sortBy=submittedDate&sortOrder=descending"
            f"&max_results={limit}"
        )
        raw = fetch_url(url)
        if not raw:
            return []
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        root = ET.fromstring(raw)
        for entry in root.findall("atom:entry", ns):
            title = (entry.findtext("atom:title", "", ns) or "").strip().replace("\n", " ")
            summary = (entry.findtext("atom:summary", "", ns) or "").strip().replace("\n", " ")[:250]
            published = entry.findtext("atom:published", now_iso(), ns)
            entry_id = (entry.findtext("atom:id", "", ns) or "").split("/")[-1]
            link_el = entry.find("atom:link[@rel='alternate']", ns)
            link = link_el.attrib.get("href", "") if link_el is not None else f"https://arxiv.org/abs/{entry_id}"
            topics.append({
                "id": f"arxiv_{entry_id}",
                "title": title,
                "summary": summary,
                "heat_score": _rss_heat_score(title, published),
                "source_count": 1,
                "category": "ai",
                "tags": ["arXiv", "Research"],
                "source_urls": [link],
                "first_seen": published,
                "updated_at": now_iso(),
                "trend": "stable",
                "source": "arxiv",
            })
        print(f"  ✅ arXiv: {len(topics)} items", file=sys.stderr)
    except Exception as e:
        print(f"  ❌ arXiv error: {e}", file=sys.stderr)
    return topics


# ── Source: RSS feeds ──────────────────────────────────────────────────────────

def _parse_rss(raw: str, source_name: str, limit: int) -> list:
    """Parse RSS 2.0 or Atom feed, return normalized topic list."""
    topics = []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        print(f"  ⚠️  XML parse error ({source_name}): {e}", file=sys.stderr)
        return []

    # Atom feed?
    atom_ns = "http://www.w3.org/2005/Atom"
    entries = root.findall(f"{{{atom_ns}}}entry")
    if entries:
        for entry in entries[:limit]:
            title = (entry.findtext(f"{{{atom_ns}}}title") or "").strip()
            summary = (entry.findtext(f"{{{atom_ns}}}summary") or title).strip()[:250]
            link_el = entry.find(f"{{{atom_ns}}}link")
            link = link_el.attrib.get("href", "") if link_el is not None else ""
            published = entry.findtext(f"{{{atom_ns}}}published") or now_iso()
            topics.append(_make_topic(source_name, title, summary, link, published))
        return topics

    # RSS 2.0
    for item in root.findall(".//item")[:limit]:
        title = (item.findtext("title") or "").strip()
        summary = (item.findtext("description") or title).strip()[:250]
        link = (item.findtext("link") or "").strip()
        pub_date = item.findtext("pubDate") or now_iso()
        topics.append(_make_topic(source_name, title, summary, link, pub_date))
    return topics


def _make_topic(source: str, title: str, summary: str, link: str, published: str) -> dict:
    return {
        "id": f"{source}_{abs(hash(title)) % 1000000}",
        "title": title,
        "summary": summary,
        "heat_score": _rss_heat_score(title, published),
        "source_count": 1,
        "category": "ai",
        "tags": [source],
        "source_urls": [link],
        "first_seen": published,
        "updated_at": now_iso(),
        "trend": "stable",
        "source": source,
    }


def fetch_all_rss(limit_per_feed: int = 8) -> list:
    print("🔍 RSS feeds...", file=sys.stderr)
    feeds = [
        ("https://techcrunch.com/category/artificial-intelligence/feed/", "techcrunch"),
        ("https://venturebeat.com/category/ai/feed/", "venturebeat"),
        ("https://www.theverge.com/rss/index.xml", "theverge"),
    ]
    topics = []
    for url, name in feeds:
        raw = fetch_url(url)
        if raw:
            items = _parse_rss(raw, name, limit_per_feed)
            topics.extend(items)
            print(f"  ✅ {name}: {len(items)} items", file=sys.stderr)
    return topics


# ── Source: HuggingFace Daily Papers ──────────────────────────────────────────

def fetch_huggingface(limit: int = 10) -> list:
    print("🔍 HuggingFace Daily Papers...", file=sys.stderr)
    # Try official papers RSS, fall back to Wired AI RSS if unavailable
    for url in [
        "https://huggingface.co/papers/rss",
        "https://www.wired.com/feed/tag/artificial-intelligence/latest/rss",
    ]:
        raw = fetch_url(url)
        if raw:
            source = "huggingface" if "huggingface" in url else "wired"
            topics = _parse_rss(raw, source, limit)
            if topics:
                for t in topics:
                    t["heat_score"] = 70.0
                    t["tags"] = ["HuggingFace" if source == "huggingface" else "Wired", "Research"]
                print(f"  ✅ {source}: {len(topics)} items", file=sys.stderr)
                return topics
    print("  ⚠️  HuggingFace: all URLs failed", file=sys.stderr)
    return []


# ── Source: Y Combinator Blog ──────────────────────────────────────────────────

def fetch_ycombinator(limit: int = 8) -> list:
    print("🔍 Y Combinator...", file=sys.stderr)
    raw = fetch_url("https://www.ycombinator.com/blog/rss.xml")
    if not raw:
        return []
    topics = _parse_rss(raw, "ycombinator", limit)
    print(f"  ✅ YC: {len(topics)} items", file=sys.stderr)
    return topics


# ── Source: 国内科技热榜 (daily-hot-news skill) ───────────────────────────────

# AI 相关关键词过滤（标题命中即保留）
_AI_FILTER_KW = [
    # 英文
    "ai", "gpt", "llm", "claude", "gemini", "deepseek", "chatgpt", "openai",
    "copilot", "agent", "model", "sora", "midjourney", "stable diffusion",
    "anthropic", "nvidia", "gpu", "robot", "autonomous", "machine learning",
    "neural", "transformer", "diffusion", "reasoning", "inference",
    "open source", "open-source",
    # 中文
    "大模型", "模型", "人工智能", "智能", "AI", "GPT", "深度学习",
    "芯片", "算力", "机器人", "自动驾驶", "无人驾驶", "开源",
    "文心", "通义", "千问", "智谱", "月之暗面", "Kimi", "豆包",
    "Claude", "Gemini", "DeepSeek", "Copilot", "ChatGPT",
    "发布", "上线", "融资", "收购", "突破", "里程碑",
]

# 要抓取的国内科技/技术平台
_DOMESTIC_PLATFORMS = [
    "ithome",        # IT之家
    "juejin",        # 稀土掘金
    "geekpark",      # 极客公园
    "36kr",          # 36氪
    "huxiu",         # 虎嗅
    "csdn",          # CSDN
    "51cto",         # 51CTO
    "sspai",         # 少数派
    "dgtle",         # 数字尾巴
    "smzdm",         # 什么值得买
    "hellogithub",   # HelloGitHub
    "github",        # GitHub Trending
]


def _is_ai_related(title: str) -> bool:
    """判断标题是否与 AI/科技相关"""
    t = title.lower()
    for kw in _AI_FILTER_KW:
        if kw.lower() in t:
            return True
    return False


def _hot_to_score(hot_str: str, rank: int = 0, platform_id: str = "") -> float:
    """将热榜排名+热度值转换为 0-100 的分数

    策略：能上榜即有热度，以排名为主、热值为辅。
    不同平台热度量级差异极大（36氪"23" vs 微博"5000万"），
    无法统一映射，因此以排名作为核心评分维度。
    """
    # 1. 排名基础分：第1名=71, 第10名=48, 与国外源 _rss_heat_score(50-95) 对齐
    if rank > 0:
        rank_score = max(48.0, 71.0 - (rank - 1) * 2.6)
    else:
        rank_score = 55.0

    # 2. 热值微调：仅在同一量级内做微调（±5分）
    heat_bonus = 0.0
    if hot_str:
        try:
            s = str(hot_str).strip()
            multiplier = 1.0
            if "亿" in s:
                multiplier = 100000000.0
                s = s.replace("亿", "")
            elif "万" in s:
                multiplier = 10000.0
                s = s.replace("万", "")
            s = "".join(c for c in s if c.isdigit() or c == ".")
            if s:
                val = float(s) * multiplier
                # 只做小幅微调
                if val >= 10000000:
                    heat_bonus = 5.0
                elif val >= 1000000:
                    heat_bonus = 3.0
                elif val >= 100000:
                    heat_bonus = 2.0
                elif val >= 10000:
                    heat_bonus = 1.0
                elif val >= 1000:
                    heat_bonus = 0.5
        except Exception:
            pass

    # 3. 平台权重微调（高质量 AI 内容平台加分）
    platform_bonus = {
        "geekpark": 3.0,    # 极客公园 AI 深度好
        "51cto": 2.0,       # 51CTO AI 技术实战
        "juejin": 2.0,      # 掘金 AI 开发者
        "hellogithub": 2.0, # HelloGitHub 开源
        "36kr": 1.0,        # 36氪 行业
        "ithome": 1.0,      # IT之家 速报
        "huxiu": 1.0,       # 虎嗅 深度
        "csdn": 0.0,        # CSDN 质量参差
    }.get(platform_id, 0.0)

    return min(95.0, rank_score + heat_bonus + platform_bonus)


def fetch_domestic_hot(limit_per_platform: int = 10) -> list:
    """从国内科技热榜抓取 AI 相关话题（基于 daily-hot-news skill）"""
    print("🔍 国内科技热榜 (daily-hot-news)...", file=sys.stderr)

    try:
        import importlib.util
        skill_dir = Path.home() / ".claude" / "skills" / "daily-hot-news"
        fetcher_path = skill_dir / "fetcher.py"
        if not fetcher_path.exists():
            # 尝试旧路径
            skill_dir = Path.home() / ".claude" / "skills" / "daily-hot-news"
            fetcher_path = skill_dir / "fetcher.py"
        if not fetcher_path.exists():
            print("  ⚠️  daily-hot-news skill 未安装，跳过国内热榜", file=sys.stderr)
            return []

        spec = importlib.util.spec_from_file_location("daily_hot_news_fetcher", fetcher_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        if not mod.is_available():
            print("  ⚠️  daily-hot-news engine 不可用，跳过国内热榜", file=sys.stderr)
            return []

    except Exception as e:
        print(f"  ⚠️  daily-hot-news 加载失败: {e}", file=sys.stderr)
        return []

    all_topics = []
    for platform_id in _DOMESTIC_PLATFORMS:
        try:
            result = mod.fetch(platform_id)
            if not result or result.get("error"):
                err_msg = result.get("message", "unknown") if result else "None"
                print(f"  ⚠️  {platform_id}: {err_msg}", file=sys.stderr)
                continue

            platform_name = result.get("platform", platform_id)
            items = result.get("data", [])
            ai_count = 0

            for item in items[:limit_per_platform]:
                title = item.get("title", "")
                if not title or not _is_ai_related(title):
                    continue

                ai_count += 1
                hot_str = item.get("hot", "")
                rank = item.get("rank", 0)
                heat = _hot_to_score(hot_str, rank=rank, platform_id=platform_id)

                # first_seen: 用热榜更新时间（如有），否则按排名估算
                # 排名越靠前意味着越新鲜，排名靠后的可能已存在数小时
                update_time = result.get("update_time", "")
                if update_time:
                    first_seen = update_time.replace(" ", "T")
                    if "+" not in first_seen and "Z" not in first_seen:
                        first_seen += "+08:00"
                else:
                    # 按排名估算：rank1≈1小时前，rank10≈10小时前
                    from datetime import timedelta
                    hours_ago = min(rank if rank > 0 else 5, 12)
                    est_time = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
                    first_seen = est_time.strftime("%Y-%m-%dT%H:%M:%SZ")

                all_topics.append({
                    "id": f"domestic_{platform_id}_{item.get('rank', 0)}",
                    "title": title,
                    "summary": item.get("desc", "") or title,
                    "heat_score": heat,
                    "source_count": 1,
                    "category": "ai",
                    "tags": [platform_name, "国内"],
                    "source_urls": [item.get("url", "")],
                    "first_seen": first_seen,
                    "updated_at": now_iso(),
                    "trend": "rising",
                    "source": f"domestic_{platform_id}",
                })

            print(f"  ✅ {platform_name}: {ai_count}/{len(items[:limit_per_platform])} AI 相关",
                  file=sys.stderr)

        except Exception as e:
            print(f"  ⚠️  {platform_id} error: {e}", file=sys.stderr)

    print(f"  📊 国内热榜合计: {len(all_topics)} 条 AI 相关话题", file=sys.stderr)
    return all_topics


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Multi-source AI news fetcher")
    parser.add_argument("--limit", type=int, default=30, help="Max topics to output")
    
    parser.add_argument("--period", default="24h", help="(unused, for compat)")
    parser.add_argument("--category", default="ai", help="(unused, for compat)")
    args = parser.parse_args()

    print(f"🚀 多源抓取开始 | limit={args.limit}", file=sys.stderr)

    all_topics: list = []
    all_topics.extend(fetch_hackernews(limit=15))
    all_topics.extend(fetch_reddit(limit_per_sub=10))
    all_topics.extend(fetch_arxiv(limit=10))
    all_topics.extend(fetch_all_rss(limit_per_feed=8))
    all_topics.extend(fetch_huggingface(limit=10))
    all_topics.extend(fetch_ycombinator(limit=8))
    all_topics.extend(fetch_domestic_hot(limit_per_platform=10))

    # Deduplicate by title prefix (first 60 chars, case-insensitive)
    seen: set = set()
    deduped: list = []
    for t in all_topics:
        key = t["title"].lower().strip()[:60]
        if key and key not in seen:
            seen.add(key)
            deduped.append(t)

    # 过滤超过 48h 的旧闻（first_seen 字段，RFC 2822 格式）
    from email.utils import parsedate_to_datetime as _parse_rfc2822
    fresh: list = []
    stale_count = 0
    now_utc = datetime.now(timezone.utc)
    for t in deduped:
        fs = t.get("first_seen", "")
        try:
            pub_dt = _parse_rfc2822(fs)
            age_h = (now_utc - pub_dt).total_seconds() / 3600
            if age_h <= 48:
                fresh.append(t)
            else:
                stale_count += 1
        except Exception:
            fresh.append(t)  # 无法解析时间则保留
    print(f"⏱️  48h 过滤：移除 {stale_count} 条旧闻，剩余 {len(fresh)} 条", file=sys.stderr)
    deduped = fresh


    # Sort by heat_score descending
    deduped.sort(key=lambda x: x["heat_score"], reverse=True)

    result = deduped[:args.limit]

    print(
        f"✅ 抓取 {len(all_topics)} 条 → 去重 {len(deduped)} 条 → 输出 {len(result)} 条",
        file=sys.stderr,
    )

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
