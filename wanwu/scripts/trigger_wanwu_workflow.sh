#!/usr/bin/env bash

set -Eeuo pipefail

CONFIG_PATH=""
RUN_ONCE=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --config) CONFIG_PATH="${2:-}"; shift 2 ;;
    --run-once) RUN_ONCE=1; shift ;;
    *) echo "用法: $0 --config PATH [--run-once]" >&2; exit 2 ;;
  esac
done

[[ -n "$CONFIG_PATH" && -f "$CONFIG_PATH" ]] || {
  echo "工作流配置不存在: $CONFIG_PATH" >&2
  exit 1
}

PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
if [[ -f "$PROJECT_ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$PROJECT_ROOT/.env"
  set +a
fi

mapfile -t VALUES < <(python3 - "$CONFIG_PATH" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    config = json.load(handle)
print(str(config.get("wanwu_base_url", "")).strip())
print(str(config.get("workflow_uuid", "")).strip())
print(str(config.get("api_key_env", "WANWU_WORKFLOW_API_KEY")).strip())
print(max(10, int(config.get("interval_seconds", 60))))
PY
)

BASE_URL="${VALUES[0]:-}"
WORKFLOW_UUID="${VALUES[1]:-}"
API_KEY_ENV="${VALUES[2]:-WANWU_WORKFLOW_API_KEY}"
INTERVAL_SECONDS="${VALUES[3]:-60}"
API_KEY="${!API_KEY_ENV-}"

[[ -n "$BASE_URL" && -n "$WORKFLOW_UUID" && "$WORKFLOW_UUID" != "WANWU_WORKFLOW_UUID" ]] || {
  echo "工作流 base_url 或 UUID 未配置: $CONFIG_PATH" >&2
  exit 1
}
[[ -n "$API_KEY" ]] || {
  echo "未配置 API Key 环境变量: $API_KEY_ENV" >&2
  exit 1
}

REQUEST_BODY="$(python3 - "$CONFIG_PATH" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    config = json.load(handle)
parameters = config.get("parameters") or {}
if not isinstance(parameters, dict):
    raise ValueError("工作流 parameters 必须是 JSON 对象")
print(json.dumps(
    {
        "uuid": str(config.get("workflow_uuid", "")).strip(),
        "parameters": parameters,
    },
    ensure_ascii=False,
))
PY
)"
RUN_URL="${BASE_URL%/}/service/api/openapi/v1/workflow/run"

while true; do
  timestamp="$(date '+%Y-%m-%d %H:%M:%S')"
  if response="$(curl --fail --silent --show-error --max-time 300 \
      -X POST "$RUN_URL" \
      -H "Authorization: Bearer $API_KEY" \
      -H 'Accept: application/json' \
      -H 'Content-Type: application/json; charset=utf-8' \
      --data "$REQUEST_BODY")"; then
    if python3 - "$response" <<'PY'
import json
import sys

result = json.loads(sys.argv[1])
code = result.get("code")
if code is not None and int(code) != 0:
    raise SystemExit(1)
PY
    then
      printf '%s\n' "{\"timestamp\":\"$timestamp\",\"status\":\"success\",\"result\":$response}"
    else
      printf '%s\n' "{\"timestamp\":\"$timestamp\",\"status\":\"failed\",\"result\":$response}"
    fi
  else
    printf '%s\n' "{\"timestamp\":\"$timestamp\",\"status\":\"failed\"}"
  fi
  [[ "$RUN_ONCE" -eq 1 ]] && break
  sleep "$INTERVAL_SECONDS"
done
