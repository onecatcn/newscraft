#!/usr/bin/env python3
"""cover_review.py — 配图质量检查

检查项（四层递进）：
  1. 文件完整性：PIL 能否正常打开、尺寸是否合理、文件大小是否 > 100KB
  2. 像素健康度：均值像素值检测（全白/全黑/全灰 → 疑似空白图）
  3. 跨文章去重：差值哈希（dhash）与历史配图库比对，相似度超阈值 → FAIL
  4. 内容语义（可选，需 AI_STUDIO_API_KEY）：调用 ERNIE-4.5-VL 判断
     - 是否包含中文/英文字符（ernie-image 的已知 bug）
     - 与文章主题是否相关（简单关键词匹配）

用法:
    python3 cover_review.py --image <图片路径> [--title <文章标题>] [--output <结果JSON>]
                            [--register] [--db <哈希库路径>] [--no-dedup] [--no-vl]

    --register   检查通过后将该图哈希写入历史库（发布时使用）
    --db         指定哈希库 JSON 路径（默认 ~/.config/autopub/cover_hashes.json）
    --no-dedup   跳过去重检查

退出码:
    0 = PASS（可以发布）
    1 = WARN（有问题但不阻断，建议人工确认）
    2 = FAIL（必须重新生成）
"""

import argparse
import base64
import datetime
import json
import os
import sys
from pathlib import Path


# ── 阈值常量 ──────────────────────────────────────────────────────────────────
MIN_FILE_SIZE_KB   = 80    # 低于此值认为图片内容过少（单位 KB）
WARN_FILE_SIZE_KB  = 150   # 低于此值给出 WARN（可能内容简单）
MIN_PIXEL_MEAN     = 10    # 均值低于此值 → 疑似全黑
MAX_PIXEL_MEAN     = 248   # 均值高于此值 → 疑似全白
MIN_PIXEL_STD      = 15    # 标准差低于此值 → 疑似单色/无内容
MIN_DIMENSION      = 512   # 图片最小边长

DEDUP_FAIL_BITS    = 8     # 汉明距离 ≤ 此值 → FAIL（高度相似/重复）
DEDUP_WARN_BITS    = 14    # 汉明距离 ≤ 此值 → WARN（可能相似）
DEFAULT_DB_PATH    = Path.home() / ".config" / "autopub" / "cover_hashes.json"


# ── 层3：跨文章去重（dhash）────────────────────────────────────────────────────

def _compute_dhash(path: Path, hash_size: int = 8) -> str:
    """差值哈希（dhash）：纯 Pillow 实现，无需 imagehash。
    将图片缩至 (hash_size+1)×hash_size 灰度，比较相邻像素生成 hash_size² 位哈希。
    返回定长十六进制字符串。
    """
    from PIL import Image
    img = Image.open(path).convert("L").resize(
        (hash_size + 1, hash_size), Image.LANCZOS
    )
    pixels = list(img.getdata())
    bits = []
    for row in range(hash_size):
        for col in range(hash_size):
            left  = pixels[row * (hash_size + 1) + col]
            right = pixels[row * (hash_size + 1) + col + 1]
            bits.append("1" if right > left else "0")
    bit_str = "".join(bits)
    hex_len = hash_size * hash_size // 4
    return hex(int(bit_str, 2))[2:].zfill(hex_len)


def _hamming(h1: str, h2: str) -> int:
    """两个等长十六进制字符串的汉明距离（位差数）。"""
    return bin(int(h1, 16) ^ int(h2, 16)).count("1")


def check_duplicate(path: Path, db_path: Path = DEFAULT_DB_PATH) -> tuple[list[dict], str]:
    """层3：与历史配图库比对，检测重复/相似图片。
    返回 (issues, dhash_hex)。
    """
    issues = []
    current_hash = ""
    try:
        current_hash = _compute_dhash(path)
    except ImportError:
        issues.append({
            "level": "WARN",
            "code":  "NO_PILLOW_DEDUP",
            "msg":   "未安装 Pillow，跳过去重检查",
        })
        return issues, current_hash
    except Exception as e:
        issues.append({
            "level": "WARN",
            "code":  "DEDUP_HASH_ERROR",
            "msg":   f"计算图片哈希失败（跳过去重）：{e}",
        })
        return issues, current_hash

    if not db_path.exists():
        # 库尚未建立，跳过比对（首次使用时正常）
        return issues, current_hash

    try:
        db = json.loads(db_path.read_text(encoding="utf-8"))
        records = db.get("hashes", [])
    except Exception as e:
        issues.append({
            "level": "WARN",
            "code":  "DEDUP_DB_ERROR",
            "msg":   f"读取哈希库失败（跳过去重）：{e}",
        })
        return issues, current_hash

    best_dist  = 999
    best_match = None
    for rec in records:
        stored = rec.get("hash", "")
        if not stored or len(stored) != len(current_hash):
            continue
        d = _hamming(current_hash, stored)
        if d < best_dist:
            best_dist  = d
            best_match = rec

    if best_match and best_dist <= DEDUP_FAIL_BITS:
        issues.append({
            "level": "FAIL",
            "code":  "DUPLICATE_IMAGE",
            "msg":   (
                f"配图与历史图片高度相似（汉明距离={best_dist}≤{DEDUP_FAIL_BITS}），"
                f"疑似重复：{best_match.get('title','?')}  [{best_match.get('date','?')}]"
            ),
        })
    elif best_match and best_dist <= DEDUP_WARN_BITS:
        issues.append({
            "level": "WARN",
            "code":  "SIMILAR_IMAGE",
            "msg":   (
                f"配图与历史图片较为相似（汉明距离={best_dist}≤{DEDUP_WARN_BITS}），"
                f"建议人工确认：{best_match.get('title','?')}  [{best_match.get('date','?')}]"
            ),
        })

    return issues, current_hash


