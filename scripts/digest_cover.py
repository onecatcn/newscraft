#!/usr/bin/env python3
"""digest_cover.py — 每日AI速递固定风格封面图生成

两步流程：
  1. ERNIE-image 生成 1024x1024 抽象科技纹理背景（明亮科技风，缓存 30 天）
  2. Pillow 叠加文字层（标题、头条、分类统计、日期）

背景图固定一张，缓存到 --cache-dir，默认 30 天过期，
过期或首次运行时才调用 ERNIE-image API，日常只做 Pillow 叠字，不消耗 token。

用法:
    # 完整流程（自动使用缓存背景，缓存过期才调 API）
    python3 digest_cover.py \
        --topics 01_topics.json \
        --output cover.png

    # 强制重新生成背景（忽略缓存）
    python3 digest_cover.py \
        --topics 01_topics.json \
        --output cover.png \
        --force-regen

    # 已有背景图，只叠字
    python3 digest_cover.py \
        --topics 01_topics.json \
        --base-image cover_base.png \
        --output cover.png

    # 只生成背景 prompt（不调用 API）
    python3 digest_cover.py \
        --topics 01_topics.json \
        --prompt-only

依赖:
    pip install Pillow openai
"""

import argparse
import base64
import io
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("❌ 缺少依赖: pip install Pillow", file=sys.stderr)
    sys.exit(1)

# ── 字体回退 ──

FONT_PATHS = [
    "/System/Library/Fonts/STHeiti Medium.ttc",                          # macOS
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",            # Linux (apt fonts-noto-cjk)
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",            # Linux (alt)
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",                 # Linux (alt 2)
]

BOLD_FONT_PATHS = [
    "/System/Library/Fonts/STHeiti Medium.ttc",                          # macOS (same, STHeiti has weight)
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",              # Linux bold
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",              # Linux bold alt
]

# ── 封面布局常量 ──

COVER_SIZE = 1024

# 颜色
COLOR_TITLE_EN = (74, 144, 217)      # #4A90D9
COLOR_TITLE_CN = (26, 26, 46)        # #1a1a2e
COLOR_HEADLINE = (51, 51, 51)        # #333
COLOR_SUBHEADLINE = (102, 102, 102)  # #666
COLOR_DATE = (153, 153, 153)         # #999
COLOR_DIVIDER_START = (74, 144, 217)  # #4A90D9
COLOR_DIVIDER_END = (155, 89, 182)    # #9B59B6
COLOR_STATS_BG = (240, 245, 255)     # 浅蓝背景 pill
COLOR_STATS_TEXT = (51, 51, 51)      # pill 文字色

# ── ERNIE-image 配置 ──

AI_STUDIO_BASE_URL = "https://aistudio.baidu.com/llm/lmapi/v3"
DEFAULT_MODEL = "ernie-image-turbo"
IMAGE_SIZE = "1024x1024"

# 固定背景 prompt（明亮科技风，速递文章统一使用）
BG_PROMPT = (
    "Bright and clean tech illustration, light gradient background from #e8f4fd to #f0f0ff, "
    "subtle geometric patterns, floating circuit nodes with soft glow, "
    "abstract data flow lines in light blue and lavender, "
    "white space for text overlay area in upper half, "
    "minimalist professional design, modern AI newsletter aesthetic, "
    "no text, no letters, no characters, no words, "
    "1:1 aspect ratio, high quality digital art"
)

# 分类 emoji 和中文名（与 daily_digest.py 一致）
CATEGORY_INFO = {
    "产品": {"emoji": "🚀", "name": "产品"},
    "模型": {"emoji": "🧠", "name": "模型"},
    "研究": {"emoji": "🔬", "name": "研究"},
    "行业": {"emoji": "📊", "name": "行业"},
    "开源": {"emoji": "🔓", "name": "开源"},
    "硬件": {"emoji": "⚡", "name": "硬件"},
    "机器人": {"emoji": "🤖", "name": "机器人"},
}

# 分类优先级（与 daily_digest.py 一致）
CATEGORY_PRIORITY = ["产品", "模型", "研究", "行业", "开源", "硬件", "机器人"]


def _find_font(paths: list, size: int) -> ImageFont.FreeTypeFont:
    """查找可用字体，未找到则回退到默认字体。"""
    for p in paths:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    # 最终回退：Pillow 默认字体
    print(f"⚠️  未找到中文字体，使用默认字体（可能无法渲染中文）", file=sys.stderr)
    return ImageFont.load_default()


