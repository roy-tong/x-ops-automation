#!/usr/bin/env python3
"""X 点赞 agent — 对指定推文点赞（精准互动，构建兴趣图谱）。

用法:
  python3 like_x.py <tweet-url> [<tweet-url> ...]
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
CDP_URL = os.environ.get("X_CDP_URL", "http://127.0.0.1:9222")
LOG_DIR = BASE / "logs"


def log(msg: str, level: str = "INFO") -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {level}: {msg}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="X 点赞 agent")
    parser.add_argument("urls", nargs="+")
    args = parser.parse_args()
    if len(args.urls) > 10:
        log("单次最多 10 个")
        return 2

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log("需要 playwright")
        return 2

    records = []
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP_URL)
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.new_page()
        for url in args.urls:
            rec = {"url": url}
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(4000)
                if "x.com/login" in page.url:
                    log("未登录")
                    page.close()
                    return 3
                like = page.locator('[data-testid="like"]').first
                unlike = page.locator('[data-testid="unlike"]').first
                if unlike.is_visible(timeout=1500):
                    rec["already_liked"] = True
                    log(f"已赞过: {url[-30:]}")
                else:
                    like.click(timeout=6000)
                    time.sleep(1.2)
                    rec["liked"] = True
                    log(f"已点赞: {url[-30:]}")
            except Exception as exc:
                rec["error"] = str(exc)[:150]
                log(f"失败: {url[-30:]} -> {str(exc)[:80]}", "ERROR")
            finally:
                records.append(rec)
            time.sleep(6)
        page.close()

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    name = datetime.now().strftime("like-%Y%m%d-%H%M%S.json")
    (LOG_DIR / name).write_text(json.dumps({"records": records}, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"结果已写入 {LOG_DIR / name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