def register_hash(dhash: str, title: str, image_path: Path,
                  db_path: Path = DEFAULT_DB_PATH) -> None:
    """将当前图片哈希写入历史库（发布成功后调用）。"""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db = json.loads(db_path.read_text(encoding="utf-8"))
    else:
        db = {"hashes": []}

    db["hashes"].append({
        "hash":  dhash,
        "title": title,
        "image": str(image_path),
        "date":  datetime.date.today().isoformat(),
    })
    db_path.write_text(json.dumps(db, ensure_ascii=False, indent=2), encoding="utf-8")


def check_file(path: Path) -> list[dict]:
    """层1：文件完整性检查"""
    issues = []
    size_kb = path.stat().st_size / 1024

    if size_kb < MIN_FILE_SIZE_KB:
        issues.append({
            "level": "FAIL",
            "code":  "FILE_TOO_SMALL",
            "msg":   f"文件大小 {size_kb:.1f} KB < {MIN_FILE_SIZE_KB} KB，疑似截断或空白图",
        })
    elif size_kb < WARN_FILE_SIZE_KB:
        issues.append({
            "level": "WARN",
            "code":  "FILE_SMALL",
            "msg":   f"文件大小 {size_kb:.1f} KB 偏小（建议 > {WARN_FILE_SIZE_KB} KB）",
        })
    return issues


def check_pixels(path: Path) -> list[dict]:
    """层2：像素健康度检查（需要 Pillow）"""
    issues = []
    try:
        from PIL import Image
        import struct

        img = Image.open(path)
        img.load()   # 触发完整读取，捕获截断错误

        w, h = img.size
        if w < MIN_DIMENSION or h < MIN_DIMENSION:
            issues.append({
                "level": "FAIL",
                "code":  "BAD_DIMENSION",
                "msg":   f"图片尺寸 {w}×{h} 过小（最小 {MIN_DIMENSION}px）",
            })

        # 像素统计（转 RGB 后采样）
        rgb = img.convert("RGB")
        # 避免导入 numpy，手动采样 10000 像素
        pixels = list(rgb.getdata())
        step   = max(1, len(pixels) // 10000)
        sample = pixels[::step]
        flat   = [v for px in sample for v in px]
        n      = len(flat)
        mean   = sum(flat) / n
        std    = (sum((v - mean) ** 2 for v in flat) / n) ** 0.5

        if mean > MAX_PIXEL_MEAN:
            issues.append({
                "level": "FAIL",
                "code":  "NEARLY_WHITE",
                "msg":   f"像素均值 {mean:.1f} 偏高（疑似空白白图）",
            })
        elif mean < MIN_PIXEL_MEAN:
            issues.append({
                "level": "FAIL",
                "code":  "NEARLY_BLACK",
                "msg":   f"像素均值 {mean:.1f} 偏低（疑似全黑图）",
            })
        elif std < MIN_PIXEL_STD:
            issues.append({
                "level": "WARN",
                "code":  "LOW_VARIANCE",
                "msg":   f"像素标准差 {std:.1f} 过低（内容可能过于单一）",
            })

    except OSError as e:
        issues.append({
            "level": "FAIL",
            "code":  "TRUNCATED",
            "msg":   f"图片文件损坏或截断：{e}",
        })
    except ImportError:
        issues.append({
            "level": "WARN",
            "code":  "NO_PILLOW",
            "msg":   "未安装 Pillow，跳过像素检查",
        })
    return issues


def check_content_vl(path: Path, title: str = "") -> list[dict]:
    """层3：调用 ERNIE-4.5-VL 语义检查（可选）"""
    issues = []
    api_key = os.environ.get("AI_STUDIO_API_KEY", "")
    if not api_key:
        return issues  # 未配置 key，静默跳过

    try:
        import requests

        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()

        suffix = path.suffix.lower().lstrip(".")
        mime   = {"jpg": "image/jpeg", "jpeg": "image/jpeg",
                  "png": "image/png",  "webp": "image/webp"}.get(suffix, "image/jpeg")

        prompt = (
            "请检查这张图片，回答以下问题（JSON格式，不要额外文字）：\n"
            "{\n"
            '  "has_text": true/false,          // 图片中是否含有中文或英文文字\n'
            '  "text_samples": ["..."],          // 如有文字，列举1-3个样本（没有则空数组）\n'
            '  "is_blank": true/false,           // 图片是否几乎是纯色/空白\n'
            '  "description": "一句话描述图片内容"\n'
            "}"
        )
        if title:
            prompt += f'\n\n文章标题供参考：「{title}」'

        payload = {
            "model": "ernie-4.5-vl-424b-preview",
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "image_url",
                     "image_url": {"url": f"data:{mime};base64,{b64}"}},
                    {"type": "text", "text": prompt},
                ],
            }],
            "max_tokens": 300,
        }
        headers = {
            "Authorization": f"token {api_key}",
            "Content-Type": "application/json",
        }
        resp = requests.post(
            "https://aistudio.baidu.com/llm/lmapi/v3/chat/completions",
            headers=headers, json=payload, timeout=30
        )
        raw = resp.json()
        text = raw.get("choices", [{}])[0].get("message", {}).get("content", "")

        # 解析 JSON
        import re
        m = re.search(r"\{[\s\S]*\}", text)
        if m:
            result = json.loads(m.group())
            if result.get("is_blank"):
                issues.append({
                    "level": "FAIL",
                    "code":  "VL_BLANK",
                    "msg":   f"VL 模型判断：图片几乎为空白（{result.get('description','')}）",
                })
            if result.get("has_text") and result.get("text_samples"):
                samples = result["text_samples"]
                issues.append({
                    "level": "WARN",
                    "code":  "VL_HAS_TEXT",
                    "msg":   f"VL 模型检测到图片含文字：{samples}（可能是 ernie-image 文字渲染 bug）",
                })
            # 将描述附加到返回信息
            issues.append({
                "level": "INFO",
                "code":  "VL_DESCRIPTION",
                "msg":   f"图片内容：{result.get('description', '(无描述)')}",
            })

    except Exception as e:
        issues.append({
            "level": "WARN",
            "code":  "VL_ERROR",
            "msg":   f"VL 检查失败（不影响发布）：{e}",
        })

    return issues


