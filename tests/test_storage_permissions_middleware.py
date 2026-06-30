from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import Request, Response
from sqlalchemy.ext.asyncio import AsyncSession


class BlobDownload:
    def __init__(self, content: bytes) -> None:
        self.content = content

    def readall(self) -> bytes:
        return self.content


class Blob:
    def __init__(self, content: bytes = b"content") -> None:
        self.content = content
        self.url = "https://storage/blob"
        self.uploaded = None
        self.deleted = False

    def upload_blob(self, data, **kwargs) -> None:
        self.uploaded = data

    def download_blob(self) -> BlobDownload:
        return BlobDownload(self.content)

    def delete_blob(self) -> None:
        self.deleted = True


class Container:
    def __init__(self, blob: Blob, exists: bool = True) -> None:
        self.blob = blob
        self.exists = exists
        self.created = False

    def get_container_properties(self):
        if not self.exists:
            raise RuntimeError("missing")

    def create_container(self) -> None:
        self.created = True

    def get_blob_client(self, *args) -> Blob:
        return self.blob


class BlobService:
    account_name = "account"
    credential = SimpleNamespace(account_key="key")

    def __init__(self, blob: Blob, exists: bool = True) -> None:
        self.container = Container(blob, exists)

    def get_container_client(self, name) -> Container:
        return self.container

    def get_blob_client(self, *args) -> Blob:
        return self.container.blob

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None


def test_blob_storage_upload_download_sas_delete(monkeypatch: pytest.MonkeyPatch) -> None:
    from azure.storage import blob as azure_blob

    from app.storage.blob import BlobStorageClient

    blob = Blob()
    service = BlobService(blob, exists=False)
    client = BlobStorageClient()
    client._service_client = cast(Any, service)
    assert client.upload_file("files", "a.txt", b"hello", "text/plain") == blob.url
    assert service.container.created and blob.uploaded == b"hello"
    monkeypatch.setattr(azure_blob, "generate_blob_sas", lambda **kwargs: "sas-token")
    assert client.generate_download_url("files", "a.txt") == f"{blob.url}?sas-token"
    assert client.download_blob("files", "a.txt") == b"content"
    client.delete_blob("files", "a.txt")
    assert blob.deleted


def test_champion_and_batch_blob_readers(monkeypatch: pytest.MonkeyPatch) -> None:
    from azure.storage.blob import BlobServiceClient

    from app.storage import batch_blob, champion_blob

    champion = Blob(b'{"models": [{"version": "1"}]}')
    service = BlobService(champion)
    monkeypatch.setattr(BlobServiceClient, "from_connection_string", lambda value: service)
    monkeypatch.setattr(champion_blob, "_get_connection_string", lambda: "connection")
    assert champion_blob.read_champion_metadata() == {"models": [{"version": "1"}]}

    batch = Blob(b'{"status":"complete"}')
    service.container.blob = batch
    monkeypatch.setattr(batch_blob, "_get_connection_string", lambda: "connection")
    assert batch_blob.read_batch_status("BATCH-1") == {"status": "complete"}
    batch.content = b'{"request_id":"1"}\nnot-json\n\n{"request_id":"2"}'
    assert list(batch_blob.stream_batch_results("batch/results.jsonl")) == [
        {"request_id": "1"},
        {"request_id": "2"},
    ]
    monkeypatch.setattr(batch_blob, "_get_connection_string", lambda: None)
    assert batch_blob.read_batch_status("BATCH-1") is None
    assert list(batch_blob.stream_batch_results("path")) == []


class ScalarCollection:
    def __init__(self, values) -> None:
        self.values = values

    def scalars(self):
        return self

    def all(self):
        return self.values

    def scalar_one_or_none(self):
        return self.values[0] if self.values else None


class PermissionDB:
    def __init__(self, *results) -> None:
        self.results = list(results)

    async def execute(self, statement):
        return ScalarCollection(self.results.pop(0))


