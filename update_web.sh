#!/usr/bin/env bash
# 重新生成 web/data.json；有变化则提交并推送（GitHub Pages 自动发布）
set -uo pipefail
cd "$(dirname "$0")"
export PATH="/usr/local/bin:/usr/bin:/bin"
PY="$HOME/Documents/ClaudeCode/gold/.venv/bin/python"

if ! "$PY" run.py json --no-position > web/data.json.tmp 2>>update_web.log; then
  echo "[$(date '+%F %T')] 取数失败，保留旧 data.json" >> update_web.log
  rm -f web/data.json.tmp
  exit 1
fi
# 只比较会变的字段：时间戳每次不同，比较去掉 updated 后的内容避免空提交
if [ -f web/data.json ] && \
   diff <(grep -v '"updated"' web/data.json.tmp) <(grep -v '"updated"' web/data.json) -q; then
  rm -f web/data.json.tmp
else
  mv web/data.json.tmp web/data.json
  git add web/data.json
  git commit -m "data: $(date '+%F %H:%M') 上金所刷新" >> update_web.log 2>&1
  git push origin main >> update_web.log 2>&1 || echo "[$(date '+%F %T')] push 失败" >> update_web.log
fi
