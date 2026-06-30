from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import Request
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession


class Result:
    def __init__(self, value) -> None:
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalars(self):
        return self

    def all(self):
        return self.value


class DB:
    def __init__(self, *values) -> None:
        self.values = list(values)

    async def execute(self, statement):
        return Result(self.values.pop(0))


def credential(value: str = "token") -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=value)


@pytest.mark.asyncio
async def test_admin_and_org_authentication(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import dependencies
    from app.core.exceptions import AuthenticationError, OrganizationSuspendedError
    from app.models.organization import OrgStatus
    from app.models.user import AdminUserStatus, OrgUserStatus

    with pytest.raises(AuthenticationError):
        await dependencies.get_current_admin(None, cast(AsyncSession, DB()))
    monkeypatch.setattr(
        dependencies,
        "decode_token",
        lambda token: {"type": "access", "user_type": "admin", "sub": "a"},
    )
    admin = SimpleNamespace(status=AdminUserStatus.ACTIVE)
    assert (
        await dependencies.get_current_admin(credential(), cast(AsyncSession, DB(admin))) is admin
    )
    with pytest.raises(AuthenticationError):
        await dependencies.get_current_admin(credential(), cast(AsyncSession, DB(None)))

    monkeypatch.setattr(
        dependencies,
        "decode_token",
        lambda token: {"type": "access", "user_type": "org_user", "sub": "u"},
    )
    org_user = SimpleNamespace(status=OrgUserStatus.ACTIVE, organization_id=uuid4())
    active_org = SimpleNamespace(status=OrgStatus.ACTIVE)
    assert (
        await dependencies.get_current_org_user(
            credential(), cast(AsyncSession, DB(org_user, active_org))
        )
        is org_user
    )
    suspended = SimpleNamespace(status=OrgStatus.SUSPENDED)
    with pytest.raises(OrganizationSuspendedError):
        await dependencies.get_current_org_user(
            credential(), cast(AsyncSession, DB(org_user, suspended))
        )


@pytest.mark.asyncio
async def test_get_current_user_both_types_and_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import dependencies
    from app.core.exceptions import AuthenticationError
    from app.models.organization import OrgStatus
    from app.models.user import AdminUserStatus, OrgUserStatus

    admin = SimpleNamespace(status=AdminUserStatus.ACTIVE)
    monkeypatch.setattr(
        dependencies,
        "decode_token",
        lambda token: {"type": "access", "user_type": "admin", "sub": "a"},
    )
    assert await dependencies.get_current_user(credential(), cast(AsyncSession, DB(admin))) is admin

    user = SimpleNamespace(status=OrgUserStatus.ACTIVE, organization_id=uuid4())
    org = SimpleNamespace(status=OrgStatus.ACTIVE)
    monkeypatch.setattr(
        dependencies,
        "decode_token",
        lambda token: {"type": "access", "user_type": "org_user", "sub": "u"},
    )
    assert (
        await dependencies.get_current_user(credential(), cast(AsyncSession, DB(user, org))) is user
    )
    monkeypatch.setattr(dependencies, "decode_token", lambda token: {"type": "refresh"})
    with pytest.raises(AuthenticationError):
        await dependencies.get_current_user(credential(), cast(AsyncSession, DB()))


def request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/v1/test",
            "headers": [],
            "query_string": b"",
            "server": ("test", 80),
            "client": ("127.0.0.1", 1),
            "scheme": "http",
        }
    )


