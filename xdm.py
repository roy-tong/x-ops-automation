#!/usr/bin/env python3
"""X DM 发送器 — CDP 驱动已登录的 X 会话发送私信。

流程: /i/chat → New message → 慢速输入用户名 → 点击精确 handle 的建议行
      → 写信框输入消息 → 发送（若出现密码确认：输入 $X_DM_PASSCODE + Confirm）

纪律（bad case 教训）：
  - 消息禁止 em dash / en dash（脚本强制断言）
  - 人味、短、引用对方真实工作、单一问题
  - 限速 75-150s；dry-run 只写信不发送

用法:
  python3 xdm.py --dry-run            # 只写信 + 截图，不发送
  python3 xdm.py                      # 正式发送（读取 xdm-queue.json）
  python3 xdm.py --only <handle>
"""
import argparse
import json
import os
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
OUTREACH = Path(os.environ.get("X_OPS_OUTREACH_DIR", str(Path.home() / ".x-ops-automation" / "outreach")))
QUEUE = OUTREACH / "xdm-queue.json"
LOG = OUTREACH / "xdm-log.jsonl"
SHOT_DIR = OUTREACH / "shots"
CDP_URL = os.environ.get("X_CDP_URL", "http://127.0.0.1:9222")
PASSCODE = os.environ.get("X_DM_PASSCODE", "")

FORBIDDEN = ["\u2014", "\u2013"]  # em dash, en dash


INBOX_CLOSED_PATTERNS = [
    # 英文
    r"not accepting messages", r"isn'?t accepting messages", r"doesn'?t accept messages",
    r"can'?t be messaged", r"cannot be messaged", r"is not accepting", r"aren'?t accepting",
    r"doesn'?t accept direct messages", r"message settings", r"not receiving messages",
    r"unable to receive messages", r"messages are off",
    # 中文
    r"未开启私信", r"无法接收消息", r"不接受消息", r"不接收私信", r"未开启消息",
    r"关闭了私信", r"暂不接收消息", r"未开通私信", r"不开放私信",
    # 日文/其他
    r"メッセージを受け付けていません", r"ダイレクトメッセージを受け付けていません",
]


def inbox_closed_reason(page):
    """读取 X 系统反馈：识别收件人 inbox 关闭（不接受 DM）的提示。返回提示片段或 None。"""
    try:
        texts = []
        for sel in ["body", '[data-testid="DmConversationHeader"]', '[data-testid="dmConversationHeader"]',
                    '[data-testid="toast"]', '#layers']:
            try:
                el = page.locator(sel).first
                if el.count() and el.is_visible(timeout=800):
                    texts.append(el.inner_text(timeout=3000))
            except Exception:
                continue
        blob = "\n".join(texts)
        for pat in INBOX_CLOSED_PATTERNS:
            m = re.search(pat, blob, re.I)
            if m:
                snippet = blob[max(0, m.start() - 60): m.end() + 60].replace("\n", " ")
                return snippet[:160]
    except Exception:
        pass
    return None


def log(msg: str, level: str = "INFO") -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {level}: {msg}", flush=True)


def open_chat(page):
    page.goto("https://x.com/i/chat", timeout=45000)
    time.sleep(6)
    page.locator('[data-testid="dm-new-chat-button"]').first.wait_for(state="visible", timeout=15000)
    page.locator('[data-testid="dm-new-chat-button"]').first.click()
    time.sleep(2)
    page.locator('input[placeholder="Search name or username"]').first.wait_for(state="visible", timeout=10000)


def _type_slow(box, text: str):
    box.click()
    for ch in text:
        box.type(ch)
        time.sleep(0.15)


