# -*- coding: utf-8 -*-
"""Daily report auto-publisher: detects and transforms Odaily 24H daily report.

Independent scheduler that runs after 9:00 AM, checks every 5 minutes for the
daily report article from Odaily, transforms it, and publishes + broadcasts.
Does not interfere with the existing AutoPublishScheduler.
"""

from __future__ import annotations

import logging
import re
import threading
from datetime import datetime, timedelta

log = logging.getLogger("pipeline")

# Title pattern for Odaily daily report articles
DAILY_REPORT_TITLE_PATTERN = re.compile(r"24H热门币种与要闻")
DAILY_REPORT_START_HOUR = 9
DAILY_REPORT_CHECK_INTERVAL = 300  # seconds (5 minutes)
DAILY_REPORT_SLEEP_UNTIL_NEXT_DAY = True

SETTING_ENABLED = "daily_report_enabled"
SETTING_LAST_DATE = "daily_report_last_date"
SETTING_LAST_ARTICLE_ID = "daily_report_last_article_id"
SETTING_COVER_URL = "daily_report_cover_url"


class DailyReportScheduler:
    """Independent scheduler for Odaily daily report auto-publish."""

    def __init__(self, pipeline_service):
        self.pipeline_service = pipeline_service
        self.database = pipeline_service.database
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._status = {
            "enabled": True,
            "last_date": None,
            "last_article_id": None,
            "last_published_at": None,
            "last_error": None,
            "checking": False,
        }

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="daily-report-scheduler")
        self._thread.start()
        log.info("DailyReportScheduler started")

    def stop(self):
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)

    def is_enabled(self) -> bool:
        if not self.database:
            return False
        raw = (self.database.get_setting(SETTING_ENABLED) or "1").strip().lower()
        return raw not in {"0", "false", "off", "no"}

    def get_status(self) -> dict:
        status = dict(self._status)
        status["enabled"] = self.is_enabled()
        if self.database:
            status["last_date"] = self.database.get_setting(SETTING_LAST_DATE)
            status["last_article_id"] = self.database.get_setting(SETTING_LAST_ARTICLE_ID)
        return status

    def trigger_manual(self) -> dict:
        """Manually trigger a daily report check and publish."""
        return self._check_and_publish()

    def toggle(self, enabled: bool) -> dict:
        if self.database:
            self.database.set_setting(SETTING_ENABLED, "1" if enabled else "0")
        self._status["enabled"] = enabled
        return {"ok": True, "enabled": enabled}

    # -- Core logic --

    def _check_and_publish(self) -> dict:
        """Check for today's daily report and publish if found."""
        if not self.database:
            return {"ok": False, "reason": "no_database"}

        today = datetime.now().strftime("%Y-%m-%d")
        last_date = self.database.get_setting(SETTING_LAST_DATE) or ""
        if last_date == today:
            return {"ok": True, "reason": "already_published_today", "date": today}

        self._status["checking"] = True
        try:
            return self._do_check(today)
        except Exception as exc:
            log.error("DailyReportScheduler check failed: %s", exc)
            self._status["last_error"] = str(exc)
            return {"ok": False, "reason": "error", "error": str(exc)}
        finally:
            self._status["checking"] = False

    def _do_check(self, today: str) -> dict:
        scraper = self.pipeline_service.scrapers.get("odaily")
        if not scraper:
            return {"ok": False, "reason": "no_odaily_scraper"}

        # Fetch article list — now includes title and publishTimestamp
        items = scraper.parse_list()
        daily_item = None
        today_str = datetime.now().strftime("%Y-%m-%d")

        for item in items:
            # Pre-filter by title from list (no need to fetch detail)
            title = item.get("title", "")
            if not DAILY_REPORT_TITLE_PATTERN.search(title):
                continue

            # Validate date: only accept articles published today
            if not self._is_published_today(item, today_str):
                log.info(
                    "DailyReport: skip %s — title matches but not today's (title=%s)",
                    item.get("article_id", ""), title[:60],
                )
                continue

            # Today's daily report found — fetch full detail
            try:
                detail = scraper.fetch_detail(item)
                # Double-check title after detail fetch
                if DAILY_REPORT_TITLE_PATTERN.search(detail.get("title", "")):
                    daily_item = detail
                    break
            except Exception as e:
                log.warning("DailyReport: detail fetch failed for %s: %s", item.get("article_id", ""), e)
                continue

        if not daily_item:
            return {"ok": True, "reason": "not_found_yet", "date": today}

        # Transform the article
        transformed = self._transform_daily_report(daily_item, today)

        # Ensure cover image is uploaded to COS
        cover_url = self._ensure_cover_uploaded()
        if cover_url:
            transformed["cover_src"] = cover_url

        # Store in database first
        transformed["source_key"] = "odaily"
        article_id = self.pipeline_service._canonical_article_id("odaily", transformed)
        transformed["article_id"] = article_id

        # Insert/update in database
        self.database.insert_or_update(transformed)

        # Generate abstract via LLM
        is_refusal_abstract = False
        try:
            from services.llm import detect_refusal_abstract, generate_abstract
            ai_abstract = generate_abstract(transformed, self.database)
            if ai_abstract:
                is_refusal_abstract, matched_keyword = detect_refusal_abstract(ai_abstract)
                if is_refusal_abstract:
                    transformed["abstract"] = ""
                    self.database.update_abstract(article_id, "")
                    log.warning(
                        "DailyReport: refusal-like abstract detected, downgraded to empty (aid=%s, hit=%s)",
                        article_id,
                        matched_keyword,
                    )
                else:
                    transformed["abstract"] = ai_abstract
                    self.database.update_abstract(article_id, ai_abstract)
                    log.info("DailyReport: abstract generated (%d chars)", len(ai_abstract))
        except Exception as exc:
            log.warning("DailyReport: abstract generation failed: %s", exc)

        # Check if article already has a CMS draft — reuse existing cms_id
        existing = self.database.get_by_article_id(article_id) if self.database else None
        if existing and existing.get("cms_id"):
            transformed["cms_id"] = existing["cms_id"]
            transformed["publish_stage"] = existing.get("publish_stage", "draft")

        # Publish flow:
        # - refusal-style abstract -> draft only (no public publish, no push)
        # - normal abstract        -> publish + broadcast
        try:
            if is_refusal_abstract:
                result = self.pipeline_service.save_article_draft(transformed, strategy="daily_report")
                publish_reason = "drafted_due_to_refusal_abstract"
            else:
                push_label = transformed["title"]
                push_content = transformed.get("abstract", "") or transformed.get("title", "")
                result = self.pipeline_service.auto_publish_and_broadcast(
                    transformed,
                    push_label=push_label,
                    push_content=push_content,
                    window_start=datetime.now().replace(hour=DAILY_REPORT_START_HOUR, minute=0, second=0, microsecond=0),
                    strategy="daily_report",
                    record_window_quota=False,
                )
                publish_reason = "published"
            cms_id = result.get("cms_id", "")
            if is_refusal_abstract:
                log.info(
                    "DailyReportScheduler drafted due to refusal abstract: %s (cms_id=%s, title=%s)",
                    article_id, cms_id, transformed["title"],
                )
            else:
                log.info(
                    "DailyReportScheduler published: %s (cms_id=%s, title=%s)",
                    article_id, cms_id, transformed["title"],
                )
        except Exception as exc:
            log.error("DailyReportScheduler publish failed: %s", exc)
            self._status["last_error"] = str(exc)
            return {"ok": False, "reason": "publish_failed", "error": str(exc)}

        # Record success
        self.database.set_setting(SETTING_LAST_DATE, today)
        self.database.set_setting(SETTING_LAST_ARTICLE_ID, article_id)
        self._status["last_date"] = today
        self._status["last_article_id"] = article_id
        self._status["last_published_at"] = datetime.now().isoformat()
        self._status["last_error"] = None

        return {
            "ok": True,
            "reason": publish_reason,
            "date": today,
            "article_id": article_id,
            "cms_id": cms_id,
            "title": transformed["title"],
        }

    def _transform_daily_report(self, article: dict, today: str) -> dict:
        """Transform the raw daily report into ChainThink format."""
        blocks = article.get("blocks", [])

        # Find "头条" block index
        toutiao_idx = None
        for i, block in enumerate(blocks):
            text = block.get("text", "").strip()
            if "头条" in text:
                toutiao_idx = i
                break

        if toutiao_idx is not None:
            # Remove everything before "头条"
            blocks = blocks[toutiao_idx:]
        else:
            log.warning("DailyReport: '头条' not found in blocks, keeping all content")

        # Remove trailing author/source blocks
        blocks = self._strip_trailing_author_source(blocks)

        # Generate title: ChainThink4.27早报
        now = datetime.now()
        new_title = f"ChainThink{now.month}.{now.day}早报"

        return {
            **article,
            "title": new_title,
            "author": "",
            "source": "",
            "blocks": blocks,
            "cover_src": "",  # Fixed cover set by _ensure_cover_uploaded()
            "abstract": "",  # Will be generated by LLM after transform
            "user_id": "6",
            "as_user_id": "6",
            "strong_content_tags": {"人工": ["加密早报"]},
        }

    @staticmethod
    def _strip_trailing_author_source(blocks: list[dict]) -> list[dict]:
        """Remove trailing blocks that look like author/source attribution."""
        if not blocks:
            return blocks

        # Patterns for author/source lines at the end
        patterns = [
            re.compile(r"^作者[：:]", re.IGNORECASE),
            re.compile(r"^来源[：:]", re.IGNORECASE),
            re.compile(r"^编辑[：:]", re.IGNORECASE),
            re.compile(r"^撰文[：:]", re.IGNORECASE),
            re.compile(r"^编译[：:]", re.IGNORECASE),
            re.compile(r"Odaily星球日报"),
            re.compile(r"原文链接"),
            re.compile(r"原文作者"),
        ]

        # Scan from end
        cut_idx = len(blocks)
        for i in range(len(blocks) - 1, -1, -1):
            block = blocks[i]
            text = block.get("text", "").strip()
            if not text:
                cut_idx = i
                continue
            is_attribution = any(p.search(text) for p in patterns)
            if is_attribution:
                cut_idx = i
            else:
                break

        return blocks[:cut_idx]

    @staticmethod
    def _is_published_today(item: dict, today_str: str) -> bool:
        """Check if an article was published today using publishTimestamp or title date."""
        # Method 1: publishTimestamp from API (milliseconds)
        ts = item.get("publish_timestamp")
        if ts:
            try:
                pub_date = datetime.fromtimestamp(int(ts) / 1000).strftime("%Y-%m-%d")
                return pub_date == today_str
            except (ValueError, TypeError, OSError):
                pass

        # Method 2: parse date from title, e.g. "24H热门币种与要闻｜...（4月27日）"
        title = item.get("title", "")
        m = re.search(r"(\d{1,2})月(\d{1,2})日", title)
        if m:
            try:
                month, day = int(m.group(1)), int(m.group(2))
                now = datetime.now()
                parsed = now.replace(month=month, day=day)
                return parsed.strftime("%Y-%m-%d") == today_str
            except ValueError:
                pass

        # Method 3: parse "M.D" format from title, e.g. "4.27 24H热门币种与要闻"
        m = re.search(r"(\d{1,2})\.(\d{1,2})", title)
        if m:
            try:
                month, day = int(m.group(1)), int(m.group(2))
                now = datetime.now()
                parsed = now.replace(month=month, day=day)
                return parsed.strftime("%Y-%m-%d") == today_str
            except ValueError:
                pass

        # No date info available — reject to be safe
        log.warning("DailyReport: cannot determine date for article %s, rejecting", item.get("article_id", ""))
        return False

    def _ensure_cover_uploaded(self) -> str:
        """Upload the daily report cover image to COS if not already done."""
        if not self.database:
            return ""

        # Check cached URL
        cached = self.database.get_setting(SETTING_COVER_URL)
        if cached:
            return cached

        # Upload from local file
        cover_path = self.pipeline_service.base_dir / "data" / "daily_report_cover.png"
        if not cover_path.exists():
            log.warning("DailyReport: cover image not found at %s", cover_path)
            return ""

        try:
            cos = self.pipeline_service.publisher.cos
            cos_url = cos.upload_cover_from_file(str(cover_path))
            if cos_url:
                self.database.set_setting(SETTING_COVER_URL, cos_url)
                log.info("DailyReport: cover uploaded to COS: %s", cos_url)
            return cos_url or ""
        except Exception as exc:
            log.error("DailyReport: cover upload failed: %s", exc)
            return ""

    # -- Scheduler loop --

    def _loop(self):
        while not self._stop_event.is_set():
            now = datetime.now()

            # Check if enabled
            if not self.is_enabled():
                self._stop_event.wait(60)
                continue

            # Check if within active hours (9:00 - 14:00)
            if now.hour < DAILY_REPORT_START_HOUR:
                # Sleep until 9:00
                wait_seconds = (
                    now.replace(hour=DAILY_REPORT_START_HOUR, minute=0, second=0, microsecond=0)
                    - now
                ).total_seconds()
                self._stop_event.wait(max(1, min(wait_seconds, 300)))
                continue

            if now.hour >= 14:
                # Already past active window, sleep until next day 9:00
                next_9am = (now + timedelta(days=1)).replace(
                    hour=DAILY_REPORT_START_HOUR, minute=0, second=0, microsecond=0
                )
                wait_seconds = (next_9am - now).total_seconds()
                self._stop_event.wait(max(1, min(wait_seconds, 300)))
                continue

            # Check if already published today
            today = now.strftime("%Y-%m-%d")
            last_date = (self.database.get_setting(SETTING_LAST_DATE) or "") if self.database else ""
            if last_date == today:
                # Already published, sleep until next day
                next_9am = (now + timedelta(days=1)).replace(
                    hour=DAILY_REPORT_START_HOUR, minute=0, second=0, microsecond=0
                )
                wait_seconds = (next_9am - now).total_seconds()
                self._stop_event.wait(max(1, min(wait_seconds, 300)))
                continue

            # Try to check and publish
            try:
                result = self._check_and_publish()
                if result.get("reason") == "published":
                    log.info("DailyReportScheduler: published successfully, sleeping until tomorrow")
                elif result.get("reason") == "not_found_yet":
                    log.debug("DailyReportScheduler: daily report not found yet, will retry in 1 min")
            except Exception as exc:
                log.error("DailyReportScheduler loop error: %s", exc)

            # Wait 1 minute before next check
            self._stop_event.wait(DAILY_REPORT_CHECK_INTERVAL)