@pytest.mark.asyncio
async def test_permission_loading_all_caller_types(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.dependencies import AuthContext
    from app.services import permissions

    cached = AuthContext(user_type="admin")
    cached.permissions = {"cached"}
    assert await permissions.load_user_permissions(cast(AsyncSession, object()), cached) == {
        "cached"
    }

    monkeypatch.setattr(permissions, "_admin_permissions", AsyncMock(return_value={"admin.read"}))
    monkeypatch.setattr(permissions, "_org_user_permissions", AsyncMock(return_value={"org.read"}))
    monkeypatch.setattr(permissions, "_api_key_permissions", AsyncMock(return_value={"api.read"}))
    assert await permissions.load_user_permissions(
        cast(AsyncSession, object()), AuthContext(user_id=uuid4(), user_type="admin")
    ) == {"admin.read"}
    assert await permissions.load_user_permissions(
        cast(AsyncSession, object()), AuthContext(user_id=uuid4(), user_type="org_user")
    ) == {"org.read"}
    api = AuthContext(user_type="api_key", api_key=cast(Any, SimpleNamespace(role_id=None)))
    assert await permissions.load_user_permissions(cast(AsyncSession, object()), api) == {
        "api.read"
    }
    assert (
        await permissions.load_user_permissions(cast(AsyncSession, object()), AuthContext())
        == set()
    )


@pytest.mark.asyncio
async def test_permission_role_resolution() -> None:
    from app.services.permission_seeder import ALL_ORG_PERMISSION_CODES
    from app.services.permissions import (
        _admin_permissions,
        _api_key_permissions,
        _org_user_permissions,
        _permissions_for_roles,
    )

    super_role = SimpleNamespace(id=uuid4(), is_system=True, name="SUPER_ADMIN")
    super_permissions = await _admin_permissions(
        cast(AsyncSession, PermissionDB([super_role])), uuid4()
    )
    assert super_permissions == {"*"}
    admin_role = SimpleNamespace(id=uuid4(), is_system=True, name="ADMIN")
    assert await _org_user_permissions(
        cast(AsyncSession, PermissionDB([admin_role])), uuid4()
    ) == set(ALL_ORG_PERMISSION_CODES)
    assert await _api_key_permissions(
        cast(AsyncSession, PermissionDB([])), SimpleNamespace(role_id=None)
    ) == set(ALL_ORG_PERMISSION_CODES)
    assert await _permissions_for_roles(cast(AsyncSession, object()), []) == set()
    assert await _permissions_for_roles(
        cast(AsyncSession, PermissionDB(["one", "two"])), [uuid4()]
    ) == {"one", "two"}


@pytest.mark.asyncio
async def test_request_id_middleware_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.middleware import RequestIdMiddleware

    middleware = RequestIdMiddleware(cast(Any, lambda scope: None))
    persist_metric = AsyncMock()
    persist_log = AsyncMock()
    monkeypatch.setattr(middleware, "_persist_request_metric", persist_metric)
    monkeypatch.setattr(middleware, "_persist_api_log", persist_log)
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/v1/test",
        "headers": [(b"x-request-id", b"req-1")],
        "query_string": b"",
        "server": ("test", 80),
        "client": ("127.0.0.1", 1234),
        "scheme": "http",
    }
    request = Request(scope)

    async def call_next(request: Request) -> Response:
        return Response("ok", status_code=200)

    response = await middleware.dispatch(request, call_next)
    assert response.headers["X-Request-ID"] == "req-1"
    assert "X-Process-Time" in response.headers
    persist_metric.assert_awaited_once()
    persist_log.assert_awaited_once()