def _classify_topic(topic: dict) -> str:
    """话题分类 — 与 daily_digest.py 一致的关键词规则。"""
    # 复用 daily_digest 的分类逻辑（简化版）
    title = topic.get("title", "").lower()
    summary = topic.get("summary", "").lower()
    tags = " ".join(topic.get("tags", [])).lower()
    text = f"{title} {summary} {tags}"

    rules = {
        "产品": ["launch", "release", "update", "app", "platform", "copilot", "chatgpt", "feature", "product", "tool", "beta", "发布", "上线", "更新", "产品", "功能"],
        "模型": ["new model", "open-source model", "open source model", "model release", "sota", "benchmark", "llm release", "新模型", "开源模型", "模型开源", "模型发布", "打榜", "登顶", "SOTA"],
        "研究": ["arxiv", "paper", "research", "study", "university", "neurips", "icml", "iclr", "论文", "研究", "学术"],
        "行业": ["funding", "raises", "valuation", "ipo", "revenue", "market", "invest", "acqui", "billion", "startup", "融资", "投资", "收购", "营收", "市场", "裁员", "合作"],
        "开源": ["open source", "open-source", "github", "apache", "mit license", "huggingface", "repo", "开源", "代码库"],
        "硬件": ["chip", "gpu", "npu", "tpu", "nvidia", "amd", "intel", "server", "datacenter", "芯片", "算力", "服务器", "硬件", "GPU"],
        "机器人": ["robot", "robotic", "embodied", "autonomous", "figure", "drone", "机器人", "无人驾驶", "具身", "机械臂"],
    }

    for cat in CATEGORY_PRIORITY:
        for kw in rules.get(cat, []):
            if kw in text:
                return cat
    return "行业"


