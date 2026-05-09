#!/usr/bin/env python3
"""web_search.py — 网络搜索 + URL 正文抓取（基于 realtime-search skill）

封装 realtime-search skill 的 search 二进制和 fetch.py，供 autopub generate 阶段
在写文章前补充网络素材（处理 TechCrunch/外网 block 等问题）。

用法:
    # 搜索并返回摘要列表（默认 brave 引擎，适合英文 AI 新闻）
    python3 web_search.py --query "Factory AI coding startup $1.5B funding 2024" [--max-results 5]

    # 使用百度引擎（适合中文内容）
    python3 web_search.py --query "Luma AI 视频生成" --engine baidu

    # 抓取单个 URL 正文
    python3 web_search.py --fetch-url "https://techcrunch.com/..."

    # 搜索 + 自动抓取 top-N 结果正文
    python3 web_search.py --query "Anthropic CPO Figma board 2024" --fetch-top 2

输出: JSON 到 stdout
    搜索模式:  {"results": [{"title", "url", "description", "published"}, ...]}
    抓取模式:  {"url": "...", "title": "...", "text": "..."}
    组合模式:  {"results": [...], "fetched": [{"url", "title", "text"}, ...]}

依赖: realtime-search skill（已内置，无需额外 API Key）
"""

import argparse
import json
import os
import subprocess
import sys
import time

# realtime-search skill 路径（相对于本脚本向上两级）
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_SKILL_ROOT = os.path.join(_SCRIPT_DIR, "..", "..", "realtime-search", "scripts")
SEARCH_BIN = os.path.join(_SKILL_ROOT, "search")
FETCH_PY = os.path.join(_SKILL_ROOT, "fetch.py")


def search(query: str, max_results: int = 5, engine: str = "brave",
           freshness: str = "") -> list[dict]:
    """调用 realtime-search search 二进制，返回搜索结果列表。"""
    cmd = [SEARCH_BIN, query, "--count", str(max_results), "--engine", engine]
    if freshness:
        cmd += ["--freshness", freshness]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            print(f"[web_search] search error: {result.stderr[:200]}", file=sys.stderr)
            return []
        data = json.loads(result.stdout)
        return data.get("results", [])
    except subprocess.TimeoutExpired:
        print("[web_search] search timeout", file=sys.stderr)
        return []
    except Exception as e:
        print(f"[web_search] search exception: {e}", file=sys.stderr)
        return []


def fetch(url: str, max_chars: int = 4000) -> dict:
    """调用 realtime-search fetch.py 抓取 URL 正文。"""
    cmd = [sys.executable, FETCH_PY, url, "--max-chars", str(max_chars)]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return {"url": url, "title": "", "text": "", "error": result.stderr[:200]}
        data = json.loads(result.stdout)
        return {
            "url": url,
            "title": data.get("title", ""),
            "text": data.get("text", "")[:max_chars],
            "error": None,
        }
    except subprocess.TimeoutExpired:
        return {"url": url, "title": "", "text": "", "error": "timeout"}
    except Exception as e:
        return {"url": url, "title": "", "text": "", "error": str(e)}


def main():
    parser = argparse.ArgumentParser(description="网络搜索 + URL 正文抓取（基于 realtime-search skill）")
    parser.add_argument("--query", "-q", help="搜索关键词")
    parser.add_argument("--max-results", "-n", type=int, default=5, help="最多返回几条搜索结果")
    parser.add_argument("--engine", default="brave", choices=["brave", "baidu"],
                        help="搜索引擎：brave（英文，默认）或 baidu（中文）")
    parser.add_argument("--freshness", default="", choices=["", "week", "month", "semiyear", "year"],
                        help="时间过滤")
    parser.add_argument("--fetch-url", help="直接抓取此 URL 的正文")
    parser.add_argument("--fetch-top", type=int, default=0, help="搜索后自动抓取前 N 条结果正文")
    parser.add_argument("--max-chars", type=int, default=4000, help="每条正文最大字符数")
    args = parser.parse_args()

    output = {}

    # 纯抓取模式
    if args.fetch_url and not args.query:
        result = fetch(args.fetch_url, args.max_chars)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    # 搜索模式
    if args.query:
        results = search(args.query, args.max_results, args.engine, args.freshness)
        output["results"] = results
        print(f"[web_search] 搜索 '{args.query}' ({args.engine}) → {len(results)} 条结果", file=sys.stderr)

        if args.fetch_top > 0:
            fetched = []
            for r in results[: args.fetch_top]:
                print(f"[web_search] 抓取: {r.get('url', '')}", file=sys.stderr)
                fetched.append(fetch(r["url"], args.max_chars))
                time.sleep(0.3)
            output["fetched"] = fetched

    # 额外抓取（组合模式）
    if args.fetch_url and args.query:
        output["extra_fetch"] = fetch(args.fetch_url, args.max_chars)

    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