def search_user(page, handle: str, name: str = "") -> bool:
    box = page.locator('input[placeholder="Search name or username"]').first
    # 清空历史输入
    try:
        box.fill("")
    except Exception:
        pass
    _type_slow(box, handle)
    time.sleep(4)
    rows = page.locator('[data-testid^="new-dm-user-suggestion-"]').all()
    for row in rows:
        try:
            txt = row.inner_text()
            if f"@{handle}" in txt:
                row.click()
                return True
        except Exception:
            pass
    # 兜底：按显示名搜索（名字片段匹配）
    if name:
        try:
            box.fill("")
        except Exception:
            pass
        _type_slow(box, name)
        time.sleep(4)
        rows = page.locator('[data-testid^="new-dm-user-suggestion-"]').all()
        for row in rows:
            try:
                txt = row.inner_text()
                if name.split()[-1] in txt and f"@{handle}" in txt:
                    row.click()
                    return True
            except Exception:
                pass
    return False


def compose(page, message: str) -> bool:
    # 新 chat UI 的写信框是 textarea（placeholder=Unencrypted message）
    tas = [e for e in page.locator("textarea").all() if e.is_visible()]
    if not tas:
        return False
    ed = tas[0]
    ed.click()
    for ch in message:
        ed.type(ch)
        time.sleep(0.008)
    time.sleep(1)
    return True


def verify_recipient(page, handle: str) -> bool:
    """安全校验：会话面板头部必须包含目标 handle，防止发错人。"""
    try:
        ta = [e for e in page.locator("textarea").all() if e.is_visible()]
        if not ta:
            return False
        node = ta[0]
        for _ in range(10):
            node = node.locator("xpath=..")
        panel = node.inner_text() or ""
        return f"@{handle}" in panel
    except Exception:
        return False


def find_send_button(page):
    for sel in ['[data-testid="dm-send-button"]', '[data-testid^="dm-send"]',
                '[role="button"][aria-label="Send"]', '[aria-label*="Send"]']:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=1500):
                return el
        except Exception:
            pass
    return None


def handle_passcode(page) -> bool:
    """发送后若出现密码确认弹窗：输入 X_DM_PASSCODE 指定的密码 + Confirm。返回是否处理过。"""
    if not PASSCODE:
        return False
    try:
        pw = page.locator('input[type="password"]').first
        if pw.is_visible(timeout=2500):
            pw.fill(PASSCODE)
            time.sleep(0.5)
            for sel in ['[role="button"]:has-text("Confirm")', '[role="button"]:has-text("确认")',
                        '[data-testid*="confirm"]', 'form button[type="submit"]']:
                try:
                    el = page.locator(sel).first
                    if el.is_visible(timeout=1500):
                        el.click()
                        return True
                except Exception:
                    pass
            # 兜底：回车
            pw.press("Enter")
            return True
    except Exception:
        pass
    return False


def wait_sent(page, timeout=15) -> bool:
    """写信框清空 = 已发送（textarea 为空即消息已送出）。"""
    try:
        for _ in range(timeout):
            tas = [e for e in page.locator("textarea").all() if e.is_visible()]
            if tas:
                if not tas[0].input_value().strip():
                    return True
            time.sleep(1)
    except Exception:
        pass
    return False


def reset_compose(page):
    try:
        page.keyboard.press("Escape")
        time.sleep(1)
        page.keyboard.press("Escape")
    except Exception:
        pass
    page.goto("https://x.com/i/chat", timeout=45000)
    time.sleep(5)