def load_topics(topics_path: str) -> dict:
    """加载 01_topics.json，返回 {topics, classified, top1, cat_counts, total, source_count}。"""
    with open(topics_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    topics = data.get("topics", [])
    if not topics:
        print("❌ 01_topics.json 无话题数据", file=sys.stderr)
        sys.exit(1)

    # 按 composite_score 排序
    topics.sort(
        key=lambda t: t.get("composite_score", t.get("heat_score", 0)),
        reverse=True,
    )

    # 分类
    classified = {}
    for topic in topics:
        cat = _classify_topic(topic)
        classified.setdefault(cat, []).append(topic)

    # 统计
    cat_counts = {cat: len(items) for cat, items in classified.items()}
    total = len(topics)
    source_count = sum(len(t.get("source_urls", [])) for t in topics)
    top1 = topics[0] if topics else None

    return {
        "topics": topics,
        "classified": classified,
        "top1": top1,
        "cat_counts": cat_counts,
        "total": total,
        "source_count": source_count,
    }


def build_digest_bg_prompt() -> str:
    """返回固定的速递背景 prompt。"""
    return BG_PROMPT


def generate_bg_image(prompt: str) -> bytes:
    """调用 ERNIE-image API 生成背景图，返回 PNG 字节。"""
    try:
        from openai import OpenAI
    except ImportError:
        print("❌ 缺少依赖: pip install openai", file=sys.stderr)
        sys.exit(1)

    api_key = os.environ.get("AI_STUDIO_API_KEY", "")
    if not api_key:
        print("❌ AI_STUDIO_API_KEY 未设置，无法生成背景图", file=sys.stderr)
        print("   请提供 --base-image 或设置 AI_STUDIO_API_KEY", file=sys.stderr)
        sys.exit(1)

    client = OpenAI(api_key=api_key, base_url=AI_STUDIO_BASE_URL)
    print(f"🎨 生成速递封面背景...", file=sys.stderr)
    print(f"  Prompt: {prompt[:80]}...", file=sys.stderr)

    resp = client.images.generate(
        model=DEFAULT_MODEL,
        prompt=prompt,
        n=1,
        response_format="b64_json",
        size=IMAGE_SIZE,
        extra_body={
            "use_pe": True,
            "num_inference_steps": 8,
            "guidance_scale": 1.0,
        },
    )
    return base64.b64decode(resp.data[0].b64_json)


def _get_cached_bg_path(cache_dir: str) -> Path:
    """返回速递封面缓存背景图路径（固定一张，不按分类）。"""
    return Path(cache_dir) / "bg_digest.png"


def _is_cache_valid(path: Path, max_age_days: int) -> bool:
    """检查缓存文件是否存在且未过期。"""
    if not path.exists():
        return False
    age_seconds = time.time() - path.stat().st_mtime
    return age_seconds < max_age_days * 86400


def render_cover_text(base_image: Image.Image, topics_data: dict, date_str: str) -> Image.Image:
    """在背景图上叠加文字层。

    布局（从上到下）：
      - 英文小标题 "AI DAILY BRIEFING"
      - 中文大标题 "每日AI速递"
      - 渐变分隔线
      - Top 3 头条（透明度渐变：100% / 70% / 40%）
      - 日期
    """
    img = base_image.copy()
    draw = ImageDraw.Draw(img, "RGBA")
    w, h = img.size  # 1024x1024

    # ── 半透明白色遮罩（上方 50% 区域）──
    mask_h = int(h * 0.5)
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    overlay_draw.rectangle(
        [(0, 0), (w, mask_h)],
        fill=(255, 255, 255, 190),  # 0.75 透明度
    )
    img = Image.alpha_composite(img.convert("RGBA"), overlay)
    draw = ImageDraw.Draw(img, "RGBA")

    # ── 字体加载 ──
    font_en_small = _find_font(FONT_PATHS, 24)
    font_title = _find_font(BOLD_FONT_PATHS + FONT_PATHS, 52)
    font_headline = _find_font(BOLD_FONT_PATHS + FONT_PATHS, 28)
    font_date = _find_font(FONT_PATHS, 20)

    # ── 布局参数 ──
    margin_left = 60
    y_cursor = 80

    # ── 英文小标题 ──
    draw.text((margin_left, y_cursor), "AI DAILY BRIEFING", fill=COLOR_TITLE_EN, font=font_en_small)
    y_cursor += 40

    # ── 中文大标题 ──
    draw.text((margin_left, y_cursor), "每日AI速递", fill=COLOR_TITLE_CN, font=font_title)
    y_cursor += 72

    # ── 渐变分隔线 ──
    line_y = y_cursor
    line_w = w - margin_left * 2
    for x in range(line_w):
        ratio = x / line_w
        r = int(COLOR_DIVIDER_START[0] + (COLOR_DIVIDER_END[0] - COLOR_DIVIDER_START[0]) * ratio)
        g = int(COLOR_DIVIDER_START[1] + (COLOR_DIVIDER_END[1] - COLOR_DIVIDER_START[1]) * ratio)
        b = int(COLOR_DIVIDER_START[2] + (COLOR_DIVIDER_END[2] - COLOR_DIVIDER_START[2]) * ratio)
        draw.line([(margin_left + x, line_y), (margin_left + x, line_y + 3)], fill=(r, g, b, 255))
    y_cursor += 24

    # ── Top 3 头条（透明度渐变）──
    # PIL draw.text 的 RGBA alpha 效果不明显，直接将前景色向背景色混合
    # 背景近似 (240, 245, 255)，alpha 100%/70%/40% → 预混合 RGB
    topics = topics_data.get("topics", [])[:3]
    bg_approx = (240, 245, 255)
    headline_alphas = [1.0, 0.7, 0.4]
    for i, topic in enumerate(topics):
        title = topic.get("title", "")
        if len(title) > 30:
            title = title[:28] + "…"
        a = headline_alphas[i]
        color = tuple(int(COLOR_HEADLINE[j] * a + bg_approx[j] * (1 - a)) for j in range(3))
        draw.text((margin_left, y_cursor), f"📰 {title}", fill=color, font=font_headline)
        y_cursor += 44

    # 不足 3 条时补空行
    for _ in range(3 - len(topics)):
        y_cursor += 44

    # ── 日期 ──
    draw.text((margin_left, y_cursor), date_str, fill=COLOR_DATE, font=font_date)

    return img.convert("RGB")


def main():
    parser = argparse.ArgumentParser(description="每日AI速递固定风格封面图生成")
    parser.add_argument("--topics", required=True, help="01_topics.json 文件路径")
    parser.add_argument("--output", default=None, help="输出封面图路径（--prompt-only 时可省略）")
    parser.add_argument("--base-image", default=None, help="已有背景图路径（跳过 ERNIE-image 生成）")
    parser.add_argument("--date", default=None, help="日期字符串（默认今天，格式 YYYY-MM-DD）")
    parser.add_argument("--prompt-only", action="store_true", help="只输出背景 prompt，不生成图片")
    parser.add_argument("--save-base", default=None, help="保存背景图到指定路径（用于调试）")
    parser.add_argument("--cache-dir", default=os.path.expanduser("~/.autopub_backgrounds"),
                        help="背景图缓存目录（默认 ~/.autopub_backgrounds/）")
    parser.add_argument("--cache-days", type=int, default=30,
                        help="缓存过期天数（默认 30，设为 0 则每次重新生成）")
    parser.add_argument("--force-regen", action="store_true",
                        help="强制重新生成背景图，忽略缓存")
    args = parser.parse_args()

    # 日期
    date_str = args.date or datetime.now().strftime("%Y-%m-%d")

    # 加载话题数据
    topics_data = load_topics(args.topics)
    print(f"📰 加载 {topics_data['total']} 条话题", file=sys.stderr)
    if topics_data["top1"]:
        print(f"   头条: {topics_data['top1'].get('title', '?')[:50]}", file=sys.stderr)
    cat_summary = " · ".join(
        f"{cat} {topics_data['cat_counts'][cat]}"
        for cat in CATEGORY_PRIORITY
        if cat in topics_data["cat_counts"]
    )
    print(f"   分类: {cat_summary}", file=sys.stderr)

    # 构建背景 prompt
    bg_prompt = build_digest_bg_prompt()

    if args.prompt_only:
        print(bg_prompt)
        return

    if not args.output:
        parser.error("--output is required when not using --prompt-only")

    # 生成/加载背景图
    if args.base_image:
        # 1) 用户显式指定背景图
        print(f"🖼️  使用已有背景图: {args.base_image}", file=sys.stderr)
        base_img = Image.open(args.base_image).convert("RGBA")
        if base_img.size != (COVER_SIZE, COVER_SIZE):
            print(f"  调整尺寸: {base_img.size} → {COVER_SIZE}x{COVER_SIZE}", file=sys.stderr)
            base_img = base_img.resize((COVER_SIZE, COVER_SIZE), Image.LANCZOS)
    else:
        # 2) 检查缓存
        cache_dir = Path(args.cache_dir)
        cache_path = _get_cached_bg_path(str(cache_dir))
        cache_days = args.cache_days

        if not args.force_regen and cache_days > 0 and _is_cache_valid(cache_path, cache_days):
            print(f"📦 使用缓存背景图: {cache_path}", file=sys.stderr)
            base_img = Image.open(cache_path).convert("RGBA")
            if base_img.size != (COVER_SIZE, COVER_SIZE):
                base_img = base_img.resize((COVER_SIZE, COVER_SIZE), Image.LANCZOS)
        else:
            # 3) 缓存无效或强制重新生成 → 调 API
            reason = "强制重新生成" if args.force_regen else (
                "缓存过期" if cache_path.exists() else "无缓存"
            )
            print(f"🎨 {reason}，调用 ERNIE-image 生成背景...", file=sys.stderr)
            bg_bytes = generate_bg_image(bg_prompt)
            base_img = Image.open(io.BytesIO(bg_bytes)).convert("RGBA")
            print(f"  ✅ 背景图已生成 ({len(bg_bytes)} bytes)", file=sys.stderr)

            # 保存到缓存
            cache_dir.mkdir(parents=True, exist_ok=True)
            base_img.convert("RGB").save(str(cache_path), "PNG")
            print(f"  💾 背景图已缓存: {cache_path}", file=sys.stderr)

            # 保存背景图（--save-base 调试用）
            if args.save_base:
                Path(args.save_base).parent.mkdir(parents=True, exist_ok=True)
                base_img.convert("RGB").save(args.save_base, "PNG")
                print(f"  💾 背景图已保存: {args.save_base}", file=sys.stderr)

    # 叠加文字层
    print("✏️  叠加文字层...", file=sys.stderr)
    final_img = render_cover_text(base_img, topics_data, date_str)

    # 输出
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    final_img.save(args.output, "PNG", quality=95)
    file_size = Path(args.output).stat().st_size
    print(f"\n✅ 封面图已保存: {args.output} ({file_size} bytes)", file=sys.stderr)
    print(f"   尺寸: {final_img.size[0]}x{final_img.size[1]}", file=sys.stderr)
    print(f"   风格: 明亮科技风（浅蓝渐变背景 + Pillow 叠字）", file=sys.stderr)


if __name__ == "__main__":
    main()
