#!/usr/bin/env bash

set -Eeuo pipefail

SKIP_TRIGGERS=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-triggers) SKIP_TRIGGERS=1; shift ;;
    *) echo "用法: $0 [--skip-triggers]" >&2; exit 2 ;;
  esac
done

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WANWU_ROOT="${WANWU_ROOT:-$(cd "$PROJECT_ROOT/../wanwu" 2>/dev/null && pwd || true)}"
OUTPUT_DIR="$PROJECT_ROOT/outputs"
LOG_DIR="$OUTPUT_DIR/server_logs"
WANWU_ENV_ARGS=(--env-file .env --env-file .env.ontology --env-file .env.image.amd64)

die() { echo "错误: $*" >&2; exit 1; }
require_cmd() { command -v "$1" >/dev/null 2>&1 || die "缺少命令: $1"; }

require_cmd docker
require_cmd curl
require_cmd python3
[[ -f "$PROJECT_ROOT/.env" ]] || die "缺少服务器配置 $PROJECT_ROOT/.env"
[[ -d "$WANWU_ROOT" ]] || die "找不到万悟目录: $WANWU_ROOT"
[[ -f "$WANWU_ROOT/.env" && -f "$WANWU_ROOT/.env.ontology" ]] || die "万悟 .env 配置不完整"

set -a
# shellcheck disable=SC1091
source "$PROJECT_ROOT/.env"
set +a

validate_workflow_configs() {
  local config_path
  for config_path in "$OUTPUT_DIR"/wanwu_*_workflow.local.json; do
    [[ -f "$config_path" ]] || die "缺少服务器工作流配置: $config_path"
    mapfile -t values < <(python3 - "$config_path" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    config = json.load(handle)
uuid = str(config.get("workflow_uuid", "")).strip()
api_key_env = str(config.get("api_key_env", "WANWU_WORKFLOW_API_KEY")).strip()
base_url = str(config.get("wanwu_base_url", "")).strip()
print(uuid)
print(api_key_env)
print(base_url)
PY
    ) || die "工作流配置不是有效 JSON: $config_path"
    [[ "${values[0]:-}" =~ ^[0-9]+$ ]] || die "workflow_uuid 未配置: $config_path"
    [[ -n "${values[2]:-}" ]] || die "wanwu_base_url 未配置: $config_path"
    key_env="${values[1]}"
    api_key="${!key_env-}"
    [[ -n "$api_key" ]] || die "未配置服务器工作流 API Key: $key_env（文件: $config_path）"
  done
}

if [[ "$SKIP_TRIGGERS" -eq 0 ]]; then
  validate_workflow_configs
fi

mkdir -p "$LOG_DIR"
docker network inspect wanwu-net >/dev/null 2>&1 || die "找不到 wanwu-net，请先启动服务器万悟"

wait_for_http() {
  local label="$1" url="$2" attempts="${3:-60}"
  local attempt
  for ((attempt = 1; attempt <= attempts; attempt++)); do
    if curl --fail --silent --show-error --max-time 3 "$url" >/dev/null 2>&1; then
      echo "$label 已就绪"
      return 0
    fi
    sleep 2
  done
  die "$label 未在预期时间内就绪: $url"
}

echo "[1/4] 校验时察千机 Compose 配置"
(cd "$PROJECT_ROOT" && docker compose --env-file .env -f docker-compose.server.yml config --quiet)

echo "[2/4] 启动完整万悟"
(cd "$WANWU_ROOT" && docker compose "${WANWU_ENV_ARGS[@]}" up -d)
wait_for_http "万悟 HTTP 网关" "http://127.0.0.1:8081"

echo "[3/4] 启动时察千机 API、PostgreSQL、Vue3"
(cd "$PROJECT_ROOT" && docker compose --env-file .env -f docker-compose.server.yml up -d --build)
wait_for_http "时察千机 FastAPI" "http://127.0.0.1:${SHICHA_API_HOST_PORT:-8000}/health"

if [[ "$SKIP_TRIGGERS" -eq 1 ]]; then
  echo "[4/4] 按要求跳过四个万悟工作流触发器"
  echo
  echo "服务器基础服务已启动。完成工作流迁移并填写新 UUID/API Key 后执行："
  echo "bash scripts/start_server_stack.sh"
  exit 0
fi

echo "[4/4] 启动四个万悟工作流触发器"
TRIGGER_SCRIPT="$PROJECT_ROOT/wanwu/scripts/trigger_wanwu_workflow.sh"
declare -a DEFINITIONS=(
  "无人值守巡检|wanwu_autonomous_workflow.local.json|wanwu_autonomous_trigger.pid|wanwu_autonomous_trigger.log"
  "SLA督办|wanwu_sla_workflow.local.json|wanwu_sla_trigger.pid|wanwu_sla_trigger.log"
  "维修后复检|wanwu_reinspection_workflow.local.json|wanwu_reinspection_trigger.pid|wanwu_reinspection_trigger.log"
  "班次简报|wanwu_shift_brief_workflow.local.json|wanwu_shift_brief_trigger.pid|wanwu_shift_brief_trigger.log"
)

for definition in "${DEFINITIONS[@]}"; do
  IFS='|' read -r label config_name pid_name log_name <<< "$definition"
  config_path="$OUTPUT_DIR/$config_name"
  pid_path="$OUTPUT_DIR/$pid_name"
  log_path="$LOG_DIR/$log_name"
  [[ -f "$config_path" ]] || die "$label 配置不存在: $config_path"
  if [[ -f "$pid_path" ]]; then
    pid="$(cat "$pid_path" 2>/dev/null || true)"
    if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null &&
       tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null | grep -Fq "$TRIGGER_SCRIPT"; then
      echo "$label 已运行（PID $pid）"
      continue
    fi
    rm -f "$pid_path"
  fi
  nohup bash "$TRIGGER_SCRIPT" --config "$config_path" >> "$log_path" 2>&1 < /dev/null &
  pid=$!
  echo "$pid" > "$pid_path"
  echo "$label 已启动（PID $pid）"
done

echo
echo "服务器模式已启动。"
echo "万悟网页: http://127.0.0.1:8081（通过 SSH 隧道访问）"
echo "FastAPI: http://127.0.0.1:${SHICHA_API_HOST_PORT:-8000}"
echo "Vue3: http://127.0.0.1:${SHICHA_FRONTEND_HOST_PORT:-5173}"
echo "停止: bash scripts/stop_server_stack.sh"
