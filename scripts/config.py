#!/usr/bin/env python3
"""config.py -- Unified configuration for newscraft Docker service.

Reads config from:
1. Environment variables (highest priority)
2. pipeline_config.json (file-based defaults)

All API keys come from environment variables only.
"""

import json
import os
from pathlib import Path


# Base paths
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
APP_DIR = Path(os.environ.get("APP_DIR", "/app"))

# Derived paths
DAILY_PIPELINE_DIR = DATA_DIR / "daily_pipeline"
STATE_DIR = DATA_DIR / "state"
LOGS_DIR = DATA_DIR / "logs"
CONTENT_LOG_PATH = DATA_DIR / "content_log.md"
PIPELINE_CONFIG_PATH = APP_DIR / "pipeline_config.json"


def load_pipeline_config() -> dict:
    """Load pipeline_config.json, return empty dict if missing."""
    if PIPELINE_CONFIG_PATH.exists():
        with open(PIPELINE_CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def get_config() -> dict:
    """Build unified config from env vars + pipeline_config.json."""
    file_cfg = load_pipeline_config()

    return {
        # Account
        "account_name": file_cfg.get("account_name", "AI 每日 10 分钟"),
        "account_type": file_cfg.get("account_type", "subscription"),
        "author": file_cfg.get("author", "AI 每日 10 分钟"),
        # Content rules
        "target_word_count": file_cfg.get(
            "target_word_count", {"min": 800, "max": 1000}
        ),
        "dedup_window_days": file_cfg.get("dedup_window_days", 7),
        "topic_count": file_cfg.get("topic_count", 10),
        "image_style": file_cfg.get("image_style", "tech_minimalist"),
        # Digest mode
        "digest_mode": file_cfg.get("digest_mode", "daily"),
        "digest_topic_count": file_cfg.get("digest_topic_count", 10),
        "digest_word_count": file_cfg.get(
            "digest_word_count", {"min": 1500, "max": 2500}
        ),
        "digest_web_search_top_n": file_cfg.get("digest_web_search_top_n", 3),
        "digest_categories": file_cfg.get(
            "digest_categories",
            ["产品", "模型", "研究", "行业", "开源", "硬件", "机器人"],
        ),
        # Fetch source config
        "fetch_category": file_cfg.get("fetch_category", "ai"),
        "fetch_period": file_cfg.get("fetch_period", "24h"),
        # API Keys (env only)
        "mp_app_id": os.environ.get("MP_APP_ID", ""),
        "mp_app_secret": os.environ.get("MP_APP_SECRET", ""),
        "ai_studio_api_key": os.environ.get("AI_STUDIO_API_KEY", ""),
        # Notification (optional IM bot integration)
        "notify_webhook_url": os.environ.get("NOTIFY_WEBHOOK_URL", ""),
        # Timeouts (minutes)
        "topic_selection_timeout": int(
            os.environ.get("TOPIC_SELECTION_TIMEOUT", "30")
        ),
        "review_approval_timeout": int(
            os.environ.get("REVIEW_APPROVAL_TIMEOUT", "30")
        ),
        # Proxy
        "http_proxy": os.environ.get("HTTP_PROXY", ""),
        "https_proxy": os.environ.get("HTTPS_PROXY", ""),
        # Paths
        "data_dir": str(DATA_DIR),
        "daily_pipeline_dir": str(DAILY_PIPELINE_DIR),
        "state_dir": str(STATE_DIR),
        "logs_dir": str(LOGS_DIR),
        "content_log_path": str(CONTENT_LOG_PATH),
    }


def validate_config(cfg: dict) -> list:
    """Validate required config, return list of missing items."""
    missing = []
    required_keys = [
        ("mp_app_id", "MP_APP_ID"),
        ("mp_app_secret", "MP_APP_SECRET"),
        ("ai_studio_api_key", "AI_STUDIO_API_KEY"),
    ]
    for key, env_name in required_keys:
        if not cfg.get(key):
            missing.append(env_name)
    return missing


def validate_ernie_config(cfg: dict) -> list:
    """Validate AI Studio API config."""
    missing = []
    if not cfg.get("ai_studio_api_key"):
        missing.append("AI_STUDIO_API_KEY")
    return missing


def validate_mp_config(cfg: dict) -> list:
    """Validate WeChat MP config."""
    missing = []
    if not cfg.get("mp_app_id"):
        missing.append("MP_APP_ID")
    if not cfg.get("mp_app_secret"):
        missing.append("MP_APP_SECRET")
    return missing
