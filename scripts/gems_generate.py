#!/usr/bin/env python3
"""gems_generate.py — 使用百度 AI Studio ernie-image-turbo 生成配图

用法:
    python3 gems_generate.py \
        --prompts-dir 05_images/ \
        --output-dir 05_images/

功能:
    1. 读取 cover_prompt.txt 和 inline_prompts.txt 中的 prompt
    2. 调用 AI Studio ernie-image-turbo API 生成图片
    3. 保存 cover.png + inline_1.png 到输出目录
    4. 输出生成结果 JSON

环境变量:
    AI_STUDIO_API_KEY   百度 AI Studio API Key
                        获取地址: https://aistudio.baidu.com/account/accessToken

依赖:
    pip install openai
"""

import argparse
import base64
import json
import os
import re
import sys
from pathlib import Path

try:
    from openai import OpenAI
except ImportError:
    print("❌ 缺少依赖: pip install openai", file=sys.stderr)
    sys.exit(1)

AI_STUDIO_BASE_URL = "https://aistudio.baidu.com/llm/lmapi/v3"
DEFAULT_MODEL = "ernie-image-turbo"

# 封面 1:1，正文 16:9（API 支持的最近尺寸）
SIZE_COVER = "1024x1024"
SIZE_INLINE = "1376x768"


def get_client() -> OpenAI:
    api_key = os.environ.get("AI_STUDIO_API_KEY", "")
    if not api_key:
        print("❌ 错误: AI_STUDIO_API_KEY 未设置", file=sys.stderr)
        print("   获取地址: https://aistudio.baidu.com/account/accessToken", file=sys.stderr)
        sys.exit(1)
    return OpenAI(api_key=api_key, base_url=AI_STUDIO_BASE_URL)


def generate_image(client: OpenAI, prompt: str, size: str, model: str = DEFAULT_MODEL) -> bytes:
    """调用 ernie-image-turbo 生成单张图片，返回 PNG 字节。"""
    resp = client.images.generate(
        model=model,
        prompt=prompt,
        n=1,
        response_format="b64_json",
        size=size,
        extra_body={
            "use_pe": True,
            "num_inference_steps": 8,
            "guidance_scale": 1.0,
        },
    )
    return base64.b64decode(resp.data[0].b64_json)


def parse_cover_prompt(prompts_dir: str) -> str:
    """从 cover_prompt.txt 解析封面图 prompt"""
    path = Path(prompts_dir) / "cover_prompt.txt"
    if not path.exists():
        print(f"❌ 未找到封面 prompt 文件: {path}", file=sys.stderr)
        sys.exit(1)

    content = path.read_text(encoding="utf-8")
    match = re.search(r"## Prompt:\s*\n\n(.+?)(?:\n\n##|\Z)", content, re.DOTALL)
    if match:
        return match.group(1).strip()
    lines = [l for l in content.split("\n") if l.strip() and not l.startswith("#")]
    return " ".join(lines).strip()


def parse_inline_prompts(prompts_dir: str) -> list:
    """从 inline_prompts.txt 解析正文配图 prompt（仅取第 1 张）"""
    path = Path(prompts_dir) / "inline_prompts.txt"
    if not path.exists():
        print(f"⚠️  未找到正文 prompt 文件: {path}，跳过正文图生成", file=sys.stderr)
        return []

    content = path.read_text(encoding="utf-8")
    match = re.search(r"\*\*Prompt:\*\*\s*\n(.+?)(?:\n\n\*\*规格|\Z)", content, re.DOTALL)
    if match:
        return [match.group(1).strip()]
    return []


def main():
    parser = argparse.ArgumentParser(description="使用 ernie-image-turbo 生成配图")
    parser.add_argument("--prompts-dir", required=True, help="含 prompt 文件的目录")
    parser.add_argument("--output-dir", required=True, help="图片输出目录")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"图片模型（默认 {DEFAULT_MODEL}）")
    args = parser.parse_args()

    client = get_client()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    results = {"cover": None, "inline": []}

    # ── 封面图 ──
    print("🎨 解析封面 prompt...", file=sys.stderr)
    cover_prompt = parse_cover_prompt(args.prompts_dir)
    print(f"  Prompt: {cover_prompt[:80]}...", file=sys.stderr)
    print(f"🖼️  生成封面图 ({SIZE_COVER})...", file=sys.stderr)
    try:
        cover_bytes = generate_image(client, cover_prompt, SIZE_COVER, args.model)
        cover_path = output_dir / "cover.png"
        cover_path.write_bytes(cover_bytes)
        print(f"  ✅ 封面图已保存: {cover_path} ({len(cover_bytes)} bytes)", file=sys.stderr)
        results["cover"] = {"filename": "cover.png", "path": str(cover_path), "size_bytes": len(cover_bytes)}
    except Exception as e:
        print(f"  ❌ 封面图生成失败: {e}", file=sys.stderr)
        results["cover_error"] = str(e)

    # ── 正文图 ──
    print("🎨 解析正文 prompt...", file=sys.stderr)
    inline_prompts = parse_inline_prompts(args.prompts_dir)
    if inline_prompts:
        prompt = inline_prompts[0]
        print(f"  Prompt: {prompt[:80]}...", file=sys.stderr)
        print(f"🖼️  生成正文图 ({SIZE_INLINE})...", file=sys.stderr)
        try:
            inline_bytes = generate_image(client, prompt, SIZE_INLINE, args.model)
            inline_path = output_dir / "inline_1.png"
            inline_path.write_bytes(inline_bytes)
            print(f"  ✅ 正文图已保存: {inline_path} ({len(inline_bytes)} bytes)", file=sys.stderr)
            results["inline"].append({"filename": "inline_1.png", "path": str(inline_path), "size_bytes": len(inline_bytes)})
        except Exception as e:
            print(f"  ❌ 正文图生成失败: {e}", file=sys.stderr)
            results["inline_error"] = str(e)
    else:
        print("  ⚠️  无正文 prompt，跳过", file=sys.stderr)

    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
