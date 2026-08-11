"""生成万悟自定义工具能够稳定解析的精简 OpenAPI Schema。

FastAPI 默认生成 OpenAPI 3.1，并会使用 ``$ref``、``anyOf`` 和 ``null`` 表达可选字段。
万悟自定义工具页面当前按 OpenAPI 3.0 的简化结构解析，这些写法可能导致文本框能显示
Schema，但“可用 API”表格为空。因此这里对万悟专用协议做一次兼容转换，标准 FastAPI
``/openapi.json`` 不受影响。
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def build_wanwu_openapi(
    full_schema: dict[str, Any],
    public_base_url: str,
    *,
    quick_only: bool = False,
    api_key_required: bool = False,
) -> dict[str, Any]:
    """生成 OpenAPI 3.0 兼容格式，可选只暴露比赛演示工具。"""

    schema = deepcopy(full_schema)
    schema["openapi"] = "3.0.0"
    schema["info"] = {
        "title": "时察千机 - 元景万悟工具",
        "description": (
            "比赛演示优先使用 quick_industrial_diagnosis，一次完成工业分析并返回中文摘要；"
            "另提供异步任务、工单闭环与历史案例检索工具。"
        ),
        "version": "0.5.0",
    }
    schema["servers"] = [
        {"url": public_base_url, "description": "时察千机工业分析服务"}
    ]
    schema["paths"] = {
        path: definition
        for path, definition in schema.get("paths", {}).items()
        if path.startswith("/api/v1/wanwu/")
        and (not quick_only or path == "/api/v1/wanwu/quick-diagnosis")
    }
    if quick_only:
        schema["info"]["title"] = "时察千机 - 万悟快速诊断工具"
        schema["info"]["description"] = (
            "比赛演示专用单工具：接收工业时序 CSV，返回异常事件、根因候选、"
            "运维建议和中文摘要；不调用外部大模型。"
        )
        quick_operation = schema["paths"].get("/api/v1/wanwu/quick-diagnosis", {}).get("post")
        if isinstance(quick_operation, dict):
            # 工具描述会直接进入万悟的工具编排上下文，明确终止条件可减少重复调用。
            quick_operation["description"] = (
                "比赛演示唯一分析工具。接收一个工业时序 CSV 的 file_url 或 file_base64，"
                "一次返回 presentation 和 analysis。调用成功后直接展示 presentation，"
                "不要继续调用其他工业分析工具，也不要再次请求大模型整理同一结果。"
            )
    # 万悟当前工具解析器更适合直接读取请求体结构，不依赖 components/$ref。
    for path_item in schema["paths"].values():
        for operation in path_item.values():
            if not isinstance(operation, dict):
                continue
            if api_key_required:
                # 服务器启用服务密钥后，让万悟导入工具时识别需要配置的鉴权字段。
                operation["security"] = [{"IndustrialApiKey": []}]
            else:
                operation.pop("security", None)
            # 本地默认不启用工业 API Key。移除 FastAPI 自动生成的可空请求头参数，
            # 避免万悟工具解析器因 anyOf/null 而不展示可用 API。
            operation["parameters"] = [
                parameter
                for parameter in operation.get("parameters", [])
                if not (
                    isinstance(parameter, dict)
                    and parameter.get("name", "").lower() == "x-api-key"
                )
            ]
            # 保留成功响应的 JSON 结构。万悟工具节点会根据 200 响应的
            # properties 生成“输出”字段；如果这里只保留 description，
            # 工具虽然可以运行，但工作流无法引用 presentation、analysis 等结果。
            operation["responses"] = _simplify_responses(
                operation.get("responses", {}),
                schema,
            )
            request_body = operation.get("requestBody")
            if request_body:
                operation["requestBody"] = _simplify_request_body(request_body, schema)

    schema["components"] = {
        "securitySchemes": {
            "IndustrialApiKey": {
                "type": "apiKey",
                "in": "header",
                "name": "X-API-Key",
                "description": "服务启用 INDUSTRIAL_API_KEY 时填写；本地开发可留空。",
            }
        }
    }
    return schema


def _simplify_request_body(request_body: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    """将 JSON 请求体的 $ref 展开，并移除万悟不兼容的可空联合类型。"""

    content = request_body.get("content", {})
    json_content = content.get("application/json", {})
    body_schema = _resolve_schema(json_content.get("schema", {}), schema)
    return {
        "required": bool(request_body.get("required", True)),
        "content": {"application/json": {"schema": _simplify_schema(body_schema, schema)}},
    }


def _resolve_schema(value: Any, root: dict[str, Any]) -> dict[str, Any]:
    """解析本文件内部的简单组件引用；外部引用直接按空对象处理。"""

    if not isinstance(value, dict):
        return {}
    reference = value.get("$ref")
    if isinstance(reference, str) and reference.startswith("#/components/schemas/"):
        name = reference.rsplit("/", 1)[-1]
        target = root.get("components", {}).get("schemas", {}).get(name, {})
        return deepcopy(target) if isinstance(target, dict) else {}
    return deepcopy(value)


def _simplify_schema(value: dict[str, Any], root: dict[str, Any]) -> dict[str, Any]:
    """递归转换万悟解析器需要兼容的 Schema 写法。

    FastAPI/Pydantic 生成的是 OpenAPI 3.1 风格约束：数值型
    ``exclusiveMinimum`` 和 ``exclusiveMaximum`` 直接保存阈值，例如
    ``exclusiveMinimum: 0.0``。OpenAPI 3.0 要求这两个字段必须是布尔值，
    实际阈值应分别放在 ``minimum`` 和 ``maximum`` 中。万悟创建自定义工具
    时按 3.0 结构解析，因此这里在导出阶段完成转换。
    """

    resolved = _resolve_schema(value, root)
    if "anyOf" in resolved:
        candidates = [item for item in resolved["anyOf"] if item.get("type") != "null"]
        return _simplify_schema(candidates[0] if candidates else {"type": "string"}, root)
    result: dict[str, Any] = {
        key: item for key, item in resolved.items() if key not in {"title", "additionalProperties"}
    }

    # Pydantic 会把 Literal 单值字段导出为 OpenAPI 3.1 的 ``const``，
    # 但万悟当前的工具解析器不接受 const，且会报 extra sibling fields。
    # 工具节点只需要知道字段的基础类型，因此移除 const 约束即可；
    # 服务端仍会通过 Pydantic response_model 保证实际返回值符合协议。
    result.pop("const", None)

    # Pydantic 2 生成的 OpenAPI 3.1 数值排他边界，需要改写成 OpenAPI 3.0：
    # exclusiveMinimum: 0.0 -> minimum: 0.0, exclusiveMinimum: true
    # 注意 bool 是 int 的子类，因此必须排除 bool，避免重复转换合法的 3.0 写法。
    exclusive_minimum = result.get("exclusiveMinimum")
    if isinstance(exclusive_minimum, (int, float)) and not isinstance(exclusive_minimum, bool):
        result["minimum"] = exclusive_minimum
        result["exclusiveMinimum"] = True

    exclusive_maximum = result.get("exclusiveMaximum")
    if isinstance(exclusive_maximum, (int, float)) and not isinstance(exclusive_maximum, bool):
        result["maximum"] = exclusive_maximum
        result["exclusiveMaximum"] = True

    if result.get("type") == "object":
        result["properties"] = {
            name: _simplify_schema(item, root)
            for name, item in resolved.get("properties", {}).items()
        }
        result["required"] = list(resolved.get("required", []))
    if result.get("type") == "array" and "items" in result:
        result["items"] = _simplify_schema(result["items"], root)
    return result


def _simplify_responses(
    responses: dict[str, Any],
    root: dict[str, Any],
) -> dict[str, Any]:
    """保留响应描述和成功响应的字段结构。

    万悟的自定义工具导入页面不会从 FastAPI 的 ``$ref`` 自动推断输出字段。
    因此这里展开 2xx 响应的 JSON Schema，但只保留必要的字段定义，避免把
    错误响应和内部模型元数据带入工具配置页面。
    """

    simplified: dict[str, Any] = {}
    for status, response in responses.items():
        if isinstance(response, dict):
            item: dict[str, Any] = {
                "description": response.get("description", "请求结果")
            }
            content = response.get("content", {})
            json_content = content.get("application/json", {})
            response_schema = json_content.get("schema")
            # 只展开成功响应，避免 4xx 错误模型成为工作流输出。
            if str(status).startswith("2") and response_schema:
                item["content"] = {
                    "application/json": {
                        "schema": _simplify_schema(response_schema, root)
                    }
                }
            simplified[status] = item
    return simplified or {"200": {"description": "请求成功"}}