@pytest.mark.asyncio
async def test_middleware_persists_metric_and_authenticated_api_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.db
    from app.core import middleware as middleware_module

    class Session:
        def __init__(self) -> None:
            self.added: list[Any] = []
            self.committed = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        def add(self, value) -> None:
            self.added.append(value)

        async def commit(self) -> None:
            self.committed += 1

    sessions = [Session(), Session()]
    monkeypatch.setattr(app.db, "async_session_factory", lambda: sessions.pop(0))
    middleware = middleware_module.RequestIdMiddleware(cast(Any, lambda scope: None))
    req = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/scores",
            "headers": [(b"user-agent", b"pytest")],
            "query_string": b"",
            "server": ("test", 80),
            "client": ("127.0.0.1", 1),
            "scheme": "http",
        }
    )
    req.state.organization_id = uuid4()
    req.state.user_id = uuid4()
    req.state.api_key_id = None
    req.state.api_key_prefix = None
    response = Response(status_code=500)
    await middleware._persist_request_metric(req, response, "req-1", 12)
    await middleware._persist_api_log(req, response, "req-1", 12)
    assert not sessions  # both isolated sessions were used


def test_monitoring_math_helpers_and_thresholds() -> None:
    from app.config.thresholds import check_threshold
    from app.jobs.drift_calculator import _psi, _score_distribution, _shap_distribution
    from app.services.metrics_aggregator import (
        _build_score_distribution,
        _compute_percentiles,
        _period_start,
        _safe_pct,
    )

    assert check_threshold("api_latency_p99", 1001) == "firing"
    assert check_threshold("data_quality_score_p25", 0.5) == "firing"
    assert check_threshold("unknown", 999) == "ok"
    assert _safe_pct(1, 4) == 25.0 and _safe_pct(1, 0) == 0.0
    assert _period_start("1h") > _period_start("unknown")
    assert _compute_percentiles([]) == (0.0, 0.0, 0.0)
    assert _compute_percentiles([1, 2, 3, 4]) == (3.0, 4.0, 4.0)
    distribution = _build_score_distribution([350, 650, 750])
    assert distribution["mean"] > 0 and len(distribution["buckets"]) == 6
    assert _build_score_distribution([])["buckets"] == []
    assert sum(_score_distribution([350, 450, 550, 650, 750, 850])) == 1.0
    assert sum(_shap_distribution([-0.4, -0.2, -0.05, 0.05, 0.2, 0.4])) == 1.0
    assert _psi([0.5, 0.5], [0.5, 0.5]) == 0.0


@pytest.mark.asyncio
async def test_health_and_readiness_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.routers import health

    assert (await health.health_check()).status == "ok"

    class Connection:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def execute(self, statement):
            return None

    monkeypatch.setattr(health, "engine", SimpleNamespace(connect=lambda: Connection()))
    monkeypatch.setattr(
        health, "get_redis_cache", lambda: SimpleNamespace(ping=AsyncMock(return_value=True))
    )
    response = Response()
    ready = await health.readiness_check(response)
    assert ready.status == "ok" and ready.checks == {"postgres": "ok", "redis": "ok"}
    assert response.status_code == 200

    monkeypatch.setattr(
        health, "get_redis_cache", lambda: SimpleNamespace(ping=AsyncMock(return_value=False))
    )
    degraded_response = Response()
    degraded = await health.readiness_check(degraded_response)
    assert degraded.status == "degraded"
    assert degraded_response.status_code == 503


def test_demo_simulation_lookup() -> None:
    from app.demo.simulation_records import (
        SIMULATION_RECORDS,
        all_records,
        get_outcome_by_consumer_id,
        get_simulation_report,
    )

    record = SIMULATION_RECORDS[0]
    assert get_simulation_report(f" {record['identification']} ")
    assert get_simulation_report(None, record["phone"])
    assert get_simulation_report("unknown") is None
    outcome = get_outcome_by_consumer_id(record["identification"])
    assert outcome is not None and outcome["decision"]
    assert get_outcome_by_consumer_id(None) is None
    assert len(all_records()) == len(SIMULATION_RECORDS)
