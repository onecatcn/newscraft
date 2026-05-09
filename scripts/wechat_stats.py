#!/usr/bin/env python3
"""wechat_stats.py -- 每日拉取微信公众号文章数据统计.

从微信公众平台数据接口获取:
- 图文群发总数据（每篇文章累计）
- 图文群发每日数据
- 用户增减数据

数据存储为 JSON 文件，按日期归档到 data/stats/ 目录。

Usage:
    python scripts/wechat_stats.py                  # 拉取前天的数据（最近可用）
    python scripts/wechat_stats.py --date 2026-04-18  # 拉取指定日期
    python scripts/wechat_stats.py --range 7        # 拉取最近7天

Env vars:
    WECHAT_APP_ID       公众号 AppID
    WECHAT_APP_SECRET   公众号 AppSecret
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

try:
    import requests
except ImportError:
    print("Error: requests library required. Install with: pip install requests")
    sys.exit(1)


# --- Configuration ---

DATA_DIR = Path(os.environ.get("STATS_DATA_DIR",
                               Path(__file__).parent.parent / "data" / "stats"))

TOKEN_CACHE_FILE = DATA_DIR / ".token_cache.json"

WECHAT_API_BASE = "https://api.weixin.qq.com"


# --- Token Management ---

def get_access_token(app_id: str, app_secret: str) -> str:
    """获取 access_token，带本地缓存（有效期 2 小时）."""
    # Check cache
    if TOKEN_CACHE_FILE.exists():
        cache = json.loads(TOKEN_CACHE_FILE.read_text())
        if cache.get("expires_at", 0) > time.time() + 300:  # 留 5 分钟余量
            return cache["access_token"]

    # Request new token
    url = f"{WECHAT_API_BASE}/cgi-bin/token"
    params = {
        "grant_type": "client_credential",
        "appid": app_id,
        "secret": app_secret,
    }
    resp = requests.get(url, params=params, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    if "access_token" not in data:
        raise RuntimeError(f"获取 access_token 失败: {data}")

    # Cache token
    TOKEN_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    cache = {
        "access_token": data["access_token"],
        "expires_at": time.time() + data.get("expires_in", 7200),
    }
    TOKEN_CACHE_FILE.write_text(json.dumps(cache))

    return data["access_token"]


# --- Data Fetching ---

def fetch_article_total(token: str, date: str) -> dict:
    """获取图文群发总数据（getarticletotal）.

    返回指定日期群发的文章累计数据。
    注意：该接口只能查询 1 天的数据。
    """
    url = f"{WECHAT_API_BASE}/datacube/getarticletotal"
    body = {"begin_date": date, "end_date": date}
    resp = requests.post(url, params={"access_token": token},
                         json=body, timeout=15)
    resp.raise_for_status()
    return resp.json()


def fetch_article_summary(token: str, date: str) -> dict:
    """获取图文群发每日数据（getarticlesummary）."""
    url = f"{WECHAT_API_BASE}/datacube/getarticlesummary"
    body = {"begin_date": date, "end_date": date}
    resp = requests.post(url, params={"access_token": token},
                         json=body, timeout=15)
    resp.raise_for_status()
    return resp.json()


def fetch_user_read(token: str, date: str) -> dict:
    """获取图文统计数据（getuserread）- 阅读来源分布."""
    url = f"{WECHAT_API_BASE}/datacube/getuserread"
    body = {"begin_date": date, "end_date": date}
    resp = requests.post(url, params={"access_token": token},
                         json=body, timeout=15)
    resp.raise_for_status()
    return resp.json()


def fetch_user_read_hour(token: str, date: str) -> dict:
    """获取图文统计分时数据（getuserreadhour）- 小时级阅读分布."""
    url = f"{WECHAT_API_BASE}/datacube/getuserreadhour"
    body = {"begin_date": date, "end_date": date}
    resp = requests.post(url, params={"access_token": token},
                         json=body, timeout=15)
    resp.raise_for_status()
    return resp.json()


def fetch_user_summary(token: str, date: str) -> dict:
    """获取用户增减数据（getusersummary）."""
    url = f"{WECHAT_API_BASE}/datacube/getusersummary"
    body = {"begin_date": date, "end_date": date}
    resp = requests.post(url, params={"access_token": token},
                         json=body, timeout=15)
    resp.raise_for_status()
    return resp.json()


# --- Main Logic ---

def pull_stats_for_date(token: str, date_str: str) -> dict:
    """拉取指定日期的全部统计数据."""
    print(f"  拉取 {date_str} 的数据...")

    result = {
        "date": date_str,
        "pulled_at": datetime.now().isoformat(),
        "article_total": fetch_article_total(token, date_str),
        "article_summary": fetch_article_summary(token, date_str),
        "user_read": fetch_user_read(token, date_str),
        "user_read_hour": fetch_user_read_hour(token, date_str),
        "user_summary": fetch_user_summary(token, date_str),
    }

    # Check for API errors
    for key, data in result.items():
        if isinstance(data, dict) and "errcode" in data and data["errcode"] != 0:
            print(f"    [WARN] {key}: errcode={data['errcode']}, "
                  f"errmsg={data.get('errmsg', '')}")

    return result


def save_stats(stats: dict, date_str: str):
    """保存统计数据到 JSON 文件."""
    out_dir = DATA_DIR / date_str[:7]  # 按月份归档 e.g. 2026-04
    out_dir.mkdir(parents=True, exist_ok=True)

    out_file = out_dir / f"{date_str}.json"
    out_file.write_text(json.dumps(stats, ensure_ascii=False, indent=2))
    print(f"  已保存: {out_file}")


def print_summary(stats: dict):
    """打印关键指标摘要."""
    date = stats["date"]
    articles = stats.get("article_total", {}).get("list", [])
    user_data = stats.get("user_summary", {}).get("list", [])

    print(f"\n{'='*50}")
    print(f"  日期: {date}")
    print(f"{'='*50}")

    if articles:
        print(f"\n  文章数据 ({len(articles)} 篇群发):")
        for item in articles:
            title = item.get("title", "无标题")
            details = item.get("details", [])
            if details:
                d = details[0]  # 第一天的数据
                pv = d.get("int_page_read_count", 0)
                uv = d.get("int_page_read_user", 0)
                share = d.get("share_count", 0)
                fav = d.get("add_to_fav_user", 0)
                print(f"    [{title}]")
                print(f"      阅读 {pv} 次 / {uv} 人 | "
                      f"分享 {share} 次 | 收藏 {fav} 人")
    else:
        print(f"\n  该日无群发文章数据")

    if user_data:
        for u in user_data:
            new = u.get("new_user", 0)
            cancel = u.get("cancel_user", 0)
            net = new - cancel
            print(f"\n  用户增减: +{new} / -{cancel} (净增 {net:+d})")

    print()


def main():
    parser = argparse.ArgumentParser(description="微信公众号文章数据每日拉取")
    parser.add_argument("--date", type=str, default=None,
                        help="指定日期 (YYYY-MM-DD)，默认为前天")
    parser.add_argument("--range", type=int, default=1,
                        help="拉取天数，从指定日期往前算 (默认 1)")
    parser.add_argument("--quiet", action="store_true",
                        help="静默模式，不打印摘要")
    args = parser.parse_args()

    # Validate credentials
    app_id = os.environ.get("WECHAT_APP_ID", "")
    app_secret = os.environ.get("WECHAT_APP_SECRET", "")
    if not app_id or not app_secret:
        print("Error: 请设置环境变量 WECHAT_APP_ID 和 WECHAT_APP_SECRET")
        print("  export WECHAT_APP_ID='your_app_id'")
        print("  export WECHAT_APP_SECRET='your_app_secret'")
        sys.exit(1)

    # Determine date range
    if args.date:
        end_date = datetime.strptime(args.date, "%Y-%m-%d")
    else:
        # 微信数据有 1-3 天延迟，默认拉前天的
        end_date = datetime.now() - timedelta(days=2)

    dates = [(end_date - timedelta(days=i)).strftime("%Y-%m-%d")
             for i in range(args.range)]
    dates.reverse()  # 按时间正序

    print(f"微信公众号数据拉取")
    print(f"日期范围: {dates[0]} ~ {dates[-1]} ({len(dates)} 天)")
    print(f"存储目录: {DATA_DIR}\n")

    # Get token
    try:
        token = get_access_token(app_id, app_secret)
        print("access_token 获取成功\n")
    except Exception as e:
        print(f"Error: 获取 access_token 失败: {e}")
        sys.exit(1)

    # Pull data for each date
    for date_str in dates:
        try:
            stats = pull_stats_for_date(token, date_str)
            save_stats(stats, date_str)
            if not args.quiet:
                print_summary(stats)
        except Exception as e:
            print(f"  [ERROR] {date_str}: {e}")

    print("完成!")


if __name__ == "__main__":
    main()
