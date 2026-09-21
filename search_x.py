#!/usr/bin/env python3
"""X 搜索 agent — 在 X 上搜索实时讨论，输出候选推文（作者/摘要/URL），供回复挑选。

用法:
  python3 search_x.py "MCP monetization" [--limit 8] [--max-pages 1]
时间范围不需要单独参数——直接把 since:/until: 操作符写进 query 即可，如:
  python3 search_x.py "agent observability since:2026-08-18 until:2026-08-19"
"""
import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
CDP_URL = os.environ.get("X_CDP_URL", "http://127.0.0.1:9222")


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="X 搜索 agent")
    parser.add_argument("query", help="搜索词")
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--max-pages", type=int, default=1,
                        help="滚动翻页次数（默认 1=只取首屏；>1 会向下滚动加载，按推文 URL 去重）")
    args = parser.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log("需要 playwright")
        return 2

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP_URL)
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.new_page()
        q = args.query.replace(" ", "%20")
        page.goto(f"https://x.com/search?q={q}&src=typed_query&f=live", wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(7000)
        if "x.com/login" in page.url:
            log("未登录")
            page.close()
            return 3

        # 每条推：article[data-testid=tweet] 结构；max-pages>1 时滚动加载并按 URL 去重
        seen = set()
        out = []
        for _page_i in range(max(1, args.max_pages)):
            if _page_i:
                page.mouse.wheel(0, 4000)
                page.wait_for_timeout(2500)
            tweets = page.locator('article[data-testid="tweet"]').all()
            for t in tweets:
                if len(out) >= args.limit:
                    break
                try:
                    link = t.locator('a[href*="/status/"]').first.get_attribute("href")
                    if link and link in seen:
                        continue
                    if link:
                        seen.add(link)
                    text = t.locator('[data-testid="tweetText"]').first.inner_text()[:160].replace("\n", " ")
                    author = t.locator('[data-testid="User-Name"]').first.inner_text()[:40].replace("\n", " ")
                    out.append({"author": author, "text": text, "url": "https://x.com" + link if link else ""})
                except Exception:
                    continue
            if len(out) >= args.limit:
                break
        for i, tw in enumerate(out, 1):
            print(f"\n[{i}] {tw['author']}")
            print(f"    {tw['text']}")
            print(f"    {tw['url']}")
        page.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
