"""万悟接入自检与精简 OpenAPI 导出命令。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

EXPECTED_OPERATION_IDS = {
    "quick_industrial_diagnosis",
    "submit_industrial_analysis",
    "get_industrial_analysis_status",
    "get_industrial_analysis_result",
    "get_industrial_decision_brief",
    "explain_industrial_run",
    "generate_industrial_shift_brief",
    "cancel_industrial_analysis",
    "list_industrial_work_orders",
    "update_industrial_work_order",
    "list_industrial_feedback_cases",
    "run_unattended_industrial_cycle",
    "get_unattended_monitoring_status",
    "dispatch_industrial_alerts",
    "list_industrial_data_sources",
    "configure_industrial_data_source",
    "verify_industrial_data_source",
    "run_industrial_sla_cycle",
    "run_industrial_reinspection_cycle",
}


def check_wanwu_integration(
    base_url: str,
    output_path: Path | None = None,
    platform_url: str | None = None,
    quick_output_path: Path | None = None,
) -> dict[str, Any]:
    """检查算法服务、完整 Schema、演示 Schema 和可选的平台网页。"""

    normalized = base_url.rstrip("/")
    health = _get_json(f"{normalized}/health")
    schema = _get_json(f"{normalized}/integrations/wanwu/openapi.json")
    quick_schema = _get_json(f"{normalized}/integrations/wanwu/quick-openapi.json")
    operation_ids = {
        operation["operationId"]
        for path_item in schema.get("paths", {}).values()
        for operation in path_item.values()
        if isinstance(operation, dict) and operation.get("operationId")
    }
    missing = sorted(EXPECTED_OPERATION_IDS - operation_ids)
    if health.get("status") != "ok":
        raise RuntimeError("健康检查未返回 status=ok")
    if missing:
        raise RuntimeError("万悟 OpenAPI 缺少工具：" + "、".join(missing))
    quick_operation_ids = {
        operation["operationId"]
        for path_item in quick_schema.get("paths", {}).values()
        for operation in path_item.values()
        if isinstance(operation, dict) and operation.get("operationId")
    }
    if quick_operation_ids != {"quick_industrial_diagnosis"}:
        raise RuntimeError("万悟快速 Schema 必须只包含 quick_industrial_diagnosis")
    platform_status = _check_http(platform_url) if platform_url else None
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(schema, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    if quick_output_path is not None:
        quick_output_path.parent.mkdir(parents=True, exist_ok=True)
        quick_output_path.write_text(
            json.dumps(quick_schema, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return {
        "status": "ready",
        "base_url": normalized,
        "service": health.get("service"),
        "tool_count": len(operation_ids),
        "operation_ids": sorted(operation_ids),
        "quick_tool_count": len(quick_operation_ids),
        "quick_operation_ids": sorted(quick_operation_ids),
        "schema_server": schema.get("servers", [{}])[0].get("url"),
        "platform_url": platform_url.rstrip("/") if platform_url else None,
        "platform_http_status": platform_status,
        "export_path": str(output_path.resolve()) if output_path else None,
        "quick_export_path": (
            str(quick_output_path.resolve()) if quick_output_path else None
        ),
    }


def _get_json(url: str) -> dict[str, Any]:
    """读取 JSON，并把网络错误转换为容易定位的中文说明。"""

    request = Request(url, headers={"User-Agent": "shicha-qianji-wanwu-check/1.0"})
    try:
        with urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise RuntimeError(f"访问 {url} 失败，HTTP {exc.code}") from exc
    except URLError as exc:
        raise RuntimeError(f"无法连接 {url}：{exc.reason}") from exc


def _check_http(url: str) -> int:
    """检查万悟网页是否能返回成功状态，不解析前端 HTML。"""

    request = Request(url.rstrip("/"), headers={"User-Agent": "shicha-qianji-check/1.0"})
    try:
        with urlopen(request, timeout=10) as response:
            status = int(response.status)
    except HTTPError as exc:
        raise RuntimeError(f"万悟平台 {url} 返回 HTTP {exc.code}") from exc
    except URLError as exc:
        raise RuntimeError(f"无法连接万悟平台 {url}：{exc.reason}") from exc
    if status < 200 or status >= 400:
        raise RuntimeError(f"万悟平台 {url} 返回异常状态 {status}")
    return status


def main() -> None:
    """命令行执行接入检查，默认同时导出精简 OpenAPI。"""

    parser = argparse.ArgumentParser(description="检查时察千机是否已准备好接入元景万悟")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--platform-url",
        default="http://127.0.0.1:8081",
        help="万悟网页地址；传空字符串表示不检查",
    )
    parser.add_argument(
        "--output",
        default="outputs/wanwu_openapi.json",
        help="精简 OpenAPI 导出路径；传空字符串表示不导出",
    )
    parser.add_argument(
        "--quick-output",
        default="outputs/wanwu_quick_openapi.json",
        help="比赛演示单工具 Schema 导出路径；传空字符串表示不导出",
    )
    args = parser.parse_args()
    output_path = Path(args.output) if args.output else None
    quick_output_path = Path(args.quick_output) if args.quick_output else None
    result = check_wanwu_integration(
        args.base_url,
        output_path,
        platform_url=args.platform_url or None,
        quick_output_path=quick_output_path,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
