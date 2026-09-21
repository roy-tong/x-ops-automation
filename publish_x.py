#!/usr/bin/env python3
"""X 发布 agent — 通过 CDP 驱动本机已登录的 Chrome 发布线程。

用法:
  python3 publish_x.py --dry-run       # 填好不发布，截图留证
  python3 publish_x.py                 # 正式发布（全部）
  python3 publish_x.py --start N       # 从第 N 条继续
  python3 publish_x.py --check         # 只检查登录态
  python3 publish_x.py --interval 60   # 固定间隔秒数（默认 45-90 随机）

前置: 双击 launch_x.command 重启 Chrome（带 --remote-debugging-port=9222），
并确认 x.com 已登录。脚本不保存任何凭据。
"""
import argparse
import json
import os
import random
import sys
import time
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
CDP_URL = os.environ.get("X_CDP_URL", "http://127.0.0.1:9222")
LOG_DIR = BASE / "logs"

SELECTORS = {
    "compose_button": [
        '[data-testid="SideNav_NewTweet_Button"]',
        '[data-testid="GlobalNewTweetButton"]',
    ],
    "textarea": '[data-testid="tweetTextarea_0"]',
    "submit": '[data-testid="tweetButton"]',
}


def log(msg: str, level: str = "INFO") -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {level}: {msg}", flush=True)