def send_one(page, rec: dict, dry_run: bool) -> dict:
    out = {"id": rec["id"], "handle": rec["handle"], "name": rec.get("name", "")}
    try:
        open_chat(page)
        if not search_user(page, rec["handle"], rec.get("name", "")):
            # 搜索不到也可能是因为 inbox 关闭 → 读取反馈
            reason = inbox_closed_reason(page)
            if reason:
                out.update({"result": "no-dm", "diag": reason})
            else:
                out.update({"result": "handle-not-found"})
            reset_compose(page)
            return out
        # 选中用户后：先检测 inbox 是否关闭（系统反馈）
        time.sleep(1.5)
        reason = inbox_closed_reason(page)
        if reason:
            out.update({"result": "no-dm", "diag": reason})
            reset_compose(page)
            return out
        composed = compose(page, rec["message"])
        if not composed:
            # 诊断：抓面板文本判断 DM 是否关闭
            diag = ""
            try:
                tas = [e for e in page.locator("textarea").all() if e.is_visible()]
                if not tas:
                    node = page.locator('[data-testid^="new-dm-user-suggestion-"]').first
                    diag = "no-composer"
                else:
                    diag = "textarea-present"
            except Exception:
                diag = "unknown"
            # inbox 关闭检测（compose 失败时页面可能展示提示）
            reason = inbox_closed_reason(page)
            if reason:
                out.update({"result": "no-dm", "diag": reason})
                reset_compose(page)
                return out
            # 重试一次（瞬态失败）
            if diag != "textarea-present":
                reset_compose(page)
                try:
                    open_chat(page)
                    search_user(page, rec["handle"], rec.get("name", ""))
                    composed = compose(page, rec["message"])
                except Exception:
                    composed = False
            if not composed:
                reason = inbox_closed_reason(page)
                if reason:
                    out.update({"result": "no-dm", "diag": reason})
                else:
                    out.update({"result": "compose-failed", "diag": diag})
                reset_compose(page)
                return out
        if not verify_recipient(page, rec["handle"]):
            reason = inbox_closed_reason(page)
            if reason:
                out.update({"result": "no-dm", "diag": reason})
            else:
                out.update({"result": "recipient-mismatch"})
            reset_compose(page)
            return out
        SHOT_DIR.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(SHOT_DIR / f"xdm-{rec['handle']}.png"))
        if dry_run:
            out.update({"result": "dry-run-draft"})
            reset_compose(page)
            return out
        btn = find_send_button(page)
        if btn:
            btn.click()
        else:
            page.keyboard.press("Enter")  # 新 chat UI：Enter 发送
        time.sleep(2)
        handle_passcode(page)
        ok = wait_sent(page)
        out.update({"result": "sent" if ok else "send-unconfirmed", "sent_at": datetime.now().isoformat()})
        reset_compose(page)
        return out
    except Exception as exc:
        out.update({"result": "error", "error": str(exc)[:300]})
        try:
            reset_compose(page)
        except Exception:
            pass
        return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--only", help="只发指定 handle")
    parser.add_argument("--interval", type=int, help="固定间隔秒数（默认 75–150 随机）")
    args = parser.parse_args()

    queue = json.loads(QUEUE.read_text(encoding="utf-8"))
    if args.only:
        queue = [q for q in queue if q["handle"] == args.only]
    else:
        queue = [q for q in queue if not q.get("paused")]  # 跳过暂停项
    sent_ids = set()
    no_dm = set()
    if LOG.exists():
        for line in LOG.read_text(encoding="utf-8").splitlines():
            d = json.loads(line)
            if d.get("result") == "sent":
                sent_ids.add(d["handle"])
            elif d.get("result") == "no-dm":
                no_dm.add(d["handle"])
    queue = [q for q in queue if q["handle"] not in sent_ids]
    if not args.only:
        skipped = [q for q in queue if q["handle"] in no_dm]
        for q in skipped:
            log(f"跳过 {q['handle']}（inbox 关闭，已在 no-dm 名单）")
        queue = [q for q in queue if q["handle"] not in no_dm]

    # 消息风格断言：无 em/en dash
    for q in queue:
        for ch in FORBIDDEN:
            assert ch not in q["message"], f"{q['handle']}: message contains a dash!"

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log("需要 playwright", "ERROR")
        return 1

    log(f"X DM 队列 {len(queue)} 条（dry-run={'是' if args.dry_run else '否'}）")
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP_URL)
        page = browser.contexts[0].new_page()
        try:
            for idx, rec in enumerate(queue):
                log(f"[{rec['handle']}] 处理中…")
                result = send_one(page, rec, args.dry_run)
                with LOG.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(result, ensure_ascii=False) + "\n")
                log(f"[{rec['handle']}] {result['result']}")
                if idx < len(queue) - 1:
                    wait = args.interval or random.randint(75, 150)
                    log(f"等待 {wait}s…")
                    time.sleep(wait)
        finally:
            page.close()
    log("完成" + ("（dry-run，未发送任何私信）" if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
