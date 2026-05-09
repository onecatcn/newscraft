#!/usr/bin/env python3
"""wechat_upload_image.py — 上传图片到微信素材库

用法:
    python3 wechat_upload_image.py \
        --image-dir 05_images/ \
        --output 05_images/media_ids.json

功能:
    1. 扫描 image-dir 中的图片文件（.png, .jpg, .jpeg, .gif）
    2. 封面图(cover.*)上传为永久素材 → 获得 media_id
    3. 正文图(inline_*.*)上传为图文消息图片 → 获得 mmbiz URL
    4. 输出 media_ids.json

环境变量:
    WECHAT_APP_ID       微信 AppID
    WECHAT_APP_SECRET   微信 AppSecret
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path


WECHAT_API_BASE = "https://api.weixin.qq.com"
TOKEN_CACHE = {"token": None, "expires_at": 0}


def get_access_token() -> str:
    """获取或刷新 access_token"""
    import time

    # Check cache
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
    print(f"✅ access_token 获取成功（有效期 {data.get('expires_in', 7200)}s）", file=sys.stderr)
    return TOKEN_CACHE["token"]


def upload_permanent_material(image_path: str, access_token: str) -> dict:
    """上传永久素材（用于封面图，获得 media_id）

    POST /cgi-bin/material/add_material?access_token=TOKEN&type=image
    """
    url = f"{WECHAT_API_BASE}/cgi-bin/material/add_material?access_token={access_token}&type=image"

    # Build multipart form data
    boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
    filename = os.path.basename(image_path)

    with open(image_path, "rb") as f:
        file_data = f.read()

    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="media"; filename="{filename}"\r\n'
        f"Content-Type: image/png\r\n\r\n"
    ).encode("utf-8") + file_data + f"\r\n--{boundary}--\r\n".encode("utf-8")

    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError) as e:
        return {"errcode": -1, "errmsg": str(e)}


def upload_article_image(image_path: str, access_token: str) -> dict:
    """上传图文消息内的图片（获得 mmbiz URL）

    POST /cgi-bin/media/uploadimg?access_token=TOKEN
    """
    url = f"{WECHAT_API_BASE}/cgi-bin/media/uploadimg?access_token={access_token}"

    boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
    filename = os.path.basename(image_path)

    with open(image_path, "rb") as f:
        file_data = f.read()

    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="media"; filename="{filename}"\r\n'
        f"Content-Type: image/png\r\n\r\n"
    ).encode("utf-8") + file_data + f"\r\n--{boundary}--\r\n".encode("utf-8")

    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError) as e:
        return {"errcode": -1, "errmsg": str(e)}


def upload_images(image_dir: str, output_path: str):
    """主上传流程"""
    image_dir = Path(image_dir)
    if not image_dir.exists():
        print(f"❌ 图片目录不存在: {image_dir}", file=sys.stderr)
        sys.exit(1)

    # Find image files
    image_extensions = {".png", ".jpg", ".jpeg", ".gif"}
    images = [
        f
        for f in sorted(image_dir.iterdir())
        if f.suffix.lower() in image_extensions
    ]

    if not images:
        print(f"⚠️  图片目录为空: {image_dir}", file=sys.stderr)
        print("请先准备配图再运行发布流程", file=sys.stderr)
        # Write empty result
        result = {
            "uploaded_at": datetime.now().isoformat(),
            "cover": None,
            "inline": [],
            "error": "no images found",
        }
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        sys.exit(1)

    print(f"📸 发现 {len(images)} 张图片", file=sys.stderr)

    # Get access token
    access_token = get_access_token()

    cover_result = None
    inline_results = []

    for img in images:
        name = img.stem.lower()
        size_mb = img.stat().st_size / (1024 * 1024)
        print(f"\n📤 上传: {img.name} ({size_mb:.1f} MB)", file=sys.stderr)

        # Check size limits
        if name.startswith("cover") and size_mb > 2:
            print(f"  ⚠️  永久素材限制 2MB，当前 {size_mb:.1f}MB", file=sys.stderr)
            print("  请压缩图片后重试", file=sys.stderr)
            continue
        elif size_mb > 10:
            print(f"  ⚠️  素材限制 10MB，当前 {size_mb:.1f}MB", file=sys.stderr)
            continue

        if name.startswith("cover"):
            # Upload as permanent material for cover
            resp = upload_permanent_material(str(img), access_token)
            if "media_id" in resp:
                print(f"  ✅ 封面图上传成功: media_id={resp['media_id']}", file=sys.stderr)
                cover_result = {
                    "filename": img.name,
                    "media_id": resp["media_id"],
                    "url": resp.get("url", ""),
                }
            else:
                print(f"  ❌ 上传失败: {resp}", file=sys.stderr)
        else:
            # Upload as article image
            resp = upload_article_image(str(img), access_token)
            if "url" in resp:
                print(f"  ✅ 正文图上传成功: {resp['url'][:60]}...", file=sys.stderr)
                inline_results.append(
                    {
                        "filename": img.name,
                        "url": resp["url"],
                    }
                )
            else:
                print(f"  ❌ 上传失败: {resp}", file=sys.stderr)

    # Write result
    result = {
        "uploaded_at": datetime.now().isoformat(),
        "cover": cover_result,
        "inline": inline_results,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"\n✅ 上传完成 → {output_path}", file=sys.stderr)
    if cover_result:
        print(f"  封面: {cover_result['media_id']}", file=sys.stderr)
    else:
        print(f"  ⚠️  无封面图（需要 cover.png）", file=sys.stderr)
    print(f"  正文图: {len(inline_results)} 张", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="上传图片到微信素材库")
    parser.add_argument("--image-dir", required=True, help="图片目录路径")
    parser.add_argument("--output", required=True, help="输出 media_ids.json 文件路径")
    args = parser.parse_args()

    upload_images(args.image_dir, args.output)


if __name__ == "__main__":
    main()
