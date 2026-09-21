#!/usr/bin/env python3
"""扫描 Gmail：列出 to:me 的邮件线程概要。只读。"""
import json
import os
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

CDP_URL = os.environ.get("X_CDP_URL", "http://127.0.0.1:9222")
OUT = Path(__file__).parent / "inbox_scan.json"
QUERY = "to:me after:2026/08/15 -from:me"


def ensure_windows(browser):
    cdp = browser.new_browser_cdp_session()
    for t in cdp.send("Target.getTargets")["targetInfos"]:
        if t["type"] == "page" and "mail.google.com" in (t.get("url") or ""):
            w = cdp.send("Browser.getWindowForTarget", {"targetId": t["targetId"]})
            b = w["bounds"]
            if b.get("windowState") == "normal" and (b["width"] < 1200 or b["height"] < 800):
                cdp.send("Browser.setWindowBounds", {"windowId": w["windowId"], "bounds": {"left": 20, "top": 40, "width": 1480, "height": 940}})


def main():
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP_URL)
        ctx = browser.contexts[0]
        ensure_windows(browser)
        page = next((pg for pg in ctx.pages if "mail.google.com" in pg.url), None) or ctx.new_page()
        page.bring_to_front()
        page.goto("https://mail.google.com/mail/u/0/#search/" + QUERY.replace(" ", "+"), timeout=45000)
        time.sleep(4)
        if "accounts.google.com" in page.url:
            print(json.dumps({"error": "not logged in"}))
            return 1
        all_rows = []
        stagnant = 0
        last = 0
        for _ in range(40):
            rows = page.evaluate("() => [...document.querySelectorAll('div[role=\"main\"] tr.zA')].map(r => r.innerText)")
            if len(rows) == last:
                stagnant += 1
            else:
                stagnant = 0
            last = len(rows)
            all_rows = rows
            if stagnant >= 2:
                break
            page.mouse.wheel(0, 4000)
            time.sleep(1.2)
        out = {"count": len(all_rows), "rows": all_rows}
        OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1))
        print(json.dumps({"count": len(all_rows), "out": str(OUT)}, ensure_ascii=False))


if __name__ == "__main__":
    sys.exit(main())
