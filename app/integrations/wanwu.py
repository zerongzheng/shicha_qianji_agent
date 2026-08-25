"""元景万悟文件输入适配器。

万悟当前 OpenAPI 工具执行器擅长发送 JSON，但 multipart/form-data 会被转换为普通文本字段，
无法像浏览器那样上传真实二进制文件。因此这里支持两种 JSON 友好的输入：

1. 平台文件节点返回的可下载 URL；
2. 小文件使用 Base64 文本直接传递。

URL 下载默认禁止访问本机、局域网、链路本地和保留地址，防止该接口被利用去探测部署机器
内部网络。服务器部署时应使用 ``WANWU_ALLOWED_FILE_HOSTS`` 精确放行万悟文件服务，
只有完全受控的临时联调环境才应开启 ``WANWU_ALLOW_PRIVATE_FILE_URLS``。
"""

from __future__ import annotations

import base64
import binascii
import ipaddress
import socket
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener


@dataclass(frozen=True)
class IncomingCsv:
    """已经完成校验、可安全落盘的 CSV 内容。"""

    file_name: str
    content: bytes
    source_type: str


def receive_wanwu_csv(
    *,
    file_url: str | None,
    file_base64: str | None,
    requested_file_name: str | None,
    max_bytes: int,
    download_timeout: float,
    allow_private_urls: bool,
    allowed_private_hosts: tuple[str, ...] = (),
) -> IncomingCsv:
    """读取万悟传来的唯一文件来源，并返回统一二进制结果。"""

    if file_url:
        content, url_file_name = _download_file(
            file_url,
            max_bytes=max_bytes,
            timeout=download_timeout,
            allow_private_urls=allow_private_urls,
            allowed_private_hosts=allowed_private_hosts,
        )
        file_name = _safe_csv_name(requested_file_name or url_file_name)
        return IncomingCsv(file_name=file_name, content=content, source_type="url")

    if file_base64:
        content = _decode_base64(file_base64, max_bytes=max_bytes)
        file_name = _safe_csv_name(requested_file_name or "wanwu_upload.csv")
        return IncomingCsv(file_name=file_name, content=content, source_type="base64")

    raise ValueError("必须提供 file_url 或 file_base64")


def _decode_base64(raw_value: str, max_bytes: int) -> bytes:
    """解码普通 Base64 或 ``data:text/csv;base64,...`` 数据 URI。"""

    encoded = raw_value.strip()
    if encoded.startswith("data:"):
        header, separator, encoded = encoded.partition(",")
        if not separator or ";base64" not in header.lower():
            raise ValueError("CSV 数据 URI 必须使用 base64 编码")
    try:
        content = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("file_base64 不是合法 Base64 内容") from exc
    _check_size(content, max_bytes)
    if not content:
        raise ValueError("CSV 文件内容不能为空")
    return content


def _download_file(
    raw_url: str,
    *,
    max_bytes: int,
    timeout: float,
    allow_private_urls: bool,
    allowed_private_hosts: tuple[str, ...],
) -> tuple[bytes, str]:
    """限制协议、目标地址、重定向和响应大小后下载文件。"""

    _validate_remote_url(
        raw_url,
        allow_private_urls=allow_private_urls,
        allowed_private_hosts=allowed_private_hosts,
    )
    opener = build_opener(
        _SafeRedirectHandler(allow_private_urls, allowed_private_hosts)
    )
    request = Request(
        raw_url,
        headers={"User-Agent": "shicha-qianji-wanwu-adapter/1.0"},
    )
    try:
        with opener.open(request, timeout=timeout) as response:
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > max_bytes:
                raise ValueError(f"远程文件超过 {max_bytes} 字节限制")
            content = response.read(max_bytes + 1)
            final_url = response.geturl()
    except HTTPError as exc:
        raise ValueError(f"下载 CSV 失败，远程服务返回 HTTP {exc.code}") from exc
    except URLError as exc:
        raise ValueError(f"下载 CSV 失败：{exc.reason}") from exc
    _check_size(content, max_bytes)
    if not content:
        raise ValueError("远程 CSV 文件内容为空")
    file_name = Path(unquote(urlparse(final_url).path)).name or "wanwu_download.csv"
    return content, file_name


class _SafeRedirectHandler(HTTPRedirectHandler):
    """每次 HTTP 重定向都重新执行地址安全校验。"""

    def __init__(
        self,
        allow_private_urls: bool,
        allowed_private_hosts: tuple[str, ...],
    ) -> None:
        super().__init__()
        self.allow_private_urls = allow_private_urls
        self.allowed_private_hosts = allowed_private_hosts

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_remote_url(
            newurl,
            allow_private_urls=self.allow_private_urls,
            allowed_private_hosts=self.allowed_private_hosts,
        )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _validate_remote_url(
    raw_url: str,
    *,
    allow_private_urls: bool,
    allowed_private_hosts: tuple[str, ...] = (),
) -> None:
    """拒绝非 HTTP 协议以及默认不允许访问的内部网络地址。"""

    parsed = urlparse(raw_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("file_url 只能使用有效的 http 或 https 地址")
    if parsed.username or parsed.password:
        raise ValueError("file_url 不允许携带 URL 用户名或密码")
    if allow_private_urls:
        return
    normalized_host = parsed.hostname.casefold().rstrip(".")
    host_is_allowed = normalized_host in {
        host.casefold().rstrip(".") for host in allowed_private_hosts
    }
    try:
        addresses = {
            item[4][0]
            for item in socket.getaddrinfo(parsed.hostname, parsed.port or 443)
        }
    except socket.gaierror as exc:
        raise ValueError("file_url 域名无法解析") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global and not host_is_allowed:
            raise ValueError(
                "file_url 默认禁止访问本机、局域网或保留网络地址；"
                "服务器联调请配置 WANWU_ALLOWED_FILE_HOSTS"
            )


def _safe_csv_name(raw_name: str) -> str:
    """移除目录部分并强制 CSV 后缀，防止文件名逃逸上传目录。"""

    file_name = Path(raw_name.strip()).name
    if not file_name or not file_name.lower().endswith(".csv"):
        raise ValueError("万悟输入文件必须使用 .csv 后缀")
    return file_name


def _check_size(content: bytes, max_bytes: int) -> None:
    """统一限制 URL 和 Base64 两种入口的解码后文件大小。"""

    if len(content) > max_bytes:
        raise ValueError(f"CSV 文件超过 {max_bytes} 字节限制")
