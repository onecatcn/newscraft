#!/usr/bin/env python3
"""wechat_draft.py — 创建微信公众号草稿

用法:
    python3 wechat_draft.py \
        --final 07_final.md \
        --images 05_images/media_ids.json \
        --output 08_wechat_draft_id.json

功能:
    1. 读取终稿 Markdown
    2. 将 Markdown 表格自动转换为图片（解决微信不渲染表格的问题）
    3. 上传表格图片到微信素材库
    4. 转换为微信 HTML 格式（表格位置替换为图片）
    5. 替换图片 URL 为微信域名 URL
    6. 调用 /cgi-bin/draft/add 创建草稿
    7. 输出草稿 ID

环境变量:
    WECHAT_APP_ID       微信 AppID
    WECHAT_APP_SECRET   微信 AppSecret
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path


WECHAT_API_BASE = "https://api.weixin.qq.com"
TOKEN_CACHE = {"token": None, "expires_at": 0}

# ── 公众号关注广告配置 ──
FOLLOW_AD_TEXT = "关注「AI 每日参」，极速同步硅谷前沿，深度拆解主流大厂进展。"
QR_URL_CACHE_FILE = Path.home() / ".config" / "autopub" / "qr_url.json"
QR_CODE_ASSET = Path(__file__).parent.parent / "materials" / "wechatqrcode.jpg"

# ── 速递分类颜色映射 ──
CATEGORY_COLORS = {
    "大厂格局": {"bg": "#e3f2fd", "border": "#42a5f5"},   # 浅蓝 — 大厂/战略
    "产品动态": {"bg": "#e8f5e9", "border": "#4caf50"},   # 浅绿 — 产品发布
    "模型前沿": {"bg": "#fce4ec", "border": "#ec407a"},   # 浅粉 — 模型/技术
    "研究论文": {"bg": "#f3e5f5", "border": "#ab47bc"},   # 浅紫 — 学术研究
    "行业观察": {"bg": "#fff3e0", "border": "#ff9800"},   # 浅橙 — 行业/商业
    "硬件&机器人": {"bg": "#e0f7fa", "border": "#00acc1"}, # 浅青 — 硬件/机器人
    "硬件&生态":  {"bg": "#fff8e1", "border": "#ff7043"}, # 浅琥珀 — 硬件/生态
    "开源项目":   {"bg": "#f1f8e9", "border": "#8bc34a"}, # 浅黄绿 — 开源
}


def get_access_token() -> str:
    """获取或刷新 access_token"""
    if TOKEN_CACHE["token"] and time.time() < TOKEN_CACHE["expires_at"] - 60:
        return TOKEN_CACHE["token"]

    app_id = os.environ.get("WECHAT_APP_ID", "")
    app_secret = os.environ.get("WECHAT_APP_SECRET", "")

    if not app_id or not app_secret:
        print("❌ 错误: WECHAT_APP_ID 或 WECHAT_APP_SECRET 未设置", file=sys.stderr)
        sys.exit(1)

    url = (
        f"{WECHAT_API_BASE}/cgi-bin/token"
        f"?grant_type=client_credential&appid={app_id}&secret={app_secret}"
    )

    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"❌ 获取 access_token 失败: {e}", file=sys.stderr)
        sys.exit(1)

    if "access_token" not in data:
        print(f"❌ access_token 错误: {data}", file=sys.stderr)
        sys.exit(1)

    TOKEN_CACHE["token"] = data["access_token"]
    TOKEN_CACHE["expires_at"] = time.time() + data.get("expires_in", 7200)
    return TOKEN_CACHE["token"]


# ── Markdown → WeChat HTML 转换 ──


def _is_ul_item(line: str) -> bool:
    """Check if a line is an unordered list item"""
    return bool(re.match(r"^[-*]\s+(.+)", line.strip()))


def _is_ol_item(line: str) -> bool:
    """Check if a line is an ordered list item"""
    return bool(re.match(r"^(\d+)\.\s+(.+)", line.strip()))


def _is_list_item(line: str) -> bool:
    return _is_ul_item(line) or _is_ol_item(line)


# Orphan symbols that should be skipped (lone punctuation on a line)
_ORPHAN_SYMBOLS = {"-", "*", ".", "·", "•", "—", "–", "─"}


def md_to_wechat_html(md: str, image_urls: dict) -> str:
    """将 Markdown 转换为微信兼容的 HTML

    Args:
        md: Markdown 文本
        image_urls: filename → wechat_url 映射
    """
    lines = md.split("\n")
    html_parts = []
    in_code_block = False
    in_refs_block = False
    in_footer_summary = False  # 文末摘要独立色块
    in_list = False
    list_type = None  # 'ul' or 'ol'
    code_buffer = []
    title_skipped = False  # skip first H1 (already used as article title metadata)
    in_category_section = False   # 是否在彩色分类区块内
    current_section_colors = None  # 当前区块颜色
    in_jinjiri_block = False      # 是否在「今日必知」色块内

    i = 0
    while i < len(lines):
        line = lines[i]

        # Code blocks
        if line.strip().startswith("```"):
            if in_code_block:
                html_parts.append(
                    f'<pre style="background:#1a1a2e;color:#e2e8f0;padding:16px;'
                    f'border-radius:8px;font-size:14px;overflow-x:auto;">'
                    f"<code>{'<br>'.join(code_buffer)}</code></pre>"
                )
                code_buffer = []
                in_code_block = False
            else:
                in_code_block = True
            i += 1
            continue

        if in_code_block:
            code_buffer.append(escape_html(line))
            i += 1
            continue

        stripped = line.strip()

        # Inline image comment: <!-- img:filename -->
        img_comment = re.match(r"^<!--\s*img:([^\s>]+)\s*-->$", stripped)
        if img_comment:
            filename = img_comment.group(1).strip()
            src = image_urls.get(filename, "")
            if src:
                html_parts.append(
                    f'<img src="{src}" alt="" '
                    f'style="max-width:100%;height:auto;margin:12px 0;border-radius:8px;">'
                )
            i += 1
            continue

        # Reference links block: <!-- refs --> ... <!-- /refs -->
        # Rendered as a distinct section with its own header and light gray background
        if stripped == "<!-- footer-summary -->":
            # 关闭上一个分类区块（如有）
            if in_category_section:
                html_parts.append('</section>')
                in_category_section = False
                current_section_colors = None
            # 关闭文末摘要区块（如有，理论上不会嵌套）
            if in_footer_summary:
                html_parts.append('</section>')
            in_footer_summary = True
            # 开启文末摘要独立色块：深色系，与正文分类区块形成层次感
            html_parts.append(
                '<section style="margin:28px 0 16px;padding:20px 18px 16px;'
                'background:#1a1a2e;border-radius:12px;">'
            )
            i += 1
            continue

        if stripped == "<!-- /footer-summary -->":
            if in_footer_summary:
                html_parts.append('</section>')
                in_footer_summary = False
            i += 1
            continue

        if stripped == "<!-- refs -->":
            in_refs_block = True
            # Close any open category section first
            if in_category_section:
                html_parts.append('</section>')
                in_category_section = False
                current_section_colors = None
            # Open refs section block
            html_parts.append(
                '<section style="margin:24px 0 8px;padding:16px 18px 14px;'
                'background:#f5f5f5;border-radius:10px;'
                'border-left:4px solid #bdbdbd;">'
                '<h2 style="font-size:14px;font-weight:bold;color:#757575;'
                'margin:0 0 10px;padding-bottom:8px;'
                'border-bottom:1px solid #e0e0e0;letter-spacing:0.05em;">'
                '📎 参考来源</h2>'
            )
            i += 1
            continue
        if in_refs_block:
            if stripped == "<!-- /refs -->":
                in_refs_block = False
                html_parts.append('</section>')
                i += 1
                continue
            if stripped:
                text = inline_format(stripped)
                html_parts.append(
                    f'<p style="margin:3px 0;line-height:1.5;color:#9e9e9e;font-size:12px;'
                    f'word-break:break-all;">{text}</p>'
                )
            i += 1
            continue

        # Empty line — only close list if next non-empty line is NOT a list item
        if not stripped:
            if in_list:
                # Peek ahead: if next non-empty line is still a list item, keep list open
                next_content = None
                for j in range(i + 1, len(lines)):
                    if lines[j].strip():
                        next_content = lines[j]
                        break
                if next_content is None or not _is_list_item(next_content):
                    in_list = False
                    list_type = None
                # Either way: skip the blank line inside or after a list (no output)
            # Skip all empty lines — paragraphs get their spacing from CSS margin
            i += 1
            continue

        # Horizontal rule
        if stripped in ("---", "***", "___"):
            if in_list:
                html_parts.append(f"</{list_type}>")
                in_list = False
                list_type = None
            # 关闭「今日必知」色块（如有）
            if in_jinjiri_block:
                html_parts.append('</section>')
                in_jinjiri_block = False
            if in_category_section:
                # 在分类色块内部：使用半透明细线作为话题间分隔
                html_parts.append(
                    '<div style="margin:18px 0;border-top:1px solid rgba(0,0,0,0.12);"></div>'
                )
            else:
                html_parts.append(
                    '<section style="margin:16px 0;border-bottom:1px solid #e0e0e0;"></section>'
                )
            i += 1
            continue

        # Headers
        h_match = re.match(r"^(#{1,6})\s+(.+)", stripped)
        if h_match:
            level = len(h_match.group(1))
            # Skip the first H1 — it's already used as the article title in WeChat metadata
            if level == 1 and not title_skipped:
                title_skipped = True
                i += 1
                continue
            if in_list:
                html_parts.append(f"</{list_type}>")
                in_list = False
                list_type = None
            level = len(h_match.group(1))
            text = inline_format(h_match.group(2))

            # H2 分类标题 — 整节内容包裹在彩色背景块内
            if level == 2:
                cat_matched = None
                for cat_name in CATEGORY_COLORS:
                    if cat_name in h_match.group(2):
                        cat_matched = cat_name
                        break
                if cat_matched:
                    colors = CATEGORY_COLORS[cat_matched]
                    # 关闭上一个分类区块（如有）
                    if in_category_section:
                        html_parts.append('</section>')
                    in_category_section = True
                    current_section_colors = colors
                    # 开启新的彩色整节区块，H2 标题在块内
                    html_parts.append(
                        f'<section style="margin:24px 0;padding:20px 18px 16px;'
                        f'background:{colors["bg"]};border-radius:12px;'
                        f'border-left:5px solid {colors["border"]};">'
                        f'<h2 style="font-size:18px;font-weight:bold;color:#1a1a2e;'
                        f'margin:0 0 14px;padding-bottom:10px;'
                        f'border-bottom:2px solid {colors["border"]};">'
                        f'{text}</h2>'
                    )
                    i += 1
                    continue

            styles = {
                1: "font-size:24px;font-weight:bold;color:#1a1a2e;margin:24px 0 12px;",
                2: "font-size:20px;font-weight:bold;color:#1a1a2e;margin:20px 0 10px;border-bottom:2px solid #0ff0fc;padding-bottom:6px;",
                3: "font-size:18px;font-weight:bold;color:#16213e;margin:16px 0 8px;",
            }
            style = styles.get(level, f"font-size:{22-level*2}px;font-weight:bold;")
            html_parts.append(f'<h{level} style="{style}">{text}</h{level}>')
            i += 1
            continue

        # Blockquote
        if stripped.startswith("> "):
            if in_list:
                html_parts.append(f"</{list_type}>")
                in_list = False
                list_type = None
            text = inline_format(stripped[2:])
            html_parts.append(
                f'<blockquote style="border-left:4px solid #0ff0fc;'
                f'padding:8px 16px;margin:12px 0;background:#f0f9ff;'
                f'color:#333;">{text}</blockquote>'
            )
            i += 1
            continue

        # Unordered list — rendered as <p> with bullet prefix to avoid WeChat <ul> rendering bugs
        ul_match = re.match(r"^[-*]\s+(.+)", stripped)
        if ul_match:
            if in_list and list_type != "ul":
                in_list = False
                list_type = None
            in_list = True
            list_type = "ul"
            text = inline_format(ul_match.group(1))
            html_parts.append(
                f'<p style="margin:6px 0;line-height:1.8;color:#333;font-size:16px;">'
                f'• {text}</p>'
            )
            i += 1
            continue

        # Ordered list — rendered as <p> with number prefix to avoid WeChat <ol> rendering bugs
        ol_match = re.match(r"^(\d+)\.\s+(.+)", stripped)
        if ol_match:
            if in_list and list_type != "ol":
                in_list = False
                list_type = None
            in_list = True
            list_type = "ol"
            num = ol_match.group(1)
            text = inline_format(ol_match.group(2))
            html_parts.append(
                f'<p style="margin:6px 0;line-height:1.8;color:#333;font-size:16px;">'
                f'{num}. {text}</p>'
            )
            i += 1
            continue

        # Close list tracking if we hit a non-list item
        if in_list:
            in_list = False
            list_type = None

        # Orphan symbol filter — skip lone punctuation characters
        if stripped in _ORPHAN_SYMBOLS:
            i += 1
            continue

        # Image
        img_match = re.match(r"!\[([^\]]*)\]\(([^)]+)\)", stripped)
        if img_match:
            alt = img_match.group(1)
            src = img_match.group(2)
            # Replace with WeChat URL if available
            filename = os.path.basename(src)
            if filename in image_urls:
                src = image_urls[filename]
            html_parts.append(
                f'<img src="{src}" alt="{alt}" '
                f'style="max-width:100%;height:auto;margin:12px 0;border-radius:8px;">'
            )
            i += 1
            continue

        # Regular paragraph
        text = inline_format(stripped)

        # ⚡ 统计行（速递副标题）— 独立浅色色块，紧跟标题之后
        if stripped.startswith("⚡"):
            html_parts.append(
                '<section style="margin:12px 0 20px;padding:10px 16px;'
                'background:#e8f4fd;border-radius:8px;'
                'border-left:4px solid #42a5f5;">'
                f'<p style="margin:0;line-height:1.6;color:#1565c0;font-size:15px;'
                f'font-weight:500;">{text}</p>'
                '</section>'
            )
            i += 1
            continue

        # **今日必知** — 开启独立色块，收集后续列表项（无行间空白）
        if stripped == "**今日必知**":
            html_parts.append(
                '<section style="margin:16px 0;padding:16px 18px 14px;'
                'background:#fff9c4;border-radius:10px;'
                'border-left:5px solid #f9a825;">'
                '<p style="margin:0 0 10px;line-height:1.6;color:#5d4037;font-size:16px;'
                'font-weight:bold;">今日必知</p>'
            )
            in_jinjiri_block = True
            i += 1
            continue

        if stripped.startswith("💡"):
            # 深度解读行：在分类色块内用更深的半透明底色突出
            html_parts.append(
                f'<p style="margin:10px 0;line-height:1.8;color:#444;font-size:15px;'
                f'padding:8px 12px;background:rgba(0,0,0,0.06);border-radius:6px;">'
                f'{text}</p>'
            )
        elif in_jinjiri_block:
            # 「今日必知」色块内：每项紧凑，无多余空白
            html_parts.append(
                f'<p style="margin:4px 0;line-height:1.7;color:#4e342e;font-size:15px;">'
                f'{text}</p>'
            )
        elif in_footer_summary:
            # 文末摘要色块内：白色文字，📌/📍 行加粗，💬/👉/📎 行用浅色
            if stripped.startswith("📌") or stripped.startswith("📍"):
                html_parts.append(
                    f'<p style="margin:8px 0;line-height:1.8;color:#ffffff;font-size:16px;'
                    f'font-weight:bold;">{text}</p>'
                )
            elif stripped.startswith("💬"):
                html_parts.append(
                    f'<p style="margin:10px 0;line-height:1.8;color:#90caf9;font-size:15px;">{text}</p>'
                )
            elif stripped.startswith("👉"):
                html_parts.append(
                    f'<p style="margin:6px 0;line-height:1.8;color:#b0bec5;font-size:14px;">{text}</p>'
                )
            elif stripped.startswith("📎"):
                html_parts.append(
                    f'<p style="margin:10px 0 0;line-height:1.8;color:#78909c;font-size:13px;">{text}</p>'
                )
            else:
                html_parts.append(
                    f'<p style="margin:8px 0;line-height:1.8;color:#cfd8dc;font-size:15px;">{text}</p>'
                )
        else:
            html_parts.append(
                f'<p style="margin:12px 0;line-height:1.8;color:#333;font-size:16px;">{text}</p>'
            )
        i += 1

    # 关闭最后一个未关闭的分类区块
    if in_category_section:
        html_parts.append('</section>')
    # 关闭未关闭的「今日必知」色块
    if in_jinjiri_block:
        html_parts.append('</section>')
    # 关闭未关闭的文末摘要区块
    if in_footer_summary:
        html_parts.append('</section>')
    # (list tracking state reset — no closing tags needed as lists use <p> elements)

    return "\n".join(html_parts)


def escape_html(text: str) -> str:
    """Escape HTML special characters"""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def inline_format(text: str) -> str:
    """Process inline markdown formatting"""
    # Bold
    text = re.sub(r"\*\*(.+?)\*\*", r'<strong style="color:#1a1a2e;">\1</strong>', text)
    # Italic
    text = re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)
    # Inline code
    text = re.sub(
        r"`(.+?)`",
        r'<code style="background:#f0f0f0;padding:2px 6px;border-radius:3px;'
        r'font-size:14px;color:#e11d48;">\1</code>',
        text,
    )
    # Links — plain text only (WeChat does not support clickable hyperlinks)
    text = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        r"\1 (\2)",
        text,
    )
    return text


def extract_title(md: str) -> str:
    """Extract title from markdown"""
    for line in md.split("\n"):
        if line.startswith("# ") and not line.startswith("## "):
            return line[2:].strip()
    return "AI 每日 10 分钟"


def extract_digest(md: str) -> str:
    """Extract digest from markdown, max 120 chars.

    For digest articles (title contains "每日AI速递"), extract the ⚡ subtitle line.
    For regular articles, extract from TL;DR blockquote.
    """
    lines = md.split("\n")

    # Check if this is a digest article
    is_digest = False
    for line in lines:
        if line.startswith("# ") and ("每日AI速递" in line or "AI每日参" in line):
            is_digest = True
            break

    if is_digest:
        # Extract ⚡ subtitle line as digest
        for line in lines:
            if line.strip().startswith("⚡"):
                digest = line.strip()
                # Remove markdown formatting
                digest = re.sub(r"\*\*(.+?)\*\*", r"\1", digest)
                digest = re.sub(r"\*(.+?)\*", r"\1", digest)
                if len(digest) > 120:
                    digest = digest[:117] + "..."
                return digest or "AI 热点速递"

    # Regular article: extract from TL;DR blockquote
    in_tldr = False
    digest_parts = []
    for line in lines:
        if "TL;DR" in line or "TLDR" in line:
            in_tldr = True
            continue
        if in_tldr:
            if line.startswith("##") or line.strip() == "---":
                break
            stripped = line.strip().lstrip("> ")
            if stripped:
                digest_parts.append(stripped)

    digest = " ".join(digest_parts)
    # Remove markdown formatting
    digest = re.sub(r"\*\*(.+?)\*\*", r"\1", digest)
    digest = re.sub(r"\*(.+?)\*", r"\1", digest)
    digest = re.sub(r"`(.+?)`", r"\1", digest)

    if len(digest) > 120:
        digest = digest[:117] + "..."
    return digest or "AI 技术热点速递"


def _upload_single_image(image_path: str, access_token: str) -> dict | None:
    """上传单张图片到微信素材库，返回 {url, media_id} 或 None"""
    url = f"{WECHAT_API_BASE}/cgi-bin/media/uploadimg?access_token={access_token}"
    boundary = "----AutopubBoundary"
    with open(image_path, "rb") as f:
        img_data = f.read()
    filename = Path(image_path).name
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="media"; filename="{filename}"\r\n'
        f"Content-Type: image/png\r\n\r\n"
    ).encode() + img_data + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        if "url" in result:
            return {"url": result["url"], "media_id": result.get("media_id", "")}
    except Exception as e:
        print(f"  ⚠️  上传失败: {e}", file=sys.stderr)
    return None


def _convert_tables_to_images(md_content: str, images_dir: Path, access_token: str) -> tuple[str, dict]:
    """
    将 md_content 中所有 Markdown 表格转换为微信图片。
    返回 (替换后的 md_content, {filename: wechat_url} 映射)
    """
    try:
        import importlib.util, sys as _sys
        skill_dir = Path(__file__).parent
        spec = importlib.util.spec_from_file_location("table_to_image", skill_dir / "table_to_image.py")
        t2i = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(t2i)
    except Exception as e:
        print(f"  ⚠️  table_to_image 加载失败，跳过表格转图: {e}", file=sys.stderr)
        return md_content, {}

    tables_dir = images_dir / "tables"
    tables_dir.mkdir(parents=True, exist_ok=True)

    mapping = t2i.process_draft.__func__ if hasattr(t2i.process_draft, '__func__') else t2i.process_draft
    # process_draft 需要写文件路径，用临时 md 文件
    import tempfile
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as tmp:
        tmp.write(md_content)
        tmp_path = tmp.name

    try:
        raw_to_img = t2i.process_draft(tmp_path, str(tables_dir))
    except Exception as e:
        print(f"  ⚠️  表格渲染失败，跳过: {e}", file=sys.stderr)
        return md_content, {}
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    if not raw_to_img:
        return md_content, {}

    print(f"  📊 发现 {len(raw_to_img)} 个表格，上传到微信素材库...", file=sys.stderr)
    extra_image_urls = {}
    new_md = md_content

    for raw_table, img_path in raw_to_img.items():
        result = _upload_single_image(img_path, access_token)
        if result:
            filename = Path(img_path).name
            extra_image_urls[filename] = result["url"]
            # 替换 markdown 中的原始表格为图片语法
            img_md = f"![表格]({filename})"
            new_md = new_md.replace(raw_table, img_md, 1)
            print(f"  ✅ 表格图片已上传: {filename}", file=sys.stderr)
        else:
            print(f"  ⚠️  表格图片上传失败，保留原始表格文本", file=sys.stderr)

    return new_md, extra_image_urls


def _get_or_upload_qr_url(access_token: str) -> str | None:
    """获取公众号二维码的微信 CDN URL（首次上传并缓存，后续复用）"""
    # 读缓存
    QR_URL_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    if QR_URL_CACHE_FILE.exists():
        try:
            cached = json.loads(QR_URL_CACHE_FILE.read_text())
            if cached.get("url"):
                return cached["url"]
        except Exception:
            pass

    # 本地文件不存在时跳过
    if not QR_CODE_ASSET.exists():
        print(f"  ⚠️  本地二维码不存在，跳过关注广告（请将二维码放置到 {QR_CODE_ASSET}）", file=sys.stderr)
        return None

    print("  📤 首次上传公众号二维码到微信 CDN...", file=sys.stderr)
    result = _upload_single_image(str(QR_CODE_ASSET), access_token)
    if result and result.get("url"):
        QR_URL_CACHE_FILE.write_text(json.dumps({"url": result["url"]}, ensure_ascii=False))
        print(f"  ✅ 二维码已上传并缓存", file=sys.stderr)
        return result["url"]

    print("  ⚠️  二维码上传失败，将跳过关注广告", file=sys.stderr)
    return None


def _build_follow_footer(qr_url: str | None) -> str:
    """生成文章末尾的合并引导语+关注区块 HTML"""
    qr_html = ""
    if qr_url:
        qr_html = (
            f'<img src="{qr_url}" alt="公众号二维码" '
            f'style="width:160px;height:160px;margin:8px auto 0;display:block;border-radius:8px;">'
        )
    return (
        '<section style="margin-top:32px;padding:20px 16px;background:#f8f9fa;'
        'border-radius:12px;text-align:center;">'
        '<p style="font-size:15px;color:#1a1a2e;margin:0 0 8px;">'
        '恭喜你完成今日份的 AI 进化！里程碑已达成：🚩</p>'
        '<p style="font-size:15px;color:#1a1a2e;margin:0 0 12px;">'
        '别忘了顺手解锁 "点赞+在看+转发" 隐藏成就。</p>'
        '<p style="font-size:14px;color:#666;margin:0 0 12px;">'
        '记得点亮 星标，防止由于算法调皮导致咱们"走散"。</p>'
        '<p style="font-size:14px;color:#666;margin:0 0 12px;">'
        '撤了，明天同一时间见！👋</p>'
        f'{qr_html}'
        '</section>'
    )


def create_draft(
    final_path: str,
    images_path: str,
    output_path: str,
):
    """主流程：创建微信草稿"""

    # 1. Read final markdown
    with open(final_path, "r", encoding="utf-8") as f:
        md_content = f.read()

    # 2. Load image URLs
    image_urls = {}
    cover_media_id = None

    if images_path and Path(images_path).exists():
        with open(images_path, "r", encoding="utf-8") as f:
            img_data = json.load(f)

        cover = img_data.get("cover")
        if cover:
            cover_media_id = cover.get("media_id")
            if cover.get("url"):
                image_urls[cover["filename"]] = cover["url"]

        for inline in img_data.get("inline", []):
            if inline.get("url"):
                image_urls[inline["filename"]] = inline["url"]

    # 2.5 表格转图片（在 HTML 转换前执行）
    access_token = get_access_token()
    images_dir = Path(images_path).parent if images_path else Path(output_path).parent
    print("📊 检测 Markdown 表格并转换为图片...", file=sys.stderr)
    md_content, table_image_urls = _convert_tables_to_images(md_content, images_dir, access_token)
    image_urls.update(table_image_urls)

    # 3. Convert markdown to HTML
    print("📝 转换 Markdown → WeChat HTML...", file=sys.stderr)
    html_content = md_to_wechat_html(md_content, image_urls)

    # 3.5 追加公众号关注广告
    print("  📣 追加公众号关注广告...", file=sys.stderr)
    qr_url = _get_or_upload_qr_url(access_token)
    html_content += _build_follow_footer(qr_url)

    title = extract_title(md_content)
    digest = extract_digest(md_content)

    print(f"  标题: {title}", file=sys.stderr)
    print(f"  摘要: {digest}", file=sys.stderr)
    print(f"  封面 media_id: {cover_media_id or '(无)'}", file=sys.stderr)
    print(f"  正文图替换: {len(image_urls)} 张", file=sys.stderr)

    # 4. Create draft via API
    url = f"{WECHAT_API_BASE}/cgi-bin/draft/add?access_token={access_token}"

    article = {
        "title": title,
        "author": "AI 每日 10 分钟",
        "digest": digest,
        "content": html_content,
        "need_open_comment": 1,
        "only_fans_can_comment": 0,
    }

    if cover_media_id:
        article["thumb_media_id"] = cover_media_id

    payload = json.dumps({"articles": [article]}, ensure_ascii=False).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )

    print("\n📤 创建微信草稿...", file=sys.stderr)

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"❌ 创建草稿失败: {e}", file=sys.stderr)
        # Save HTML locally as fallback
        html_fallback = Path(output_path).parent / "draft_fallback.html"
        with open(html_fallback, "w", encoding="utf-8") as f:
            f.write(f"<html><head><title>{title}</title></head><body>")
            f.write(html_content)
            f.write("</body></html>")
        print(f"💾 HTML 已保存到 {html_fallback}，可手动上传", file=sys.stderr)
        sys.exit(1)

    if "media_id" in result:
        draft_id = result["media_id"]
        print(f"\n✅ 草稿创建成功！", file=sys.stderr)
        print(f"  草稿 ID: {draft_id}", file=sys.stderr)

        # Write output
        output = {
            "draft_id": draft_id,
            "created_at": datetime.now().isoformat(),
            "title": title,
            "digest": digest,
            "image_media_ids": {
                "cover": cover_media_id,
                "inline": [
                    img.get("url") for img in (json.load(open(images_path)).get("inline", []) if images_path and Path(images_path).exists() else [])
                ],
            },
            "status": "draft_created",
        }
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)

        # Print publish instructions
        print(f"\n📋 手动发布步骤:", file=sys.stderr)
        print(f"  1. 打开 https://mp.weixin.qq.com", file=sys.stderr)
        print(f"  2. 登录公众号后台", file=sys.stderr)
        print(f"  3. 进入「内容管理」→「草稿箱」", file=sys.stderr)
        print(f"  4. 找到草稿: \"{title}\"", file=sys.stderr)
        print(f"  5. 点击「预览」在手机上确认排版", file=sys.stderr)
        print(f"  6. 确认无误后点击「群发」", file=sys.stderr)
    else:
        print(f"❌ 创建草稿失败: {result}", file=sys.stderr)

        # Save HTML locally as fallback
        html_fallback = Path(output_path).parent / "draft_fallback.html"
        with open(html_fallback, "w", encoding="utf-8") as f:
            f.write(
                f'<html><head><meta charset="utf-8"><title>{title}</title></head><body>'
            )
            f.write(html_content)
            f.write("</body></html>")
        print(f"💾 HTML 已保存到 {html_fallback}，可手动在微信后台创建", file=sys.stderr)

        output = {
            "draft_id": None,
            "created_at": datetime.now().isoformat(),
            "title": title,
            "error": result,
            "fallback_html": str(html_fallback),
            "status": "failed",
        }
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="创建微信公众号草稿")
    parser.add_argument("--final", required=True, help="终稿 Markdown 文件路径")
    parser.add_argument(
        "--images", default="", help="图片 media_ids.json 文件路径"
    )
    parser.add_argument("--output", required=True, help="输出 JSON 文件路径")
    args = parser.parse_args()

    create_draft(args.final, args.images, args.output)


if __name__ == "__main__":
    main()
