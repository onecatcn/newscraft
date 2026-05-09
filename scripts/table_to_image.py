#!/usr/bin/env python3
"""table_to_image.py — 将 Markdown 表格转换为图片

功能：
  1. 从 Markdown 文件中提取所有表格
  2. 用 matplotlib 渲染为美观的 PNG 图片
  3. 将图片保存到指定目录
  4. 返回「原始表格文本 → 图片路径」的映射，供 wechat_draft.py 替换使用

用法：
    python3 table_to_image.py --draft 04_draft.md --output-dir 05_images/tables/
"""

import argparse
import hashlib
import re
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
from matplotlib.patches import FancyBboxPatch


# ── 字体配置 ──────────────────────────────────────────────

def _find_cjk_font() -> str | None:
    """找到可用的 CJK 字体路径"""
    candidates = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/arphic/uming.ttc",
        "/usr/share/fonts/truetype/arphic/ukai.ttc",
    ]
    for path in candidates:
        if Path(path).exists():
            return path
    # 从 fontmanager 搜索
    for f in fm.fontManager.ttflist:
        if any(k in f.name for k in ["Noto Sans CJK", "Noto Serif CJK", "AR PL"]):
            return f.fname
    return None


def _setup_font() -> str:
    """设置全局中文字体，返回字体名称"""
    font_path = _find_cjk_font()
    if font_path:
        fm.fontManager.addfont(font_path)
        prop = fm.FontProperties(fname=font_path)
        font_name = prop.get_name()
        plt.rcParams["font.family"] = [font_name, "DejaVu Sans"]
    else:
        font_name = "DejaVu Sans"
    plt.rcParams["axes.unicode_minus"] = False
    return font_name


FONT_NAME = _setup_font()


# ── Markdown 表格解析 ─────────────────────────────────────

def extract_tables(md_text: str) -> list[dict]:
    """
    从 Markdown 中提取所有表格。
    返回列表，每项包含：
      - raw: 原始表格文本（含换行）
      - headers: 列头列表
      - rows: 数据行列表（每项为列值列表）
      - preceding_heading: 表格前最近的标题文字（用于生成文件名）
    """
    lines = md_text.split("\n")
    tables = []
    i = 0
    last_heading = ""

    while i < len(lines):
        line = lines[i]

        # 记录最近的标题
        h_match = re.match(r"^#{1,4}\s+(.+)", line.strip())
        if h_match:
            last_heading = h_match.group(1).strip()

        # 检测表格起始行（含 | 且不只是分隔线）
        if "|" in line and not re.match(r"^\s*[\|\-\s:]+$", line):
            # 收集连续的表格行
            table_lines = []
            j = i
            while j < len(lines) and "|" in lines[j]:
                table_lines.append(lines[j])
                j += 1

            if len(table_lines) >= 2:
                # 解析表头
                headers = _parse_row(table_lines[0])
                # 跳过分隔行（第二行，如 |---|---|）
                data_start = 1
                if len(table_lines) > 1 and re.match(
                    r"^\s*\|?\s*[-:]+\s*(\|\s*[-:]+\s*)*\|?\s*$", table_lines[1]
                ):
                    data_start = 2

                rows = [_parse_row(l) for l in table_lines[data_start:] if "|" in l]

                if headers and rows:
                    raw = "\n".join(table_lines)
                    tables.append(
                        {
                            "raw": raw,
                            "headers": headers,
                            "rows": rows,
                            "preceding_heading": last_heading,
                        }
                    )

            i = j
            continue
        i += 1

    return tables


def _parse_row(line: str) -> list[str]:
    """解析表格一行，去掉首尾的 | 和空格"""
    line = line.strip().strip("|")
    cells = [c.strip() for c in line.split("|")]
    return cells


def _clean_cell(text: str) -> str:
    """去除 Markdown 格式（加粗、链接等）"""
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    text = re.sub(r"`(.+?)`", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"<[^>]+>", "", text)
    return text.strip()


# ── 表格渲染 ──────────────────────────────────────────────

# 颜色主题（与 autopub 深海蓝风格一致）
THEME = {
    "header_bg":   "#1a1a2e",
    "header_fg":   "#ffffff",
    "row_even_bg": "#f8f9fc",
    "row_odd_bg":  "#eef1f7",
    "border":      "#c8d0e0",
    "text":        "#1a1a2e",
    "title_fg":    "#1a1a2e",
    "accent":      "#0ff0fc",
}


