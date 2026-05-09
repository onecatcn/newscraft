#!/usr/bin/env python3
"""deepreport_review.py -- 三阶段深度报道审稿（事实+逻辑+深度）

用法:
    python3 scripts/deepreport_review.py <文章路径>
"""

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
MODEL = "ernie-5.0-thinking-preview"

# ── 三阶段 Review Prompt ──────────────────────────────────────────

SYSTEM_PROMPT = """你是一位资深科技媒体审稿编辑，专注于深度报道的质量把关。你必须严格基于文章内容进行审查，不编造信息。"""

FACT_CHECK_PROMPT = """## 阶段一：事实性检查

请逐条审查以下文章中的可验证声明，对每一条给出判定。

检查维度：
- 数据准确性（数字、成绩、金额等是否有误）
- 时间线一致性（同一事件在不同段落的描述是否矛盾）
- 公司名称/产品名称准确性（拼写、官方名称）
- 引述来源可靠性（"宣布""递交"等是否有公开报道可查）
- 对比公平性（对比条件是否对等）

输出 JSON 格式（不要输出 JSON 以外的内容）：
{{
  "items": [
    {{"claim": "原文陈述", "verdict": "✅已验证|⚠️存疑|❌有误", "detail": "验证详情和来源", "severity": "致命|高|中"}}
  ],
  "summary": "事实性检查总结，列出必须修改的致命问题"
}}

以下是待审文章：

{article}"""

LOGIC_REVIEW_PROMPT = """## 阶段二：观点逻辑分析

请审查以下文章的核心论点，检查逻辑问题。

检查维度：
- 因果关系：是否把相关性当因果？推理链条是否有跳跃？
- 以偏概全：是否用单一案例推出普遍结论？
- 暗示性因果：两件事放在一起是否暗示了不成立的因果？
- 幸存者偏差：是否只看成功案例？
- 偷换概念：论据和结论讨论的是否是同一件事？
- 选择性引用：是否只引用支持论点的证据，忽略反面？

输出 JSON 格式（不要输出 JSON 以外的内容）：
{{
  "issues": [
    {{"type": "因果关系|以偏概全|暗示性因果|幸存者偏差|偷换概念|选择性引用", "location": "原文相关段落关键词", "description": "逻辑问题说明", "severity": "高|中"}}
  ],
  "summary": "逻辑分析总结，指出最严重的逻辑问题"
}}

以下是待审文章：

{article}"""

DEPTH_REVIEW_PROMPT = """## 阶段三：观点深度提升

请评估以下文章每个核心观点的深度。深度报道的价值在于提供读者自己看不出的洞察。

对每个核心观点评估三个维度：
1. 信息增量：这个观点是"大家都知道的共识"还是"需要分析才能看到的"？
2. 分析链条：结论是直接给出的，还是经过至少2层推理得出的？
3. 可行动性：读者看完后，对这件事的理解有没有实质改变？

输出 JSON 格式（不要输出 JSON 以外的内容）：
{{
  "opinions": [
    {{"opinion": "文中观点摘要", "depth": "浅|中|深", "gap": "深度不足的具体原因", "suggestion": "提升深度的具体方向"}}
  ],
  "summary": "深度提升总结，列出最有价值提升方向"
}}

以下是待审文章：

{article}"""


def get_client() -> OpenAI:
    api_key = os.environ.get("AI_STUDIO_API_KEY", "")
    if not api_key:
        print("❌ 未配置 AI_STUDIO_API_KEY", file=sys.stderr)
        sys.exit(1)
    return OpenAI(api_key=api_key, base_url=AI_STUDIO_BASE_URL)


def call_ernie(client: OpenAI, prompt: str, article: str) -> str:
    full_prompt = prompt.format(article=article[:6000])
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": full_prompt},
        ],
        temperature=0.3,
        max_tokens=4096,
    )
    # thinking 模型可能把内容放在 reasoning_content 或 content
    choice = resp.choices[0]
    msg = choice.message
    content = msg.content or ""
    # 如果 content 为空，检查 reasoning_content
    if not content and hasattr(msg, "reasoning_content") and msg.reasoning_content:
        content = msg.reasoning_content
    return content


def parse_json(text: str) -> dict:
    text = text.strip()
    # 去除 Markdown 代码块包装
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    # 尝试直接解析
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        json_str = m.group()
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            pass
    # 修复常见 JSON 问题：尾随逗号、单引号
    text_fixed = text
    text_fixed = re.sub(r",\s*([}\]])", r"\1", text_fixed)  # 去尾随逗号
    text_fixed = text_fixed.replace("'", '"')  # 单引号→双引号
    m = re.search(r"\{[\s\S]*\}", text_fixed)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass
    raise ValueError(f"无法解析 JSON，原始输出前500字: {text[:500]}")


def print_stage(stage_name: str, result: dict):
    print(f"\n{'━'*60}")
    print(f"  {stage_name}")
    print(f"{'━'*60}")

    if "items" in result:
        for item in result["items"]:
            icon = {"✅已验证": "✅", "⚠️存疑": "⚠️", "❌有误": "❌"}.get(item.get("verdict", ""), "❓")
            print(f"  {icon} [{item.get('severity', '?')}] {item.get('claim', '')}")
            print(f"     → {item.get('detail', '')}")
    elif "issues" in result:
        for issue in result["issues"]:
            print(f"  ⚡ [{issue.get('severity', '?')}] {issue.get('type', '')} — {issue.get('location', '')}")
            print(f"     → {issue.get('description', '')}")
    elif "opinions" in result:
        for op in result["opinions"]:
            depth_icon = {"浅": "🔴", "中": "🟡", "深": "🟢"}.get(op.get("depth", ""), "⚪")
            print(f"  {depth_icon} [{op.get('depth', '?')}] {op.get('opinion', '')}")
            print(f"     差距: {op.get('gap', '')}")
            print(f"     建议: {op.get('suggestion', '')}")

    print(f"\n  📋 {result.get('summary', '无总结')}")


def main():
    if len(sys.argv) < 2:
        print(f"用法: python3 {sys.argv[0]} <文章Markdown路径>", file=sys.stderr)
        sys.exit(1)

    draft_path = Path(sys.argv[1])
    if not draft_path.exists():
        print(f"❌ 文件不存在: {draft_path}", file=sys.stderr)
        sys.exit(1)

    article = draft_path.read_text(encoding="utf-8")
    title = article.split("\n")[0].lstrip("# ").strip()

    print(f"🤖 ERNIE-5.0 三阶段深度报道审稿")
    print(f"   文章：{title}")
    print(f"   模型：{MODEL}")

    client = get_client()

    stages = [
        ("阶段一：事实性检查", FACT_CHECK_PROMPT),
        ("阶段二：观点逻辑分析", LOGIC_REVIEW_PROMPT),
        ("阶段三：观点深度提升", DEPTH_REVIEW_PROMPT),
    ]

    all_results = {"title": title, "model": MODEL, "stages": {}}

    for stage_name, prompt in stages:
        print(f"\n  [{stage_name}] 审稿中...", end="", flush=True)
        try:
            raw = call_ernie(client, prompt, article)
            result = parse_json(raw)
            all_results["stages"][stage_name] = result
            print(" 完成")
            print_stage(stage_name, result)
        except Exception as e:
            all_results["stages"][stage_name] = {"error": str(e)}
            print(f" ❌ {e}")

    # 保存结果
    output_path = draft_path.parent / f"{draft_path.stem}_review.json"
    output_path.write_text(
        json.dumps(all_results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n📋 详细结果已保存：{output_path}")


if __name__ == "__main__":
    main()
