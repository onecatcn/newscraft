#!/usr/bin/env python3
"""llm_review.py -- 多模型 LLM 内容审稿（面向中国AI技术从业者视角）

用法:
    python3 scripts/llm_review.py --draft <文章路径> --output <结果JSON路径>

调用 ERNIE-4.5 + ERNIE-5.0 对文章进行内容质量评审，
提示词固定为「请基于中国AI技术从业者的角度审读本文」。
结果写入 JSON 并打印格式化摘要。
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

try:
    from openai import OpenAI
except ImportError:
    print("❌ 缺少 openai 库，请执行：pip install openai", file=sys.stderr)
    sys.exit(1)

AI_STUDIO_BASE_URL = "https://aistudio.baidu.com/llm/lmapi/v3"

REVIEW_SYSTEM_PROMPT = "你是一位服务于中国AI技术社区的资深科技媒体编辑。"

REVIEW_USER_PROMPT = """请基于中国AI技术从业者的角度审读本文。

请输出 JSON 格式的评审意见（不要输出任何 JSON 以外的内容）：
{{
  "score": <1-10的整数，综合评分>,
  "strengths": ["亮点1", "亮点2"],
  "weaknesses": ["问题1", "问题2"],
  "china_relevance": "<对国内AI从业者的实际参考价值，1-2句>",
  "suggestion": "<最重要的一条修改建议>",
  "verdict": "<approve|revise|reject>"
}}

以下是待审文章：

{article}
"""

MODELS = [
    ("ERNIE-4.5", "ernie-4.5-turbo-128k"),
    ("ERNIE-5.0", "ernie-5.0-thinking-preview"),
]


def get_client():
    """返回配置好的 AI Studio OpenAI 兼容客户端。"""
    api_key = os.environ.get("AI_STUDIO_API_KEY", "")
    if not api_key:
        print("❌ 缺少 AI_STUDIO_API_KEY 环境变量", file=sys.stderr)
        sys.exit(1)
    return OpenAI(base_url=AI_STUDIO_BASE_URL, api_key=api_key)


def parse_json_from_text(text: str) -> dict:
    """从模型输出中提取 JSON 对象。"""
    text = text.strip()
    # 去除 Markdown 代码块包装
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        return json.loads(m.group())
    raise ValueError("未找到 JSON 内容")


def review_with_model(client, model_id: str, article: str) -> dict:
    """调用单个模型进行审稿，返回解析后的评审字典。"""
    resp = client.chat.completions.create(
        model=model_id,
        max_tokens=1024,
        messages=[
            {"role": "system", "content": REVIEW_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": REVIEW_USER_PROMPT.format(article=article[:4000]),
            },
        ],
    )
    text = resp.choices[0].message.content or ""
    return parse_json_from_text(text)


def print_review_summary(title: str, reviews: dict) -> None:
    """打印格式化的审稿摘要。"""
    print(f"\n{'─'*60}")
    print(f"📄 {title}")
    print(f"{'─'*60}")
    for model_name, result in reviews.items():
        if "error" in result:
            print(f"  [{model_name}] ❌ {result['error']}")
            continue
        score   = result.get("score", "?")
        verdict = result.get("verdict", "?")
        verdict_icon = {"approve": "✅", "revise": "✏️", "reject": "❌"}.get(verdict, "❓")
        print(f"  [{model_name}] {verdict_icon} {score}/10  verdict={verdict}")
        for s in result.get("strengths", []):
            print(f"    👍 {s}")
        for w in result.get("weaknesses", []):
            print(f"    ⚠️  {w}")
        if result.get("china_relevance"):
            print(f"    🇨🇳 {result['china_relevance']}")
        if result.get("suggestion"):
            print(f"    💡 建议：{result['suggestion']}")


def main():
    parser = argparse.ArgumentParser(description="多模型 LLM 内容审稿")
    parser.add_argument("--draft",  required=True, help="文章 Markdown 文件路径")
    parser.add_argument("--output", required=True, help="审稿结果 JSON 输出路径")
    args = parser.parse_args()

    draft_path = Path(args.draft)
    if not draft_path.exists():
        print(f"❌ 文章文件不存在: {draft_path}", file=sys.stderr)
        sys.exit(1)

    article = draft_path.read_text(encoding="utf-8")
    title   = article.split("\n")[0].lstrip("# ").strip()

    client  = get_client()
    results = {"title": title, "draft": str(draft_path), "reviews": {}}

    print(f"🤖 LLM 内容审稿（基于中国AI技术从业者视角）")
    print(f"   文章：{title}")

    for model_name, model_id in MODELS:
        print(f"  [{model_name}] 审稿中...", end="", flush=True)
        try:
            review = review_with_model(client, model_id, article)
            results["reviews"][model_name] = review
            score   = review.get("score", "?")
            verdict = review.get("verdict", "?")
            print(f" {score}/10 [{verdict}]")
        except Exception as e:
            results["reviews"][model_name] = {"error": str(e)}
            print(f" ❌ {e}")

    # 写入 JSON
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 打印摘要
    print_review_summary(title, results["reviews"])

    # 综合判断
    verdicts = [
        r.get("verdict", "revise")
        for r in results["reviews"].values()
        if "error" not in r
    ]
    if all(v == "approve" for v in verdicts):
        overall = "approve"
        print(f"\n✅ 综合结论：两模型均通过，建议发布")
    elif any(v == "reject" for v in verdicts):
        overall = "reject"
        print(f"\n❌ 综合结论：有模型建议拒绝，请认真检查")
    else:
        overall = "revise"
        print(f"\n✏️  综合结论：建议修改后再发布")

    results["overall_verdict"] = overall
    Path(args.output).write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"\n📋 详细结果已保存：{args.output}")


if __name__ == "__main__":
    main()
