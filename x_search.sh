#!/bin/bash
# x_search.sh — X 搜索的统一参数化入口（多关键词 / 时间窗 / 输出文件全部参数化）。
#
# 用法:
#   ./x_search.sh --query "agent observability" --query "MCP tool calls" \
#                 [--since 2026-08-18] [--until 2026-08-19] \
#                 [--limit 8] [--max-pages 1] [--sleep 5] \
#                 [--out logs/reply-candidates-YYYYMMDD-HHMM.txt] --go
#
#   # 或从文件读 query 列表（每行一个，# 注释与空行忽略）:
#   ./x_search.sh --queries-file queries.txt --out logs/foo.txt --go
#
# 安全约定:
#   - 默认 dry-run：只打印将执行的完整计划（每个 query 的最终搜索词、输出文件），不访问 X。
#     确认无误后加 --go 才真正执行（读操作，走 launch_x.command 开的 CDP 9222 会话）。
#   - 每个 query 之间默认停 5 秒（--sleep 可调），比旧变体的连续轰炸温和。
#   - 任一 query 失败（如未登录，search_x.py 退出码 3）立即停止，不做重试轰炸；已写入的
#     部分结果保留在输出文件里。
#   - --since/--until 只是拼接 X 搜索的 since:/until: 操作符，不做本地过滤。
#
set -euo pipefail

BASE="$(cd "$(dirname "$0")" && pwd)"
cd "$BASE"

usage() { sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

QUERIES=()
SINCE=""
UNTIL=""
LIMIT=8
MAX_PAGES=1
SLEEP=5
OUT="logs/reply-candidates-$(date +%Y%m%d-%H%M).txt"
GO=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --query|-q)        [[ $# -ge 2 ]] || usage 1; QUERIES+=("$2"); shift 2 ;;
    --queries-file|-f) [[ $# -ge 2 ]] || usage 1; QUERIES_FILE="$2"; shift 2 ;;
    --since)           [[ $# -ge 2 ]] || usage 1; SINCE="$2"; shift 2 ;;
    --until)           [[ $# -ge 2 ]] || usage 1; UNTIL="$2"; shift 2 ;;
    --limit)           [[ $# -ge 2 ]] || usage 1; LIMIT="$2"; shift 2 ;;
    --max-pages)       [[ $# -ge 2 ]] || usage 1; MAX_PAGES="$2"; shift 2 ;;
    --sleep)           [[ $# -ge 2 ]] || usage 1; SLEEP="$2"; shift 2 ;;
    --out|-o)          [[ $# -ge 2 ]] || usage 1; OUT="$2"; shift 2 ;;
    --go)              GO=1; shift ;;
    -h|--help)         usage 0 ;;
    *)                 echo "未知参数: $1" >&2; usage 1 ;;
  esac
done

# 从文件补充 query 列表（与 --query 可混用）
if [[ -n "${QUERIES_FILE:-}" ]]; then
  [[ -f "$QUERIES_FILE" ]] || { echo "queries-file 不存在: $QUERIES_FILE" >&2; exit 1; }
  while IFS= read -r line; do
    line="${line%%#*}"
    [[ -z "${line// /}" ]] && continue
    QUERIES+=("$line")
  done < "$QUERIES_FILE"
fi

[[ ${#QUERIES[@]} -ge 1 ]] || { echo "至少需要一个 --query 或 --queries-file" >&2; usage 1; }

for v in "$SINCE" "$UNTIL"; do
  [[ -z "$v" ]] || [[ "$v" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || {
    echo "--since/--until 需要 YYYY-MM-DD 格式，收到: $v" >&2; exit 1; }
done
[[ "$LIMIT" =~ ^[0-9]+$ && "$MAX_PAGES" =~ ^[0-9]+$ && "$SLEEP" =~ ^[0-9]+$ ]] || {
  echo "--limit/--max-pages/--sleep 需要是非负整数" >&2; exit 1; }

# 组装每个 query 的最终搜索词（since/until 是 X 搜索操作符，随 query 走 URL）
effective() {
  local q="$1"
  [[ -n "$SINCE" ]] && q="$q since:$SINCE"
  [[ -n "$UNTIL" ]] && q="$q until:$UNTIL"
  printf '%s' "$q"
}

mkdir -p "$(dirname "$OUT")"

echo "=== x_search 计划 $(date '+%F %H:%M') ==="
echo "  输出文件 : $OUT"
echo "  参数     : limit=$LIMIT  max-pages=$MAX_PAGES  sleep=${SLEEP}s  since=${SINCE:-无}  until=${UNTIL:-无}"
echo "  query 数 : ${#QUERIES[@]}"
i=0
for q in "${QUERIES[@]}"; do
  i=$((i + 1))
  echo "  [$i] $(effective "$q")"
done

if [[ $GO -ne 1 ]]; then
  echo ""
  echo "dry-run：以上为将执行的计划，未访问 X。加 --go 执行。"
  exit 0
fi

echo "=== x_search 结果 $(date '+%F %H:%M') (limit=$LIMIT max-pages=$MAX_PAGES) ===" > "$OUT"
i=0
for q in "${QUERIES[@]}"; do
  i=$((i + 1))
  eff="$(effective "$q")"
  {
    echo ""
    echo "### QUERY: $q"
    if [[ "$eff" != "$q" ]]; then
      echo "    (effective: $eff)"
    fi
  } >> "$OUT"
  echo "[${i}/${#QUERIES[@]}] 搜索: $eff"
  if ! python3 search_x.py "$eff" --limit "$LIMIT" --max-pages "$MAX_PAGES" >> "$OUT" 2>&1; then
    echo "query 失败（search_x.py 非零退出，常见原因：未登录/CDP 未就绪），停止后续 query。" >&2
    echo "已写入的部分结果在: $OUT" >&2
    exit 1
  fi
  [[ $i -lt ${#QUERIES[@]} ]] && sleep "$SLEEP"
done
echo "done: $OUT"