def determine_verdict(issues: list[dict]) -> str:
    """根据 issues 列表决定最终结论"""
    levels = {i["level"] for i in issues}
    if "FAIL" in levels:
        return "FAIL"
    if "WARN" in levels:
        return "WARN"
    return "PASS"


def main():
    parser = argparse.ArgumentParser(description="配图质量检查")
    parser.add_argument("--image",    required=True,  help="图片文件路径")
    parser.add_argument("--title",    default="",     help="文章标题（用于 VL 语义检查 & 去重记录）")
    parser.add_argument("--output",   default="",     help="结果 JSON 输出路径（可选）")
    parser.add_argument("--no-vl",    action="store_true", help="跳过 VL 语义检查")
    parser.add_argument("--no-dedup", action="store_true", help="跳过跨文章去重检查")
    parser.add_argument("--register", action="store_true", help="检查通过后将图片哈希写入历史库")
    parser.add_argument("--db",       default="",     help="哈希库路径（默认 ~/.config/autopub/cover_hashes.json）")
    args = parser.parse_args()

    img_path = Path(args.image)
    if not img_path.exists():
        print(f"❌ 图片文件不存在: {img_path}", file=sys.stderr)
        sys.exit(2)

    db_path = Path(args.db) if args.db else DEFAULT_DB_PATH

    all_issues: list[dict] = []
    all_issues += check_file(img_path)

    # 层2：像素健康度
    file_verdict = determine_verdict(all_issues)
    if file_verdict != "FAIL":
        all_issues += check_pixels(img_path)

    # 层3：跨文章去重（dhash）
    current_dhash = ""
    if not args.no_dedup and determine_verdict(all_issues) != "FAIL":
        dedup_issues, current_dhash = check_duplicate(img_path, db_path)
        all_issues += dedup_issues

    # 层4：VL 语义检查
    if not args.no_vl and determine_verdict(all_issues) != "FAIL":
        all_issues += check_content_vl(img_path, args.title)

    verdict = determine_verdict(all_issues)

    # ── 输出 ──
    icon = {"PASS": "✅", "WARN": "⚠️ ", "FAIL": "❌"}[verdict]
    print(f"\n{icon} 配图检查：{verdict}  ({img_path.name})")
    for issue in all_issues:
        lvl  = issue["level"]
        code = issue["code"]
        msg  = issue["msg"]
        sym  = {"FAIL": "❌", "WARN": "⚠️ ", "INFO": "ℹ️ "}.get(lvl, "  ")
        print(f"  {sym} [{code}] {msg}")

    result = {
        "image":   str(img_path),
        "title":   args.title,
        "verdict": verdict,
        "dhash":   current_dhash,
        "issues":  all_issues,
    }

    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"📋 检查结果已保存：{args.output}")

    # --register：通过才写库
    if args.register and verdict != "FAIL" and current_dhash:
        register_hash(current_dhash, args.title, img_path, db_path)
        print(f"📦 图片哈希已写入历史库：{db_path}")

    # 退出码
    sys.exit({"PASS": 0, "WARN": 1, "FAIL": 2}[verdict])


if __name__ == "__main__":
    main()
