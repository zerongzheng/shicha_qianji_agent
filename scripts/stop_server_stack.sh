#!/usr/bin/env bash

set -Eeuo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUTPUT_DIR="$PROJECT_ROOT/outputs"
WANWU_ROOT="${WANWU_ROOT:-$(cd "$PROJECT_ROOT/../wanwu" 2>/dev/null && pwd || true)}"

stop_pid() {
  local label="$1"
  local pid_name="$2"
  local pid_path="$OUTPUT_DIR/$pid_name"
  if [[ ! -f "$pid_path" ]]; then
    echo "$label 未发现 PID 文件"
    return
  fi
  local pid
  pid="$(cat "$pid_path" 2>/dev/null || true)"
  if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null &&
     tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null | grep -Fq "wanwu/scripts/trigger_wanwu_workflow.sh"; then
    kill "$pid" 2>/dev/null || true
    sleep 1
    kill -9 "$pid" 2>/dev/null || true
    echo "$label 已停止（PID $pid）"
  else
    echo "$label 进程已退出"
  fi
  rm -f "$pid_path"
}

stop_pid "无人值守巡检触发器" wanwu_autonomous_trigger.pid
stop_pid "SLA 督办触发器" wanwu_sla_trigger.pid
stop_pid "维修后复检触发器" wanwu_reinspection_trigger.pid
stop_pid "班次简报触发器" wanwu_shift_brief_trigger.pid

if [[ -f "$PROJECT_ROOT/docker-compose.server.yml" && -f "$PROJECT_ROOT/.env" ]]; then
  (cd "$PROJECT_ROOT" && docker compose --env-file .env -f docker-compose.server.yml stop) || true
fi

if [[ -d "$WANWU_ROOT" && -f "$WANWU_ROOT/.env" ]]; then
  (cd "$WANWU_ROOT" && docker compose --env-file .env --env-file .env.ontology --env-file .env.image.amd64 stop) || true
fi

echo "服务器模式已停止；PostgreSQL 数据卷、万悟数据卷和 outputs 文件均已保留。"
