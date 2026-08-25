"""账号密码与登录令牌的最小安全实现。

项目不保存明文密码，也不把可直接使用的登录令牌写入数据库。密码采用带随机盐的
PBKDF2-HMAC-SHA256，登录令牌使用系统安全随机数生成，数据库只保存令牌摘要。
当前实现不依赖第三方认证服务，便于校赛离线演示；接入企业统一身份平台时可替换本模块。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

PASSWORD_ITERATIONS = 390_000


def hash_password(password: str) -> str:
    """为新密码生成不可逆摘要；空密码在进入本函数前应被拒绝。"""

    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PASSWORD_ITERATIONS,
    )
    return "pbkdf2_sha256${}${}${}".format(
        PASSWORD_ITERATIONS,
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(digest).decode("ascii"),
    )


def verify_password(password: str, encoded: str) -> bool:
    """以常量时间比较密码摘要，格式损坏时直接返回失败。"""

    try:
        algorithm, raw_iterations, raw_salt, raw_digest = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        expected = base64.urlsafe_b64decode(raw_digest.encode("ascii"))
        actual = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            base64.urlsafe_b64decode(raw_salt.encode("ascii")),
            int(raw_iterations),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def generate_session_token() -> str:
    """生成只返回给浏览器一次的高熵登录令牌。"""

    return secrets.token_urlsafe(32)


def hash_session_token(token: str) -> str:
    """令牌摘要用于数据库查询，数据库泄露时不能直接冒用会话。"""

    return hashlib.sha256(token.encode("utf-8")).hexdigest()
