#!/usr/bin/env bash

# Ubuntu 服务器上的万悟接入只读检查。
# 本脚本不会启动、停止或删除容器；在项目根目录执行：
#   bash scripts/check_wanwu_server.sh

set -u

NETWORK_NAME="${WANWU_DOCKER_NETWORK:-wanwu-net}"
API_CONTAINER="${SHICHA_API_CONTAINER:-shicha-qianji-api}"
WANWU_CONTAINER="${WANWU_WORKFLOW_CONTAINER:-workflow-wanwu}"
API_URL="${SHICHA_API_URL:-http://127.0.0.1:8000}"

echo "=== 磁盘与内存 ==="
df -h / "${SHICHA_OUTPUT_DIR:-./outputs}" 2>/dev/null || true
free -h || true

echo
echo "=== Docker 网络 ==="
if docker network inspect "$NETWORK_NAME" >/dev/null 2>&1; then
  docker network inspect "$NETWORK_NAME" \
    --format '{{range $id, $item := .Containers}}{{$item.Name}} {{end}}'
else
  echo "错误：找不到 Docker 网络 $NETWORK_NAME"
fi

echo
echo "=== 容器状态 ==="
for container in "$API_CONTAINER" "$WANWU_CONTAINER"; do
  if docker inspect "$container" >/dev/null 2>&1; then
    docker inspect "$container" \
      --format '{{.Name}} status={{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}'
  else
    echo "$container missing"
  fi
done

echo
echo "=== API 健康与 OpenAPI ==="
curl --fail --silent --show-error "$API_URL/health" || true
echo
curl --fail --silent --show-error \
  "$API_URL/integrations/wanwu/quick-openapi.json" \
  | python3 -c 'import json,sys; data=json.load(sys.stdin); print("schema_server=", data.get("servers", [{}])[0].get("url")); print("tools=", [op.get("operationId") for item in data.get("paths", {}).values() for op in item.values() if isinstance(op, dict)])' \
  || true

echo
echo "=== 万悟容器解析时察千机服务名 ==="
docker exec "$WANWU_CONTAINER" sh -c \
  "getent hosts $API_CONTAINER 2>/dev/null || ping -c 1 $API_CONTAINER 2>/dev/null" \
  || echo "警告：万悟容器无法解析 $API_CONTAINER，请检查两个项目是否都加入 $NETWORK_NAME"

echo
echo "检查完成。工具真实文件下载还需在万悟工作流中上传一份 CSV 进行验证。"
