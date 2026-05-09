#!/usr/bin/env python3
"""
wechat_cleanup.py — 清理微信公众号草稿箱与素材库
每次发布前调用，保留最新 N 篇草稿和最新 N 张素材图，删除其余。

用法：
    python3 wechat_cleanup.py [--keep-drafts 1] [--keep-images 1] [--dry-run]
"""
import os, argparse, requests, sys
from datetime import datetime

def get_token():
    r = requests.get("https://api.weixin.qq.com/cgi-bin/token", params={
        "grant_type": "client_credential",
        "appid":  os.environ["WECHAT_APP_ID"],
        "secret": os.environ["WECHAT_APP_SECRET"],
    })
    data = r.json()
    if "access_token" not in data:
        print(f"❌ 获取 access_token 失败: {data}")
        sys.exit(1)
    return data["access_token"]

def list_drafts(token):
    resp = requests.post(
        f"https://api.weixin.qq.com/cgi-bin/draft/batchget?access_token={token}",
        json={"offset": 0, "count": 20, "no_content": 0}
    ).json()
    return resp.get("item", [])

def delete_draft(token, media_id, dry_run):
    if dry_run:
        print(f"  [dry-run] 跳过删除草稿 {media_id[:40]}...")
        return True
    resp = requests.post(
        f"https://api.weixin.qq.com/cgi-bin/draft/delete?access_token={token}",
        json={"media_id": media_id}
    ).json()
    return resp.get("errcode", -1) == 0

def list_images(token):
    resp = requests.post(
        f"https://api.weixin.qq.com/cgi-bin/material/batchget_material?access_token={token}",
        json={"type": "image", "offset": 0, "count": 20}
    ).json()
    return resp.get("item", [])

def delete_image(token, media_id, dry_run):
    if dry_run:
        print(f"  [dry-run] 跳过删除素材 {media_id[:40]}...")
        return True
    resp = requests.post(
        f"https://api.weixin.qq.com/cgi-bin/material/del_material?access_token={token}",
        json={"media_id": media_id}
    ).json()
    return resp.get("errcode", -1) == 0

def main():
    parser = argparse.ArgumentParser(description="清理微信草稿箱与素材库")
    parser.add_argument("--keep-drafts", type=int, default=1, help="保留最新草稿数量（默认1）")
    parser.add_argument("--keep-images", type=int, default=1, help="保留最新素材图数量（默认1）")
    parser.add_argument("--dry-run", action="store_true", help="仅预览，不实际删除")
    args = parser.parse_args()

    print(f"🧹 微信草稿箱清理 {'[dry-run]' if args.dry_run else ''}")
    token = get_token()

    # ── 清理草稿 ──────────────────────────────────────
    drafts = list_drafts(token)
    drafts_sorted = sorted(drafts, key=lambda x: x.get("update_time", 0), reverse=True)
    print(f"\n📄 草稿箱共 {len(drafts_sorted)} 篇，保留最新 {args.keep_drafts} 篇")
    for i, d in enumerate(drafts_sorted):
        mid   = d["media_id"]
        title = d["content"]["news_item"][0]["title"] if d.get("content") else "未知标题"
        ts    = datetime.fromtimestamp(d.get("update_time", 0)).strftime("%m-%d %H:%M")
        if i < args.keep_drafts:
            print(f"  ✅ 保留: [{ts}] {title[:35]}")
        else:
            ok = delete_draft(token, mid, args.dry_run)
            print(f"  {'✅' if ok else '❌'} 删除: [{ts}] {title[:35]}")

    # ── 清理素材图片 ──────────────────────────────────
    images = list_images(token)
    images_sorted = sorted(images, key=lambda x: x.get("update_time", 0), reverse=True)
    print(f"\n🖼️  素材库共 {len(images_sorted)} 张图片，保留最新 {args.keep_images} 张")
    for i, img in enumerate(images_sorted):
        mid  = img["media_id"]
        name = img.get("name", "")
        ts   = datetime.fromtimestamp(img.get("update_time", 0)).strftime("%m-%d %H:%M")
        if i < args.keep_images:
            print(f"  ✅ 保留: [{ts}] {name}")
        else:
            ok = delete_image(token, mid, args.dry_run)
            print(f"  {'✅' if ok else '❌'} 删除: [{ts}] {name}")

    print("\n✅ 清理完成")

if __name__ == "__main__":
    main()