@pytest.mark.asyncio
async def test_auth_context_jwt_admin_org_and_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import dependencies
    from app.models.api_key import ApiKeyStatus
    from app.models.organization import OrgStatus
    from app.models.user import AdminUserStatus, OrgRoleLabel, OrgUserStatus

    org = SimpleNamespace(id=uuid4(), status=OrgStatus.ACTIVE)
    user = SimpleNamespace(
        id=uuid4(),
        email="u@example.com",
        status=OrgUserStatus.ACTIVE,
        organization_id=org.id,
        role_label=OrgRoleLabel.CREDIT_OFFICER,
    )
    monkeypatch.setattr(
        dependencies,
        "decode_token",
        lambda token: {"type": "access", "user_type": "org_user", "sub": str(user.id)},
    )
    context = await dependencies.get_auth_context(
        request(), credential(), cast(AsyncSession, DB(user, org))
    )
    assert context.user_type == "org_user" and context.organization is org

    admin = SimpleNamespace(
        id=uuid4(), email="a@example.com", status=AdminUserStatus.ACTIVE, role=None
    )
    monkeypatch.setattr(
        dependencies,
        "decode_token",
        lambda token: {"type": "access", "user_type": "admin", "sub": str(admin.id)},
    )
    context = await dependencies.get_auth_context(
        request(), credential(), cast(AsyncSession, DB(admin))
    )
    assert context.user_type == "admin"

    def invalid_token(token: str) -> dict[str, Any]:
        raise ValueError("not a JWT")

    monkeypatch.setattr(dependencies, "decode_token", invalid_token)
    api_key = SimpleNamespace(
        id=uuid4(),
        status=ApiKeyStatus.ACTIVE,
        organization_id=org.id,
        key_prefix="ps_live_x",
        last_used_at=None,
    )
    context = await dependencies.get_auth_context(
        request(), credential("api-key"), cast(AsyncSession, DB(api_key, org))
    )
    assert context.user_type == "api_key" and api_key.last_used_at is not None


@pytest.mark.asyncio
async def test_rate_limit_and_permission_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import dependencies
    from app.core.exceptions import AuthorizationError

    auth = dependencies.AuthContext(user_id=uuid4(), user_type="org_user", organization_id=uuid4())
    fake_cache = SimpleNamespace(get_client=AsyncMock(return_value=object()))
    limiter = SimpleNamespace(check_rate_limit=AsyncMock(return_value={"remaining": 9}))
    monkeypatch.setattr(dependencies, "get_redis_cache", lambda: fake_cache)
    monkeypatch.setattr(dependencies, "RateLimiter", lambda client: limiter)
    req = request()
    assert await dependencies.check_rate_limit(auth, req) is auth
    assert req.state.rate_limit_headers == {"remaining": 9}
    assert req.state.organization_id == auth.organization_id

    from app.services import permissions

    monkeypatch.setattr(permissions, "load_user_permissions", AsyncMock(return_value={"allowed"}))
    check = dependencies.require_permission("allowed")
    assert await check(auth, cast(AsyncSession, DB())) is auth
    denied = dependencies.require_permission("denied")
    with pytest.raises(AuthorizationError):
        await denied(auth, cast(AsyncSession, DB()))
    any_check = dependencies.require_any_permission("other", "allowed")
    assert await any_check(auth, cast(AsyncSession, DB())) is auth


@pytest.mark.asyncio
async def test_api_log_actor_enrichment() -> None:
    from app.services.api_log_enrichment import pick_actor, resolve_actors

    admin_id, org_id, key_id = uuid4(), uuid4(), uuid4()
    logs = [
        SimpleNamespace(user_id=admin_id, api_key_id=None, organization_id=None),
        SimpleNamespace(user_id=org_id, api_key_id=None, organization_id=uuid4()),
        SimpleNamespace(user_id=None, api_key_id=key_id, organization_id=uuid4()),
    ]
    admin = SimpleNamespace(id=admin_id, name="Admin", email="a@example.com")
    user = SimpleNamespace(id=org_id, name="Officer", email="u@example.com")
    key = SimpleNamespace(id=key_id, name="Integration")
    actors = await resolve_actors(cast(AsyncSession, DB([admin], [user], [key])), logs)
    assert actors[str(admin_id)].type == "admin_user"
    assert actors[str(org_id)].type == "org_user"
    assert actors[str(key_id)].type == "api_key"
    actor = pick_actor(logs[2], actors)
    assert actor is not None and actor.name == "Integration"
    assert pick_actor(SimpleNamespace(api_key_id=None, user_id=None), actors) is None