def write_result(records: list) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    name = datetime.now().strftime("publish-%Y%m%d-%H%M%S.json")
    (LOG_DIR / name).write_text(
        json.dumps({"records": records}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log(f"结果已写入 {LOG_DIR / name}")


def dismiss_confirm_sheet(page) -> None:
    """X 的确认弹层（如 Discard draft?）——点主确认按钮或再按 Escape。"""
    try:
        btn = page.locator('[data-testid="confirmationSheetConfirm"]').first
        if btn.is_visible(timeout=1500):
            btn.click()
            time.sleep(1)
            return
    except Exception:
        pass
    try:
        page.keyboard.press("Escape")
        time.sleep(0.8)
    except Exception:
        pass


def open_composer(page) -> bool:
    dismiss_confirm_sheet(page)
    for sel in SELECTORS["compose_button"]:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=5000):
                btn.click()
                return True
        except Exception:
            continue
    return False


def fill_text(page, text: str) -> bool:
    """输入推文并校验内容真正进入编辑器（X 未识别输入时 Post 按钮保持 disabled）。

    X 的编辑器是 contenteditable：用 press_sequentially 逐字符输入（事件完整），
    然后读取 inner_text 校验；失败最多重试 3 次。
    """
    ta = page.locator(SELECTORS["textarea"]).first
    ta.wait_for(state="visible", timeout=10000)
    for attempt in range(1, 4):
        try:
            ta.click()
            time.sleep(0.4)
            ta.press_sequentially(text, delay=12, timeout=60000)
            time.sleep(1.0)
            got = ta.inner_text().strip()
            # X 的 contenteditable 会把 \n\n 渲染为 \n\n\n（段落符），归一化后比较
            norm = lambda s: __import__("re").sub(r"\n+", "\n", s)
            if norm(got) == norm(text.strip()):
                return True
            log(f"输入校验不一致(第 {attempt} 次): 已输入 {len(got)}/{len(text)} 字符，清空重试", "WARN")
            # 清空再试（Ctrl+A 删除）
            ta.click()
            page.keyboard.press("Meta+A")
            page.keyboard.press("Backspace")
            time.sleep(0.6)
        except Exception as exc:
            log(f"输入异常(第 {attempt} 次): {str(exc)[:120]}", "WARN")
            time.sleep(1)
    return False


def submit(page) -> bool:
    """点 Post 按钮；若按钮 disabled（内容未识别）则抛错让上层处理。"""
    btn = page.locator(SELECTORS["submit"]).first
    btn.wait_for(state="visible", timeout=8000)
    deadline = time.time() + 15
    while time.time() < deadline:
        if btn.is_enabled():
            break
        time.sleep(0.5)
    if not btn.is_enabled():
        raise RuntimeError("Post 按钮保持 disabled（内容未被 X 识别）")
    btn.click()
    return True


DAILY_LIMIT_MARKERS = ["每日发帖上限", "daily posting limit", "posting limit", "发帖上限"]


def check_daily_limit(page) -> bool:
    """检测 X 每日发帖上限横幅（免费账号 ~100 条/日，触顶后点 Post 无效果）。"""
    try:
        layers = page.locator("#layers").first.inner_text(timeout=3000)
        return any(m in layers for m in DAILY_LIMIT_MARKERS)
    except Exception:
        return False


def wait_posted(page, text: str, timeout=40) -> bool:
    """发布成功判定（X 发送后发帖框保留并清空，不跳转 /status/）：
    1) URL 出现 /status/；2) 发帖框关闭；3) 发帖框文本被清空（已发送）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if "/status/" in page.url:
            return True
        try:
            ta = page.locator(SELECTORS["textarea"]).first
            if ta.is_visible(timeout=1200):
                if not ta.inner_text().strip():
                    return True  # 已发送：输入区已清空
            else:
                return True  # 发帖框已关闭
        except Exception:
            return True
        time.sleep(1)
    return False


def already_posted(page, text: str) -> bool:
    """防重复：时间线第一条若与本条前缀一致，说明已发布过（断点续跑保护）。"""
    prefix = text[:60]
    try:
        first = page.locator('[data-testid="tweetText"]').first
        if first.is_visible(timeout=3000):
            return first.inner_text()[:60] == prefix
    except Exception:
        pass
    return False


def safe_goto(page, url: str, attempts: int = 3) -> bool:
    """goto 带重试：X 偶发 ERR_ABORTED（导航被中断/重定向竞争）。"""
    for attempt in range(1, attempts + 1):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            return True
        except Exception as exc:
            log(f"goto 失败(第 {attempt}/{attempts} 次): {str(exc)[:120]}", "WARN")
            time.sleep(3)
    return False


def verify_login(page) -> bool:
    if not safe_goto(page, "https://x.com/home"):
        return False
    time.sleep(4)
    for sel in SELECTORS["compose_button"]:
        try:
            if page.locator(sel).first.is_visible(timeout=5000):
                return True
        except Exception:
            continue
    return False


def get_handle(page) -> str:
    """从导航取当前账号 handle（/xxx 形式）；取不到时回退 X_HANDLE 环境变量。"""
    try:
        href = page.locator('[data-testid="AppTabBar_Profile_Link"]').first.get_attribute("href")
        if href and href.startswith("/"):
            return href
    except Exception:
        pass
    env = os.environ.get("X_HANDLE", "").strip().lstrip("@")
    return f"/{env}" if env else ""


def latest_status_url(page, handle: str, tries: int = 4):  # returns Optional[str]
    """发布后从 profile 第一条抓最新推文 URL（带重试，等待时间线刷新）。"""
    for attempt in range(1, tries + 1):
        try:
            safe_goto(page, f"https://x.com{handle}", attempts=2)
            time.sleep(3)
            link = page.locator('article[data-testid="tweet"] a[href*="/status/"]').first.get_attribute("href", timeout=5000)
            if link and "/status/" in link:
                return "https://x.com" + link if link.startswith("/") else link
        except Exception:
            pass
        log(f"等待时间线刷新(第 {attempt}/{tries} 次)…", "WARN")
        time.sleep(3)
    return None


def post_reply(page, url: str, text: str, dry_run: bool) -> bool:
    """在指定推文下回复（线程模式：每条回复上一条）。"""
    if not safe_goto(page, url):
        return False
    time.sleep(4)
    reply_btn = page.locator('[data-testid="reply"]').first
    reply_btn.wait_for(state="visible", timeout=15000)
    reply_btn.click()
    time.sleep(2)
    if not fill_text(page, text):
        raise RuntimeError("回复输入 3 次校验失败")
    time.sleep(1)
    if dry_run:
        shot = LOG_DIR / f"dryrun-reply-{int(time.time())}.png"
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(shot))
        log(f"dry-run 回复已填好（截图 {shot.name}）")
        dismiss_confirm_sheet(page)
        return True
    submit(page)
    ok = wait_posted(page, text)
    if not ok:
        log("未检测到回复发布成功", "WARN")
    return ok


def run_check() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log("需要 playwright: pip install --user playwright", "ERROR")
        return 2
    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(CDP_URL)
        except Exception as exc:
            log(f"无法连接 {CDP_URL}。先双击 launch_x.command 重启 Chrome。: {exc}", "ERROR")
            return 2
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.new_page()
        ok = verify_login(page)
        log("X 登录态正常，可以发布" if ok else "X 未登录或页面结构变化，请人工检查 x.com")
        page.close()
        return 0 if ok else 3


def main() -> int:
    parser = argparse.ArgumentParser(description="X 发布 agent")
    parser.add_argument("--dry-run", action="store_true", help="填好内容但不发布")
    parser.add_argument("--start", type=int, default=1, help="从第 N 条开始")
    parser.add_argument("--interval", type=int, default=0, help="固定间隔秒数（0=45-90 随机）")
    parser.add_argument("--check", action="store_true", help="只检查登录态")
    parser.add_argument("--continue-on-error", action="store_true", help="单条失败后继续下一条")
    parser.add_argument("--file", default="thread.json", help="线程配置文件（默认 thread.json）")
    parser.add_argument("--thread", action="store_true", help="线程模式：第 2 条起作为上一条的回复（真线程）")
    args = parser.parse_args()

    cfg = json.loads((BASE / args.file).read_text(encoding="utf-8"))
    # 长度预检：X 上限 280（双宽字符按 2 计），Python len 是启发式；--check 模式跳过
    if args.check:
        return run_check()
    for i, t in enumerate(cfg["tweets"], 1):
        if len(t) > 280:
            log(f"预检失败: 第 {i} 条 {len(t)} 字符，必超 X 上限，中止（先压缩再发布）", "ERROR")
            return 2
        if len(t) > 275:
            log(f"预检警告: 第 {i} 条 {len(t)} 字符，接近上限（双宽字符可能超限）", "WARN")
    tweets = cfg["tweets"]
    if not 1 <= args.start <= len(tweets):
        log(f"--start 必须在 1..{len(tweets)}", "ERROR")
        return 2

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log("需要 playwright: pip install --user playwright", "ERROR")
        return 2

    records = []
    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(CDP_URL)
        except Exception as exc:
            log(f"无法连接 {CDP_URL}。先双击 launch_x.command 重启 Chrome。: {exc}", "ERROR")
            return 2
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.new_page()

        if args.check:
            ok = verify_login(page)
            log("X 登录态正常，可以发布" if ok else "X 未登录或页面结构变化，请人工检查 x.com")
            page.close()
            return 0 if ok else 3

        log("检查登录态…")
        if not verify_login(page):
            log("X 未登录，中止。请在浏览器里登录后重试。", "ERROR")
            page.close()
            return 3
        log("登录态正常。")

        if args.dry_run:
            log(f"DRY-RUN 模式：填 {len(tweets) - args.start + 1} 条，不发布。")

        handle = get_handle(page) if (args.thread and not args.dry_run) else None
        last_url = None
        for idx in range(args.start - 1, len(tweets)):
            text = tweets[idx]
            rec = {"index": idx + 1, "length": len(text), "started_at": datetime.now().isoformat()}
            try:
                if args.thread and idx > 0:
                    # 线程模式：回复上一条
                    if not last_url:
                        raise RuntimeError("线程模式缺少上一条 URL，中止")
                    if not post_reply(page, last_url, text, args.dry_run):
                        raise RuntimeError("回复发布失败")
                    rec.update({"mode": "reply", "reply_to": last_url})
                    if args.dry_run:
                        rec.update({"dry_run": True})
                        log(f"[{idx + 1}/{len(tweets)}] dry-run 回复已填好（不发布）")
                    else:
                        rec.update({"submitted": True, "posted": True})
                        log(f"[{idx + 1}/{len(tweets)}] 已发布回复 ({len(text)} 字符)")
                    time.sleep(3)
                else:
                    if not open_composer(page):
                        raise RuntimeError("找不到发帖按钮（可能被限流或 UI 变化）")
                    time.sleep(1.5)
                    if already_posted(page, text):
                        rec.update({"skipped": "already_posted"})
                        log(f"[{idx + 1}/{len(tweets)}] 检测到已发布过，跳过（防重复）")
                        continue
                    if not fill_text(page, text):
                        raise RuntimeError("输入内容 3 次校验失败，中止该条（防止发空推）")
                    time.sleep(1)
                    if args.dry_run:
                        shot = LOG_DIR / f"dryrun-{idx + 1:02d}.png"
                        LOG_DIR.mkdir(parents=True, exist_ok=True)
                        page.screenshot(path=str(shot))
                        rec.update({"dry_run": True, "screenshot": str(shot)})
                        log(f"[{idx + 1}/{len(tweets)}] dry-run 已填好（截图 {shot.name}），不发布")
                        try:
                            close = page.locator('[data-testid="Close"]').first
                            if close.is_visible(timeout=2000):
                                close.click()
                                time.sleep(1)
                        except Exception:
                            pass
                        dismiss_confirm_sheet(page)
                        time.sleep(1)
                    else:
                        submit(page)
                        ok = wait_posted(page, text)
                        rec.update({"submitted": True, "posted": ok})
                        if ok:
                            log(f"[{idx + 1}/{len(tweets)}] 已发布 ({len(text)} 字符)")
                        else:
                            if check_daily_limit(page):
                                log(f"[{idx + 1}/{len(tweets)}] X 每日发帖上限已到（免费账号触顶），今日停止发布", "ERROR")
                            else:
                                log(f"[{idx + 1}/{len(tweets)}] 未检测到发布成功（可能被限流/字符超限）", "WARN")
                            if not args.continue_on_error:
                                page.close()
                                write_result(records)
                                return 1
                        # 发布后停留一下，再关闭可能残留的弹层
                        time.sleep(3)

                # 抓取最新推文 URL 用于下一条回复
                if args.thread and not args.dry_run:
                    last_url = latest_status_url(page, handle)
                    if not last_url:
                        log("未能抓到刚发布推文 URL，线程中断", "WARN")
                        break
                    rec["status_url"] = last_url
            except Exception as exc:
                rec.update({"error": str(exc)[:300]})
                log(f"[{idx + 1}/{len(tweets)}] 失败: {exc}", "ERROR")
                if not args.continue_on_error:
                    rec["finished_at"] = datetime.now().isoformat()
                    records.append(rec)
                    page.close()
                    write_result(records)
                    return 1
            finally:
                rec["finished_at"] = datetime.now().isoformat()
                records.append(rec)

            if idx < len(tweets) - 1:
                wait = args.interval or random.randint(cfg["interval_seconds"][0], cfg["interval_seconds"][1])
                log(f"等待 {wait}s 后发下一条…")
                time.sleep(wait)

        page.close()

    write_result(records)
    log("完成。" + ("（dry-run，未发布任何内容）" if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
