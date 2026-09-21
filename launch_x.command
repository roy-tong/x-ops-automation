#!/usr/bin/env bash
# 启动 agent-used 发布会话：
#  1) 复制 Chrome 登录 profile（保留 X 登录态）到专用目录——Chrome 136+ 出于安全限制，
#     默认 profile 下 --remote-debugging-port 会被忽略，必须使用非默认 --user-data-dir
#  2) 用复制出的 profile 启动 Chrome，开启远程调试端口 9222
# 双击运行即可。之后保持 Chrome 开着，再执行 publish_x.py。
set -euo pipefail

PORT=9222
BASE="$(cd "$(dirname "$0")" && pwd)"
PROFILE_DIR="$BASE/x-profile"
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
SRC_PROFILE="$HOME/Library/Application Support/Google/Chrome/Default"

# 1. 端口已被占用 → 会话已就绪
if lsof -ti:"$PORT" >/dev/null 2>&1; then
  echo "端口 $PORT 已在监听（调试会话已就绪）。直接运行 publish_x.py 即可。"
  exit 0
fi

# 2. 首次运行：复制登录 profile（排除缓存，约 1-2GB）
if [[ ! -d "$PROFILE_DIR/Default" ]]; then
  echo "首次运行：复制 Chrome 登录 profile（保留 X 登录态）…"
  mkdir -p "$PROFILE_DIR/Default"
  rsync -a --quiet \
    --exclude='Cache' --exclude='Cache_Data' --exclude='Code Cache' --exclude='GPUCache' \
    --exclude='DawnGraphiteCache' --exclude='DawnWebGPUCache' --exclude='GrShaderCache' \
    --exclude='ShaderCache' --exclude='Service Worker' --exclude='CacheStorage' \
    --exclude='component_crx_cache' --exclude='OptimizationHints' --exclude='GraphiteDawnCache' \
    --exclude='media_cache' --exclude='Application Cache' --exclude='blob_storage' --exclude='File System' \
    --exclude='BrowserMetrics' --exclude='JumpListIcons*' --exclude='Top Sites' \
    --exclude='History*' --exclude='Visited Links' --exclude='Last Session' --exclude='Last Tabs' \
    --exclude='Network Action Predictor' --exclude='DIPS' --exclude='InterestActionHistory' \
    --exclude='url_param_scanner' --exclude='WebRTC Logs' --exclude='Extension State' \
    --exclude='FileTypePolicies' --exclude='Media History' --exclude='Notifications' \
    --exclude='PreviewThumbnail' --exclude='Reporting and NEL' --exclude='Sessions' \
    --exclude='SharedDictionary' --exclude='Storage' --exclude='Sync Data' --exclude='Translate' \
    --exclude='ZxcvbnData' --exclude='SafetyTips' --exclude='Subresource Filter' \
    "$SRC_PROFILE/" "$PROFILE_DIR/Default/" || true
  echo "profile 复制完成。"
fi

# 3. 启动 Chrome（用复制出的 profile）
"$CHROME" --user-data-dir="$PROFILE_DIR" --remote-debugging-port="$PORT" \
  --no-first-run --no-default-browser-check >/dev/null 2>&1 &

# 4. 等待端口就绪
for i in $(seq 1 30); do
  if curl -s "http://127.0.0.1:$PORT/json/version" >/dev/null 2>&1; then
    echo "Chrome 调试会话就绪（端口 ${PORT}）。"
    echo "确认 x.com 已登录后运行：python3 publish_x.py --check"
    exit 0
  fi
  sleep 1
done
echo "Chrome 调试端口未能就绪，请手动检查。" >&2
exit 1
