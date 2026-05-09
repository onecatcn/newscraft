#!/usr/bin/env python3
"""orchestrator.py -- State-machine driven pipeline orchestrator.

Drives the daily newscraft pipeline through these states:

    IDLE -> FETCHING -> NOTIFY_TOPIC_LIST -> AWAITING_SELECTION
      -> GENERATING -> NOTIFY_REVIEW -> AWAITING_APPROVAL
      -> PUBLISHING -> NOTIFY_DRAFT -> DONE

Three human checkpoints (via webhook notification):
1. Topic selection: reply with a number 1-10
2. Draft review: reply "ok" or correction text
3. Publish notification: informational only

State is persisted to daily_pipeline/{date}/pipeline_state.json.
Replies come from /data/state/reply_queue.json (written by callback_server).

Usage:
    python3 orchestrator.py           # run today's pipeline
    python3 orchestrator.py --date 2026-03-30   # run for specific date
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# Add src/ to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import get_config, validate_config, validate_ernie_config, validate_mp_config
from notify import NotifyClient
from callback_server import read_and_clear_replies


# ── State machine states ──

STATES = [
    "IDLE",
    "FETCHING",
    "NOTIFY_TOPIC_LIST",
    "AWAITING_SELECTION",
    "DIGEST_GENERATING",
    "GENERATING",
    "NOTIFY_REVIEW",
    "AWAITING_APPROVAL",
    "PUBLISHING",
    "NOTIFY_DRAFT",
    "DONE",
    "ERROR",
]


# ── Paths ──

def pipeline_dir(cfg: dict, date_str: str) -> Path:
    return Path(cfg["daily_pipeline_dir"]) / date_str


def state_file(cfg: dict, date_str: str) -> Path:
    return pipeline_dir(cfg, date_str) / "pipeline_state.json"


SCRIPTS_DIR = Path(os.environ.get("APP_DIR", "/app")) / "scripts"
SRC_DIR = Path(__file__).resolve().parent


# ── State persistence ──

def load_state(cfg: dict, date_str: str) -> dict:
    """Load pipeline state for a given date."""
    sf = state_file(cfg, date_str)
    if sf.exists():
        try:
            return json.loads(sf.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"state": "IDLE", "date": date_str}


def save_state(cfg: dict, date_str: str, state_data: dict):
    """Persist pipeline state."""
    pdir = pipeline_dir(cfg, date_str)
    pdir.mkdir(parents=True, exist_ok=True)
    state_data["updated_at"] = datetime.now().isoformat()
    state_file(cfg, date_str).write_text(
        json.dumps(state_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ── Script execution helpers ──

def run_script(script_name: str, args: list = None, cwd: str = None,
               timeout: int = 300) -> subprocess.CompletedProcess:
    """Run a pipeline script, returning CompletedProcess."""
    if script_name.endswith(".sh"):
        cmd = ["bash", str(SCRIPTS_DIR / script_name)]
    elif script_name.endswith(".py"):
        cmd = ["python3", str(SCRIPTS_DIR / script_name)]
    else:
        cmd = [str(SCRIPTS_DIR / script_name)]

    if args:
        cmd.extend(args)

    print(f"[orchestrator] running: {' '.join(cmd)}", file=sys.stderr)
    result = subprocess.run(
        cmd, capture_output=True, text=True, cwd=cwd, timeout=timeout
    )

    if result.returncode != 0:
        print(f"[orchestrator] script failed (rc={result.returncode}):", file=sys.stderr)
        print(result.stderr[-500:] if result.stderr else "(no stderr)", file=sys.stderr)

    return result


def run_src_script(script_name: str, args: list = None, cwd: str = None,
                   timeout: int = 300) -> subprocess.CompletedProcess:
    """Run a src/ Python module."""
    cmd = ["python3", str(SRC_DIR / script_name)]
    if args:
        cmd.extend(args)

    print(f"[orchestrator] running: {' '.join(cmd)}", file=sys.stderr)
    return subprocess.run(
        cmd, capture_output=True, text=True, cwd=cwd, timeout=timeout
    )


# ── Wait for reply ──

def wait_for_reply(timeout_minutes: int) -> str | None:
    """Poll reply_queue.json until a reply arrives or timeout.

    Returns the reply content string, or None on timeout.
    """
    deadline = time.time() + timeout_minutes * 60
    poll_interval = 10  # seconds

    while time.time() < deadline:
        replies = read_and_clear_replies()
        if replies:
            # Return the first reply's content
            content = replies[0].get("content", "").strip()
            if content:
                print(f"[orchestrator] received reply: {content[:80]}", file=sys.stderr)
                return content
        time.sleep(poll_interval)

    print(f"[orchestrator] reply timeout ({timeout_minutes}m)", file=sys.stderr)
    return None


# ── Notification message templates ──

def format_topic_list(date_str: str, topics: list) -> str:
    """Format topic selection notification."""
    lines = [f"## AI 每日 10 分钟 - 今日候选 ({date_str})", "请回复数字选择深度文章，或 d 选择速递模式：\n"]
    for i, t in enumerate(topics[:10], 1):
        dup_mark = " [重复]" if t.get("is_duplicate") else ""
        score = t.get("composite_score", t.get("heat_score", 0))
        lines.append(
            f"{i}. **{t.get('title', '?')}** (热度 {score}){dup_mark}"
        )
        summary = t.get("summary", "")
        if summary:
            lines.append(f"   {summary[:60]}")
    lines.append(f"\nd — 每日速递（8-10条分类速览，默认）")
    lines.append(f"g — 深度文章（选第1个主题）")
    lines.append(f"> 30分钟内无回复将自动选择速递模式")
    return "\n".join(lines)


def format_review_report(title: str, review: dict) -> str:
    """Format review notification."""
    summary = review.get("summary", {})
    word_count = review.get("word_count", 0)
    passed = summary.get("passed", 0)
    total = summary.get("total_checks", 0)
    overall = summary.get("overall", "unknown")

    warn_items = []
    for c in review.get("checks", []):
        if c.get("status") in ("fail", "warn"):
            warn_items.append(f"- {c.get('item', '?')}: {c.get('detail', '')[:60]}")

    lines = [
        "## 质量审核报告",
        f"**标题**: {title}",
        f"**字数**: {word_count}",
        f"**检查**: {passed}/{total} 通过 ({overall})",
    ]
    if warn_items:
        lines.append("\n**需关注:**")
        lines.extend(warn_items[:5])

    lines.append("\n> 回复 ok 通过，或回复修改意见")
    return "\n".join(lines)


def format_publish_notify(title: str) -> str:
    """Format publish notification."""
    return (
        f"## 草稿已创建\n"
        f"**标题**: {title}\n"
        f"请登录 mp.weixin.qq.com 手动群发"
    )


def format_error_notify(stage: str, error: str) -> str:
    """Format error notification."""
    return (
        f"## Pipeline 错误\n"
        f"**阶段**: {stage}\n"
        f"**错误**: {error[:200]}\n"
    )


# ── Main pipeline ──

def run_pipeline(date_str: str = None):
    """Execute the pipeline state machine."""
    if not date_str:
        date_str = datetime.now().strftime("%Y-%m-%d")

    cfg = get_config()

    # Validate basic config
    missing = validate_config(cfg)
    if missing:
        print(f"[orchestrator] missing config: {', '.join(missing)}", file=sys.stderr)
        print("[orchestrator] continuing with limited functionality", file=sys.stderr)

    # Initialize notification client (if configured)
    notifier = None
    if cfg.get("notify_webhook_url"):
        notifier = NotifyClient(
            cfg["notify_webhook_url"],
            
            
            
        )

    # Load current state
    pstate = load_state(cfg, date_str)
    current = pstate.get("state", "IDLE")

    # Skip if already done or in error
    if current == "DONE":
        print(f"[orchestrator] {date_str} already done, skipping", file=sys.stderr)
        return
    if current == "ERROR":
        print(f"[orchestrator] {date_str} in ERROR state, skipping", file=sys.stderr)
        return

    pdir = pipeline_dir(cfg, date_str)
    pdir.mkdir(parents=True, exist_ok=True)

    try:
        # ── Phase 1: Fetch topics ──
        if current == "IDLE":
            print(f"[orchestrator] === Phase 1: Fetch ({date_str}) ===", file=sys.stderr)
            pstate["state"] = "FETCHING"
            save_state(cfg, date_str, pstate)

            # Run multisource_fetch.py -> raw JSON
            raw_output_path = str(pdir / "raw_topics.json")
            result = run_script("multisource_fetch.py", [
                "--category", cfg["fetch_category"],
                "--period", cfg["fetch_period"],
                "--limit", str(cfg["topic_count"] * 2),
            ])

            if result.returncode != 0:
                raise RuntimeError(f"multisource_fetch failed: {result.stderr[-200:]}")

            # Save raw output
            with open(raw_output_path, "w", encoding="utf-8") as f:
                f.write(result.stdout)

            # Run topic_parse.py
            topics_path = str(pdir / "01_topics.json")
            result = run_script("topic_parse.py", [
                "--input", raw_output_path,
                "--content-log", cfg["content_log_path"],
                "--output", topics_path,
                "--top-n", str(cfg["topic_count"]),
            ])

            if result.returncode != 0:
                raise RuntimeError(f"topic_parse failed: {result.stderr[-200:]}")

            # Load topics for notification
            with open(topics_path, "r", encoding="utf-8") as f:
                topics_data = json.load(f)
            topics = topics_data.get("topics", [])

            if not topics:
                raise RuntimeError("No topics fetched")

            # Send topic list notification
            pstate["state"] = "NOTIFY_TOPIC_LIST"
            pstate["topics_count"] = len(topics)
            save_state(cfg, date_str, pstate)

            if notifier:
                msg = format_topic_list(date_str, topics)
                notifier.send_md(msg)
                print("[orchestrator] topic list notification sent", file=sys.stderr)

            pstate["state"] = "AWAITING_SELECTION"
            pstate["selection_sent_at"] = datetime.now().isoformat()
            save_state(cfg, date_str, pstate)
            current = "AWAITING_SELECTION"

        # ── Wait for topic selection ──
        if current == "AWAITING_SELECTION":
            print("[orchestrator] waiting for topic selection...", file=sys.stderr)
            reply = wait_for_reply(cfg["topic_selection_timeout"])

            # Parse selection: d/digest → 速递模式, g → 深度第1个, 1-10 → 深度对应
            is_digest = True  # default to digest mode
            selection = 0

            if reply:
                reply_lower = reply.strip().lower()
                if reply_lower in ("d", "digest", "速递"):
                    is_digest = True
                    print("[orchestrator] digest mode selected", file=sys.stderr)
                elif reply_lower in ("g", "generate", "深度"):
                    is_digest = False
                    selection = 1
                    print("[orchestrator] depth mode selected (topic 1)", file=sys.stderr)
                else:
                    try:
                        num = int(reply.strip())
                        if 1 <= num <= cfg["topic_count"]:
                            is_digest = False
                            selection = num
                            print(f"[orchestrator] depth mode, topic {num} selected",
                                  file=sys.stderr)
                        else:
                            print(f"[orchestrator] invalid selection '{reply}', using digest mode",
                                  file=sys.stderr)
                    except ValueError:
                        print(f"[orchestrator] non-numeric reply '{reply}', using digest mode",
                              file=sys.stderr)
            else:
                print("[orchestrator] timeout, auto-selecting digest mode", file=sys.stderr)

            if is_digest:
                # ── 速递模式 ──
                pstate["mode"] = "digest"
                pstate["state"] = "DIGEST_GENERATING"
                save_state(cfg, date_str, pstate)
                current = "DIGEST_GENERATING"

                if notifier:
                    notifier.send_md("**已选择**: 每日速递模式\n开始生成速递文章...")
            else:
                # ── 深度模式 ──
                # Load topics and select
                topics_path = str(pdir / "01_topics.json")
                with open(topics_path, "r", encoding="utf-8") as f:
                    topics_data = json.load(f)
                topics = topics_data.get("topics", [])

                if selection > len(topics):
                    selection = 1
                selected = topics[selection - 1]

                # Enrich selected topic
                selected_topic = {
                    "topic_id": selected.get("id", ""),
                    "title": selected.get("title", ""),
                    "summary": selected.get("summary", ""),
                    "heat_score": selected.get("heat_score", 0),
                    "source_urls": selected.get("source_urls", []),
                    "tags": selected.get("tags", []),
                    "selected_rank": selection,
                    "selected_at": datetime.now().isoformat(),
                }

                selected_path = str(pdir / "02_topic_selected.json")
                with open(selected_path, "w", encoding="utf-8") as f:
                    json.dump(selected_topic, f, ensure_ascii=False, indent=2)

                pstate["mode"] = "depth"
                pstate["state"] = "GENERATING"
                pstate["selected_topic"] = selected_topic["title"]
                pstate["selected_rank"] = selection
                save_state(cfg, date_str, pstate)
                current = "GENERATING"

                if notifier:
                    notifier.send_md(
                        f"**已选择**: {selection}. {selected_topic['title']}\n开始生成深度文章..."
                    )

        # ── Phase 1.5: Digest Generate ──
        if current == "DIGEST_GENERATING":
            print(f"[orchestrator] === Phase 1.5: Digest Generate ({date_str}) ===",
                  file=sys.stderr)

            topics_path = str(pdir / "01_topics.json")
            digest_prompt_path = str(pdir / "03_digest_prompt.md")
            draft_path = pdir / "04_digest.md"
            images_dir = str(pdir / "05_images_digest")
            review_path = str(pdir / "06_review_digest.json")

            # Step 1: Run daily_digest.py to generate prompt
            result = run_script("daily_digest.py", [
                "--input", topics_path,
                "--output", digest_prompt_path,
                "--web-search-topics", str(cfg.get("digest_web_search_top_n", 3)),
                "--max-topics", str(cfg.get("digest_topic_count", 10)),
            ])
            if result.returncode != 0:
                raise RuntimeError(f"daily_digest failed: {result.stderr[-200:]}")

            # Step 2: Check if draft was generated externally by autopub skill
            if not draft_path.exists():
                print("[orchestrator] 04_digest.md not found — waiting for ERNIE to generate",
                      file=sys.stderr)
                print("[orchestrator] Run: /autopub digest", file=sys.stderr)
                return  # Stay in DIGEST_GENERATING state, exit gracefully

            draft_path = str(draft_path)
            print(f"[orchestrator] digest draft found: {draft_path}", file=sys.stderr)

            # Step 3: Prepare image prompts from draft
            result = run_script("gems_prepare.py", [
                "--draft", draft_path,
                "--output-dir", images_dir,
            ])
            # Non-fatal: image prompts are optional

            # Step 4: Generate images (requires AI_STUDIO_API_KEY)
            if cfg.get("ai_studio_api_key"):
                result = run_script("gems_generate.py", [
                    "--prompts-dir", images_dir,
                    "--output-dir", images_dir,
                ])
                if result.returncode != 0:
                    print("[orchestrator] gems_generate failed (non-fatal)",
                          file=sys.stderr)

            # Step 5: Quality check (digest mode)
            result = run_script("quality_check.py", [
                "--draft", draft_path,
                "--output", review_path,
            ])
            if result.returncode != 0:
                raise RuntimeError(f"quality_check failed: {result.stderr[-200:]}")

            # Load review for notification
            with open(review_path, "r", encoding="utf-8") as f:
                review_data = json.load(f)

            title = "每日AI速递"

            # Send review report
            pstate["state"] = "NOTIFY_REVIEW"
            save_state(cfg, date_str, pstate)

            if notifier:
                msg = format_review_report(title, review_data)
                notifier.send_md(msg)

            pstate["state"] = "AWAITING_APPROVAL"
            pstate["approval_sent_at"] = datetime.now().isoformat()
            pstate["mode"] = "digest"
            save_state(cfg, date_str, pstate)
            current = "AWAITING_APPROVAL"

        # ── Phase 2: Generate ──
        if current == "GENERATING":
            print("[orchestrator] === Phase 2: Generate ===", file=sys.stderr)
            selected_path = str(pdir / "02_topic_selected.json")
            draft_path = pdir / "04_draft.md"
            images_dir = str(pdir / "05_images")
            review_path = str(pdir / "06_review.json")

            # Step 1: Check if draft was generated externally by autopub skill
            if not draft_path.exists():
                print("[orchestrator] 04_draft.md not found — waiting for ERNIE to generate",
                      file=sys.stderr)
                print("[orchestrator] Run: /autopub generate", file=sys.stderr)
                return  # Stay in GENERATING state, exit gracefully

            draft_path = str(draft_path)
            print(f"[orchestrator] draft found: {draft_path}", file=sys.stderr)

            # Step 2: Prepare image prompts from draft
            result = run_script("gems_prepare.py", [
                "--draft", draft_path,
                "--output-dir", images_dir,
            ])
            # Non-fatal: image prompts are optional

            # Step 3: Generate images (requires AI_STUDIO_API_KEY)
            if cfg.get("ai_studio_api_key"):
                result = run_script("gems_generate.py", [
                    "--prompts-dir", images_dir,
                    "--output-dir", images_dir,
                ])
                if result.returncode != 0:
                    print("[orchestrator] gems_generate failed (non-fatal)",
                          file=sys.stderr)

            # Step 4: Quality check
            result = run_script("quality_check.py", [
                "--draft", draft_path,
                "--output", review_path,
            ])
            if result.returncode != 0:
                raise RuntimeError(f"quality_check failed: {result.stderr[-200:]}")

            # Load review for notification
            with open(review_path, "r", encoding="utf-8") as f:
                review_data = json.load(f)

            with open(selected_path, "r", encoding="utf-8") as f:
                selected_topic = json.load(f)
            title = selected_topic.get("title", "")

            # Send review report
            pstate["state"] = "NOTIFY_REVIEW"
            save_state(cfg, date_str, pstate)

            if notifier:
                msg = format_review_report(title, review_data)
                notifier.send_md(msg)

            pstate["state"] = "AWAITING_APPROVAL"
            pstate["approval_sent_at"] = datetime.now().isoformat()
            save_state(cfg, date_str, pstate)
            current = "AWAITING_APPROVAL"

        # ── Wait for approval ──
        if current == "AWAITING_APPROVAL":
            print("[orchestrator] waiting for review approval...", file=sys.stderr)
            reply = wait_for_reply(cfg["review_approval_timeout"])

            if reply and reply.lower().strip() != "ok":
                # Regenerate with correction
                print(f"[orchestrator] correction received: {reply[:80]}", file=sys.stderr)
                pstate["correction"] = reply
                pstate["state"] = "GENERATING"
                save_state(cfg, date_str, pstate)
                # Re-run pipeline from GENERATING state
                # (recursive call will pick up GENERATING state)
                run_pipeline(date_str)
                return
            else:
                if not reply:
                    print("[orchestrator] timeout, auto-approving", file=sys.stderr)

            # Finalize: copy draft to 07_final.md
            is_digest_mode = pstate.get("mode") == "digest"
            if is_digest_mode:
                draft_path = pdir / "04_digest.md"
            else:
                draft_path = pdir / "04_draft.md"
            final_path = pdir / "07_final.md"
            if draft_path.exists():
                final_path.write_text(
                    draft_path.read_text(encoding="utf-8"),
                    encoding="utf-8",
                )

            pstate["state"] = "PUBLISHING"
            pstate.pop("correction", None)
            save_state(cfg, date_str, pstate)
            current = "PUBLISHING"

        # ── Phase 3: Publish ──
        if current == "PUBLISHING":
            print("[orchestrator] === Phase 3: Publish ===", file=sys.stderr)

            is_digest_mode = pstate.get("mode") == "digest"
            final_path = str(pdir / "07_final.md")
            images_dir = str(pdir / ("05_images_digest" if is_digest_mode else "05_images"))
            media_ids_path = str(pdir / images_dir.split("/")[-1] / "media_ids.json")
            draft_id_path = str(pdir / "08_wechat_draft_id.json")

            # Validate WeChat config
            mp_missing = validate_mp_config(cfg)
            if mp_missing:
                raise RuntimeError(
                    f"WeChat config missing: {', '.join(wechat_missing)}"
                )

            # Upload images
            if Path(images_dir).exists() and list(Path(images_dir).glob("*.png")):
                result = run_script("wechat_upload_image.py", [
                    "--image-dir", images_dir,
                    "--output", media_ids_path,
                ])
                if result.returncode != 0:
                    print("[orchestrator] image upload failed (non-fatal)",
                          file=sys.stderr)

            # Create draft
            result = run_script("wechat_draft.py", [
                "--final", final_path,
                "--images", media_ids_path if Path(media_ids_path).exists() else "",
                "--output", draft_id_path,
            ])
            if result.returncode != 0:
                raise RuntimeError(f"wechat_draft failed: {result.stderr[-200:]}")

            # Load title for notification
            title = "每日AI速递" if is_digest_mode else ""
            selected_path = pdir / "02_topic_selected.json"
            if not is_digest_mode and selected_path.exists():
                with open(str(selected_path), "r", encoding="utf-8") as f:
                    selected = json.load(f)
                title = selected.get("title", "")
            elif is_digest_mode:
                # Try to extract title from the digest draft
                final_md = pdir / "07_final.md"
                if final_md.exists():
                    for line in final_md.read_text(encoding="utf-8").split("\n"):
                        if line.startswith("# ") and "每日AI速递" in line:
                            title = line[2:].strip()
                            break

            # Send publish notification
            pstate["state"] = "NOTIFY_DRAFT"
            save_state(cfg, date_str, pstate)

            if notifier:
                notifier.send_md(format_publish_notify(title))

            # Update content log
            _update_content_log(cfg, date_str, pdir)

            pstate["state"] = "DONE"
            pstate["completed_at"] = datetime.now().isoformat()
            save_state(cfg, date_str, pstate)
            print(f"[orchestrator] === Pipeline DONE ({date_str}) ===", file=sys.stderr)

    except Exception as e:
        error_msg = str(e)
        print(f"[orchestrator] ERROR: {error_msg}", file=sys.stderr)
        pstate["state"] = "ERROR"
        pstate["error"] = error_msg
        pstate["error_at"] = datetime.now().isoformat()
        save_state(cfg, date_str, pstate)

        if notifier:
            try:
                notifier.send_error(pstate.get("state", "UNKNOWN"), error_msg)
            except Exception:
                pass  # Don't mask the original error


def _update_content_log(cfg: dict, date_str: str, pdir: Path):
    """Append an entry to content_log.md."""
    log_path = Path(cfg["content_log_path"])

    # Load info
    title = ""
    draft_id = ""
    mode = "depth"

    # Check mode from state file
    state_file_path = pdir / "pipeline_state.json"
    if state_file_path.exists():
        try:
            state_data = json.loads(state_file_path.read_text(encoding="utf-8"))
            if state_data.get("mode") == "digest":
                mode = "digest"
        except Exception:
            pass

    if mode == "digest":
        # Extract title from final draft
        final_md = pdir / "07_final.md"
        if final_md.exists():
            for line in final_md.read_text(encoding="utf-8").split("\n"):
                if line.startswith("# ") and "每日AI速递" in line:
                    title = line[2:].strip()
                    break
        if not title:
            title = "每日AI速递"
    else:
        selected_path = pdir / "02_topic_selected.json"
        if selected_path.exists():
            with open(selected_path, "r", encoding="utf-8") as f:
                selected = json.load(f)
            title = selected.get("title", "")

    draft_id_path = pdir / "08_wechat_draft_id.json"
    if draft_id_path.exists():
        with open(draft_id_path, "r", encoding="utf-8") as f:
            draft_data = json.load(f)
        draft_id = draft_data.get("draft_id", "")

    mode_label = "速递" if mode == "digest" else "深度"
    entry = (
        f"\n## {date_str}\n"
        f"- **标题**: {title}\n"
        f"- **状态**: 草稿已创建\n"
        f"- **草稿 ID**: {draft_id}\n"
        f"- **模式**: {mode_label}\n"
        f"- **流水线**: Docker 自动化\n"
    )

    if log_path.exists():
        content = log_path.read_text(encoding="utf-8")
    else:
        content = "# Content Log\n"

    content += entry
    log_path.write_text(content, encoding="utf-8")
    print(f"[orchestrator] content log updated: {log_path}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="Pipeline orchestrator")
    parser.add_argument(
        "--date", default=None,
        help="Pipeline date (YYYY-MM-DD), defaults to today"
    )
    args = parser.parse_args()
    run_pipeline(args.date)


if __name__ == "__main__":
    main()