def render_table_image(
    headers: list[str],
    rows: list[list[str]],
    output_path: str,
    title: str = "",
) -> str:
    """
    将表头 + 数据行渲染为 PNG，保存到 output_path。
    返回 output_path。
    """
    # 统一列数
    n_cols = len(headers)
    clean_headers = [_clean_cell(h) for h in headers]
    clean_rows = []
    for row in rows:
        # 补齐或截断到 n_cols
        padded = (row + [""] * n_cols)[:n_cols]
        clean_rows.append([_clean_cell(c) for c in padded])

    n_rows = len(clean_rows)

    # ── 动态计算单元格宽度 ──
    # 按列计算最大字符数（中文算2，英文算1）
    def char_width(s: str) -> float:
        return sum(2 if ord(c) > 127 else 1 for c in s)

    col_max_w = []
    for ci in range(n_cols):
        max_w = char_width(clean_headers[ci])
        for row in clean_rows:
            if ci < len(row):
                max_w = max(max_w, char_width(row[ci]))
        col_max_w.append(max_w)

    # 换算为英寸（每字符约 0.12 英寸），最小 1.0，最大 4.5
    col_widths = [max(1.0, min(4.5, w * 0.12)) for w in col_max_w]
    total_width = sum(col_widths) + 0.6  # 左右边距
    total_width = max(6.0, min(14.0, total_width))

    row_height = 0.42       # 英寸每行
    header_height = 0.52
    title_height = 0.4 if title else 0.0
    fig_height = title_height + header_height + n_rows * row_height + 0.4

    fig, ax = plt.subplots(figsize=(total_width, fig_height))
    ax.set_xlim(0, total_width)
    ax.set_ylim(0, fig_height)
    ax.axis("off")
    fig.patch.set_facecolor("white")

    # 归一化列宽（累计 x 坐标）
    margin = 0.3
    usable_w = total_width - 2 * margin
    col_w_norm = [w / sum(col_widths) * usable_w for w in col_widths]
    col_x = [margin]
    for w in col_w_norm[:-1]:
        col_x.append(col_x[-1] + w)

    # ── 绘制标题 ──
    y_cursor = fig_height
    if title:
        y_cursor -= title_height
        ax.text(
            total_width / 2,
            y_cursor + title_height * 0.55,
            title,
            ha="center", va="center",
            fontsize=11, fontweight="bold",
            color=THEME["title_fg"],
            fontfamily=FONT_NAME,
        )

    # ── 绘制表头 ──
    y_cursor -= header_height
    for ci, (hdr, cx, cw) in enumerate(zip(clean_headers, col_x, col_w_norm)):
        rect = FancyBboxPatch(
            (cx + 0.02, y_cursor + 0.04),
            cw - 0.04, header_height - 0.08,
            boxstyle="round,pad=0.02",
            facecolor=THEME["header_bg"],
            edgecolor="none",
        )
        ax.add_patch(rect)
        ax.text(
            cx + cw / 2,
            y_cursor + header_height / 2,
            hdr,
            ha="center", va="center",
            fontsize=9.5, fontweight="bold",
            color=THEME["header_fg"],
            fontfamily=FONT_NAME,
        )

    # ── 绘制数据行 ──
    for ri, row in enumerate(clean_rows):
        y_cursor -= row_height
        bg = THEME["row_even_bg"] if ri % 2 == 0 else THEME["row_odd_bg"]
        for ci, (cell, cx, cw) in enumerate(zip(row, col_x, col_w_norm)):
            rect = FancyBboxPatch(
                (cx + 0.02, y_cursor + 0.04),
                cw - 0.04, row_height - 0.08,
                boxstyle="round,pad=0.02",
                facecolor=bg,
                edgecolor=THEME["border"],
                linewidth=0.4,
            )
            ax.add_patch(rect)
            # 长文字自动截断
            display_text = cell if len(cell) <= 30 else cell[:28] + "…"
            ax.text(
                cx + cw / 2,
                y_cursor + row_height / 2,
                display_text,
                ha="center", va="center",
                fontsize=8.5,
                color=THEME["text"],
                fontfamily=FONT_NAME,
            )

    # ── 底部分隔线 ──
    ax.axhline(
        y=fig_height - title_height - header_height - n_rows * row_height,
        xmin=margin / total_width,
        xmax=(total_width - margin) / total_width,
        color=THEME["border"],
        linewidth=0.6,
    )

    plt.tight_layout(pad=0.3)
    plt.savefig(output_path, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return output_path


# ── 主入口 ───────────────────────────────────────────────

def process_draft(draft_path: str, output_dir: str) -> dict[str, str]:
    """
    读取 draft_path，提取所有表格，各渲染为图片存入 output_dir。
    返回 {raw_table_text: image_path} 映射。
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    with open(draft_path, "r", encoding="utf-8") as f:
        md_text = f.read()

    tables = extract_tables(md_text)
    if not tables:
        print("ℹ️  未发现 Markdown 表格，无需转换", file=sys.stderr)
        return {}

    print(f"📊 发现 {len(tables)} 个表格，开始渲染...", file=sys.stderr)
    mapping = {}

    for idx, tbl in enumerate(tables, start=1):
        # 用表格内容 hash 生成稳定文件名
        h = hashlib.md5(tbl["raw"].encode()).hexdigest()[:8]
        img_name = f"table_{idx:02d}_{h}.png"
        img_path = str(output_dir / img_name)

        title = tbl["preceding_heading"] if tbl["preceding_heading"] else ""
        render_table_image(tbl["headers"], tbl["rows"], img_path, title=title)
        mapping[tbl["raw"]] = img_path
        print(f"  ✅ 表格 {idx}: {img_name}  ({len(tbl['rows'])} 行 × {len(tbl['headers'])} 列)", file=sys.stderr)

    return mapping


def main():
    parser = argparse.ArgumentParser(description="Markdown 表格 → PNG 图片")
    parser.add_argument("--draft", required=True, help="Markdown 草稿路径")
    parser.add_argument("--output-dir", required=True, help="图片输出目录")
    args = parser.parse_args()

    mapping = process_draft(args.draft, args.output_dir)
    if not mapping:
        sys.exit(0)

    print(f"\n📋 共生成 {len(mapping)} 张表格图片:")
    for img_path in mapping.values():
        print(f"  {img_path}")


if __name__ == "__main__":
    main()
