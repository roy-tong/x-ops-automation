> **⚠️ 已停止维护（2026-10-11）**：本仓库的 X 渠道能力已完整并入 [marketing-ops-automation](https://github.com/roy-tong/marketing-ops-automation) —— 多渠道（X / Email / Reddit / LinkedIn / GitHub）统一执行内核，其 X 渠道即 vendor 自本仓库，且新增门禁/节流/回读校验/账本等写路径事务管道。请改用 marketing-ops-automation，本仓库仅作历史留档（archived）。

# x-ops-automation

X（Twitter）自有账号运营自动化工具集：发布线程、搜索候选、回复、关注、点赞、私信（DM）。
Python + Playwright（sync API）通过 **CDP 接管本机已登录的真实 Chrome** 完成，全程不保存任何账号凭据。

> 合规提示：本项目仅用于**你本人拥有/运营的账号**的日常运营自动化（发帖、互动、触达）。
> 请遵守 X 的服务条款与自动化规则，保持人类节奏，不做大规模抓取、垃圾营销或他人账号操控。
> 由此产生的一切账号风险（限流、锁号）由使用者自行承担。

## 架构：为什么用 CDP 接管真实 Chrome

所有脚本不做模拟登录、不碰账密、不走无头浏览器，而是：

```
launch_x.command ──启动──> 真实 Chrome（--remote-debugging-port=9222，专用 x-profile/）
      │                        │
      │ 复制本机登录 profile      │ CDP (Chrome DevTools Protocol)
      ▼                        ▼
  x-profile/（含登录态）   Python 脚本 connect_over_cdp("http://127.0.0.1:9222")
```

理由：

1. **登录态复用**：X 的登录风控极强（验证码、设备指纹、二次验证）。用真实已登录会话驱动，脚本零凭据、零登录逻辑，永不过期失效。
2. **风控友好**：接管的是真实 Chrome——真实的浏览器指纹、真实的 profile、真实的历史行为。配合脚本内的人类节奏输入（逐字符输入、随机间隔），比无头浏览器/API 裸调安全得多。
3. **Chrome 136+ 的安全限制**：默认 profile 下 `--remote-debugging-port` 会被忽略，必须使用非默认 `--user-data-dir`。`launch_x.command` 首次运行会把本机 Chrome 登录 profile 复制到脚本旁的 `x-profile/`（排除缓存），此后用它启动带调试端口的 Chrome。

`x-profile/` 含登录态（等同于账号本身），**已被 .gitignore 排除，绝不能提交或分发**。

## 安装与快速开始

```bash
pip install -r requirements.txt   # 只需要 playwright 包本身
# 无需 `playwright install` 下载浏览器：走的是系统已装的真实 Chrome

# 1. 启动调试会话（双击 launch_x.command，或命令行执行）
./launch_x.command
#    首次运行会复制登录 profile（约 1-2GB，几分钟）；之后秒级启动

# 2. 在弹出的 Chrome 里确认 x.com 已登录，然后检查会话
python3 publish_x.py --check
```

## 命令清单与 dry-run 约定

**所有写操作命令（发布/回复/DM）都支持 dry-run：只把内容填进输入框并截图留证，不点发送。搜索入口默认 dry-run，加 `--go` 才执行。请养成先 dry-run 再实跑的习惯。**

| 脚本 | 作用 | 用法 |
|---|---|---|
| `launch_x.command` | 启动带 CDP 9222 的真实 Chrome（复用登录态） | 双击或 `./launch_x.command` |
| `publish_x.py` | 发布推文/线程（支持断点续跑、防重复、每日上限检测） | `python3 publish_x.py --dry-run` 后去掉实跑 |
| `search_x.py` | 搜索单个关键词，输出候选推文（作者/摘要/URL） | `python3 search_x.py "agent observability since:2026-08-18"` |
| `x_search.sh` | 多关键词批量搜索的统一入口（见下） | `./x_search.sh --query ... [--go]` |
| `reply_x.py` | 在指定推文下回复（可选点赞） | `python3 reply_x.py --tweet-url <url> --text "..." [--like] [--dry-run]` |
| `follow_x.py` | 批量关注（单次 ≤10） | `python3 follow_x.py @a @b @c` |
| `like_x.py` | 批量点赞（单次 ≤10） | `python3 like_x.py <url1> <url2>` |
| `xdm.py` | 队列化发私信（含收件箱关闭检测、防重发） | `python3 xdm.py --dry-run` |
| `scan_inbox.py` | 辅助工具：同一 CDP 会话只读扫描 Gmail 收件箱 | `python3 scan_inbox.py` |

### publish_x.py（发布）

```bash
python3 publish_x.py --check            # 只检查登录态
python3 publish_x.py --dry-run          # 逐条填入编辑器 + 截图（logs/dryrun-*.png），不发布
python3 publish_x.py --dry-run --thread # 线程模式预演（第 2 条起是回复第 1 条）
python3 publish_x.py --start N          # 断点续跑：从第 N 条继续
python3 publish_x.py --interval 60      # 固定间隔（默认 45-90s 随机）
python3 publish_x.py --continue-on-error
python3 publish_x.py --file mythread.json
```

内容文件 `thread.json`（默认读取，已被 .gitignore 排除）：

```json
{
  "interval_seconds": [45, 90],
  "tweets": ["第一条", "第二条（线程模式下作为第一条的回复）"]
}
```

内置保护：发布前 280 字符长度预检；输入后读回校验（防发空推/错推）；时间线首条前缀比对防重复；检测「每日发帖上限」横幅自动停止。

### x_search.sh（批量搜索统一入口）

把搜索词、时间窗、输出文件参数化，收敛曾经散落的手写变体。**默认 dry-run：只打印执行计划，不访问 X；加 `--go` 才真正执行（纯读操作）。**

```bash
# 1. 先看计划（dry-run）
./x_search.sh --query "agent observability" --query "MCP tool calls" \
              --since 2026-08-18 --until 2026-08-19 \
              --limit 8 --max-pages 1 --sleep 5 \
              --out logs/reply-candidates-20260921.txt

# 2. 确认无误后执行
./x_search.sh --query "agent observability" --since 2026-08-18 --until 2026-08-19 \
              --out logs/reply-candidates-20260921.txt --go

# 或从文件读 query 列表（每行一个，# 注释与空行忽略；可与 --query 混用）
./x_search.sh --queries-file queries.txt --out logs/foo.txt --go
```

参数说明：

| 参数 | 说明 |
|---|---|
| `--query / -q` | 搜索词，**可重复**传多个 |
| `--queries-file / -f` | 从文件读 query 列表（每行一个，`#` 后为注释） |
| `--since / --until` | `YYYY-MM-DD`，只是拼接 X 的 `since:`/`until:` 搜索操作符，不做本地过滤 |
| `--limit` | 每个 query 取几条候选（默认 8） |
| `--max-pages` | 滚动翻页次数（默认 1=只取首屏，>1 按推文 URL 去重） |
| `--sleep` | query 之间的间隔秒数（默认 5） |
| `--out / -o` | 输出文件（默认 `logs/reply-candidates-<时间戳>.txt`） |
| `--go` | **不加就永远是 dry-run**；加了才真正执行 |

失败即停：任一 query 失败（如未登录、CDP 未就绪）立即终止，不做重试轰炸；已写入的部分结果保留。

### xdm.py（私信）

队列文件 `~/.x-ops-automation/outreach/xdm-queue.json`（可用 `X_OPS_OUTREACH_DIR` 改位置）：

```json
[
  {"id": "001", "handle": "someone", "name": "Some One", "message": "Hi ...", "paused": false}
]
```

```bash
python3 xdm.py --dry-run          # 逐条写信 + 截图（不发送）
python3 xdm.py                    # 正式发送（自动跳过已 sent / no-dm 的 handle）
python3 xdm.py --only <handle>    # 只处理指定 handle
python3 xdm.py --interval 120     # 固定间隔（默认 75-150s 随机）
```

内置纪律：消息内容禁止 em/en dash（脚本强制断言）；发送前校验会话面板确实包含目标 handle（防发错人）；识别「对方未开启私信」的系统提示并记入 no-dm 名单，后续自动跳过。

## 防限速策略

写操作全部模拟人类节奏，并带硬上限：

- **发布**：单条间隔 45-90s 随机（`--interval` 可固定）；检测到每日上限横幅立即停止当日发布。
- **关注**：单次 ≤10 个（脚本硬限制），每个间隔 8s。
- **点赞**：单次 ≤10 个（脚本硬限制），每个间隔 6s。
- **私信**：单条间隔 75-150s 随机；逐字符慢速输入搜索词。
- **搜索**：query 间隔默认 5s；失败即停不重试轰炸。
- **通用**：逐字符输入（`press_sequentially`/慢速 `type`）而非剪贴板粘贴；页面加载留足等待时间；`goto` 带重试但间隔 3s。

## 截图与日志审计

每类操作都有可回溯的留痕，全部落在 `logs/`（或 DM 的 outreach 目录），均已 gitignore：

- **dry-run 截图**：`logs/dryrun-*.png`（发布）、`logs/reply-dryrun-*.png`（回复）、outreach `shots/xdm-<handle>.png`（DM——正式发送前也必截图）。
- **结构化结果**：每次运行写 `logs/publish-*.json` / `follow-*.json` / `like-*.json` / `reply-*.json`，逐条记录成功/失败/跳过原因与时间戳。
- **DM 追加日志**：`xdm-log.jsonl` 记录每个 handle 的处理结果（sent / no-dm / handle-not-found / recipient-mismatch / ...），作为防重发名单。

## 环境变量与配置

| 变量 | 默认值 | 用途 | 影响脚本 |
|---|---|---|---|
| `X_CDP_URL` | `http://127.0.0.1:9222` | CDP 调试端点（如端口冲突可改） | 全部 Python 脚本 |
| `X_HANDLE` | 空 | 线程模式下页面取不到账号 handle 时的回退（不带 @） | `publish_x.py` |
| `X_DM_PASSCODE` | 空 | X 发 DM 弹密码确认时自动填入的密码；未设置则不自动处理（发送可能停在确认框） | `xdm.py` |
| `X_OPS_OUTREACH_DIR` | `~/.x-ops-automation/outreach` | DM 队列 / 日志 / 截图目录 | `xdm.py` |

| 文件/目录 | 默认位置 | 说明 |
|---|---|---|
| `thread.json` | 脚本旁 | 发布内容（`--file` 可换）；个人数据，不入库 |
| `xdm-queue.json` | outreach 目录 | DM 队列；个人数据，不入库 |
| `x-profile/` | 脚本旁 | `launch_x.command` 生成的 Chrome 登录态副本；**等同账号凭据，绝不入库/分发** |
| `logs/` | 脚本旁 | 截图与运行记录 |

依赖：仅需 `playwright`（见 `requirements.txt`），因为走 CDP 连接系统 Chrome，无需下载 Playwright 自带浏览器。仅支持 macOS（Chrome 路径与 `launch_x.command` 按 macOS 写死，其余脚本跨平台）。

## License

MIT（见 [LICENSE](LICENSE)）。
