#!/usr/bin/env python3
"""X 关注 agent — 批量关注目标账号（建立兴趣图谱）。

用法:
  python3 follow_x.py @modelcontextprotocol @langchainai @swyxio

纪律: 每天 ≤10 个新关注；只关注真正相关且持续输出的账号。
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


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="X 关注 agent")
    parser.add_argument("handles", nargs="+", help="要关注的 handle（@xxx）")
    args = parser.parse_args()
    if len(args.handles) > 10:
        log("单次最多关注 10 个（X 行为风控 + 纪律）", "ERROR")
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
        for h in args.handles:
            rec = {"handle": h, "started_at": datetime.now().isoformat()}
            try:
                page.goto(f"https://x.com/{h.lstrip('@')}", wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(4500)
                if "x.com/login" in page.url:
                    log("未登录", "ERROR")
                    page.close()
                    return 3
                # 关注按钮：动态 testid 为 {user-id}-follow（语言无关）
                follow = page.locator('button[data-testid$="-follow"]').first
                try:
                    follow.wait_for(state="visible", timeout=5000)
                    follow.click()
                    page.wait_for_timeout(1500)
                    rec["followed"] = True
                    log(f"已关注 {h}")
                except Exception:
                    rec["followed"] = False
                    rec["note"] = "已关注或按钮未找到"
                    log(f"{h}: 已关注或按钮未找到，跳过")
            except Exception as exc:
                rec["error"] = str(exc)[:200]
                log(f"{h}: 失败 {str(exc)[:120]}", "ERROR")
            finally:
                rec["finished_at"] = datetime.now().isoformat()
                records.append(rec)
            time.sleep(8)  # 人类节奏
        page.close()

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    name = datetime.now().strftime("follow-%Y%m%d-%H%M%S.json")
    (LOG_DIR / name).write_text(json.dumps({"records": records}, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"结果已写入 {LOG_DIR / name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
