#!/usr/bin/env python3
"""X 回复 agent — 在指定推文下贡献观点回复（可选点赞）。

用法:
  python3 reply_x.py --tweet-url https://x.com/<user>/status/<id> --text "..." [--like] [--dry-run]

前置: 同 publish_x.py（Chrome 调试端口 9222 + 已登录 X）。
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

SELECTORS = {
    "reply_button": '[data-testid="reply"]',
    "textarea": '[data-testid="tweetTextarea_0"]',
    "submit": '[data-testid="tweetButton"]',
    "like": '[data-testid="like"]',
    "unlike": '[data-testid="unlike"]',
}


def log(msg: str, level: str = "INFO") -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {level}: {msg}", flush=True)


def wait_posted(page, timeout=40) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            ta = page.locator(SELECTORS["textarea"]).first
            if ta.is_visible(timeout=1200):
                if not ta.inner_text().strip():
                    return True
            else:
                return True
        except Exception:
            return True
        time.sleep(1)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="X 回复 agent")
    parser.add_argument("--tweet-url", required=True, help="目标推文 URL")
    parser.add_argument("--text", required=True, help="回复内容")
    parser.add_argument("--like", action="store_true", help="回复后点赞原推")
    parser.add_argument("--dry-run", action="store_true", help="填好不发送")
    args = parser.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log("需要 playwright", "ERROR")
        return 2

    rec = {"url": args.tweet_url, "started_at": datetime.now().isoformat()}
    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(CDP_URL)
        except Exception as exc:
            log(f"无法连接 {CDP_URL}: {exc}", "ERROR")
            return 2
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.new_page()
        page.goto(args.tweet_url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(5000)

        # 登录态检查
        if "x.com/login" in page.url:
            log("未登录，中止", "ERROR")
            page.close()
            return 3

        # 打开回复框
        reply = page.locator(SELECTORS["reply_button"]).first
        reply.wait_for(state="visible", timeout=15000)
        reply.click()
        page.wait_for_timeout(2000)

        ta = page.locator(SELECTORS["textarea"]).first
        ta.wait_for(state="visible", timeout=10000)
        ta.click()
        time.sleep(0.4)
        ta.press_sequentially(args.text, delay=12, timeout=60000)
        time.sleep(1.0)
        got = ta.inner_text().strip()
        rec["entered"] = len(got)
        # X 会把 \n\n 渲染为 \n\n\n，归一化后比较
        import re
        norm = lambda s: re.sub(r"\n+", "\n", s)
        if norm(got) != norm(args.text.strip()):
            log(f"输入校验不一致: {len(got)}/{len(args.text)}，中止（防止发错内容）", "ERROR")
            page.close()
            return 4

        if args.dry_run:
            shot = LOG_DIR / f"reply-dryrun-{int(time.time())}.png"
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(shot))
            log(f"dry-run 已填好（截图 {shot.name}）")
            rec["dry_run"] = True
            page.close()
            (LOG_DIR / f"reply-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json").write_text(
                json.dumps({"record": rec}, ensure_ascii=False, indent=2), encoding="utf-8")
            return 0

        btn = page.locator(SELECTORS["submit"]).first
        btn.wait_for(state="visible", timeout=8000)
        deadline = time.time() + 15
        while time.time() < deadline:
            if btn.is_enabled():
                break
            time.sleep(0.5)
        if not btn.is_enabled():
            log("回复按钮保持 disabled（可能超字符限制），中止", "ERROR")
            page.close()
            return 5
        btn.click()
        ok = wait_posted(page)
        rec.update({"submitted": True, "posted": ok})
        log("回复已发布" if ok else "未检测到发布成功", "INFO" if ok else "WARN")

        if args.like and ok:
            page.wait_for_timeout(2000)
            like_btn = page.locator(SELECTORS["like"]).first
            try:
                like_btn.wait_for(state="visible", timeout=5000)
                like_btn.click()
                page.wait_for_timeout(1000)
                rec["liked"] = True
                log("已点赞原推")
            except Exception:
                log("点赞按钮未找到（可能已赞过）", "WARN")

        page.close()
    rec["finished_at"] = datetime.now().isoformat()
    (LOG_DIR / f"reply-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json").write_text(
        json.dumps({"record": rec}, ensure_ascii=False, indent=2), encoding="utf-8")
    log("完成。")
    return 0 if rec.get("posted") else 5


if __name__ == "__main__":
    sys.exit(main())
