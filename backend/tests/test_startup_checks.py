"""prod 启动断言直测（v4.4.1 安全件）：自相矛盾配置拒绝启动。

口径：validate_prod_settings 只在 app_env=prod 生效；dev 下即使配置矛盾也放行
（本地 HTTP 开发本来就是 secure_cookie=False）。
"""

import pytest

from app.core.config import settings
from app.main import validate_prod_settings


def test_prod_rejects_insecure_cookie(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "prod")
    monkeypatch.setattr(settings, "jwt_secure_cookie", False)
    with pytest.raises(RuntimeError, match="jwt_secure_cookie"):
        validate_prod_settings(settings)


def test_prod_rejects_gate_disabled(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "prod")
    monkeypatch.setattr(settings, "jwt_secure_cookie", True)
    monkeypatch.setattr(settings, "auth_gate_enabled", False)
    with pytest.raises(RuntimeError, match="auth_gate_enabled"):
        validate_prod_settings(settings)


def test_prod_rejects_auto_promote(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "prod")
    monkeypatch.setattr(settings, "jwt_secure_cookie", True)
    monkeypatch.setattr(settings, "auth_gate_enabled", True)
    monkeypatch.setattr(settings, "auto_promote_first_user", True)
    with pytest.raises(RuntimeError, match="auto_promote_first_user"):
        validate_prod_settings(settings)


def test_prod_passes_hardened_config(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "prod")
    monkeypatch.setattr(settings, "jwt_secure_cookie", True)
    monkeypatch.setattr(settings, "auth_gate_enabled", True)
    monkeypatch.setattr(settings, "auto_promote_first_user", False)
    validate_prod_settings(settings)  # 不抛即通过


def test_dev_env_skips_assert(monkeypatch):
    """dev 放行矛盾配置（本地 HTTP 开发 secure=False 是正常态）。"""
    monkeypatch.setattr(settings, "app_env", "dev")
    monkeypatch.setattr(settings, "jwt_secure_cookie", False)
    monkeypatch.setattr(settings, "auth_gate_enabled", True)
    monkeypatch.setattr(settings, "auto_promote_first_user", True)
    validate_prod_settings(settings)  # 不抛
