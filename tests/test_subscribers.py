from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest


class ScalarResult:
    def __init__(self, value) -> None:
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def all(self):
        return self.value


class AsyncContext:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None


class FakeSession(AsyncContext):
    def __init__(self, results=None) -> None:
        self.results = list(results or [])
        self.added: list[Any] = []

    def begin(self):
        return AsyncContext()

    async def execute(self, statement):
        return ScalarResult(self.results.pop(0) if self.results else None)

    def add(self, value) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        for value in self.added:
            if getattr(value, "id", None) is None:
                value.id = uuid4()

    async def commit(self) -> None:
        return None


class SessionFactory:
    def __init__(self, *sessions: FakeSession) -> None:
        self.sessions = list(sessions)

    def __call__(self):
        return self.sessions.pop(0)


@pytest.mark.asyncio
async def test_ingestion_subscriber_validation_and_update(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.models.data_source import UploadStatus
    from app.subscribers import ingestion

    await ingestion.handle_data_ingested({})
    await ingestion.handle_data_ingested({"training_upload_id": "u"})
    await ingestion.handle_data_ingested({"training_upload_id": "u", "status": "UNKNOWN"})

    upload = SimpleNamespace()
    session = FakeSession([upload])
    monkeypatch.setattr(ingestion, "async_session_factory", SessionFactory(session))
    template_id = uuid4()
    await ingestion.handle_data_ingested(
        {
            "training_upload_id": "u",
            "status": "TRANSFORMED",
            "quality_report": {"ok": True},
            "quality_score": 0.9,
            "transformed_file_path": "clean/file.csv",
            "features_mapped": 18,
            "error_report": "warning",
            "schema_template_id": str(template_id),
        }
    )
    assert upload.status == UploadStatus.TRANSFORMED
    assert upload.quality_report == {"ok": True}
    assert upload.mapping_template_id == template_id
    assert upload.completed_at is not None


@pytest.mark.asyncio
async def test_training_started_subscriber(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.models.data_source import UploadStatus
    from app.subscribers import training_started

    await training_started.handle_model_training_started({})
    missing = FakeSession([None])
    monkeypatch.setattr(training_started, "async_session_factory", SessionFactory(missing))
    await training_started.handle_model_training_started({"training_upload_id": "missing"})

    upload = SimpleNamespace()
    session = FakeSession([upload])
    monkeypatch.setattr(training_started, "async_session_factory", SessionFactory(session))
    await training_started.handle_model_training_started(
        {
            "training_upload_id": "u",
            "training_id": "TRAIN-1",
            "record_count": 1000,
            "models_to_train": ["credit_risk"],
        }
    )
    assert upload.status == UploadStatus.TRAINING
    assert upload.training_id == "TRAIN-1"
    assert upload.record_count == 1000


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "all_succeeded", "expected"),
    [
        ("COMPLETE", True, "trained"),
        ("FAILED", False, "error"),
        ("COMPLETE", False, "trained"),
    ],
)
async def test_training_completed_subscriber(
    status: str,
    all_succeeded: bool,
    expected: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.subscribers import training

    upload = SimpleNamespace()
    session = FakeSession([None, upload])
    monkeypatch.setattr(training, "async_session_factory", SessionFactory(session))
    await training.handle_model_training_completed(
        {
            "training_upload_id": "u",
            "training_id": "TRAIN-1",
            "status": status,
            "all_succeeded": all_succeeded,
            "training_duration_seconds": 12,
            "models_trained": {
                "credit_risk": {
                    "status": "success",
                    "model_version": "v1",
                    "metrics": {"auc": 0.91},
                },
                "ignored": "not-a-dict",
            },
            "dataset_info": {"errors": ["invalid rows"]},
        }
    )
    assert upload.status.value == expected
    assert upload.training_id == "TRAIN-1"
    assert len(session.added) == 2  # model version + training metric


@pytest.mark.asyncio
async def test_scoring_complete_subscriber_updates_request_and_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.jobs import alert_evaluator, drift_calculator
    from app.models.score_request import ScoreRequestStatus
    from app.subscribers import scoring_complete

    await scoring_complete.handle_scoring_complete({})
    await scoring_complete.handle_scoring_complete({"request_id": "SCR-1"})
    await scoring_complete.handle_scoring_complete({"request_id": "SCR-1", "decision": "BAD"})

    score_request = SimpleNamespace(
        id=uuid4(),
        callback_url=None,
        tracking_id="SCR-1",
        organization_id=uuid4(),
    )
    main_session = FakeSession([None, score_request])
    metrics_session = FakeSession()
    monkeypatch.setattr(
        scoring_complete,
        "async_session_factory",
        SessionFactory(main_session, metrics_session),
    )
    for module, names in (
        (drift_calculator, ("compute_score_psi", "compute_feature_drift")),
        (alert_evaluator, ("evaluate_risk_alerts", "evaluate_model_alerts")),
    ):
        for name in names:
            monkeypatch.setattr(module, name, AsyncMock())

    await scoring_complete.handle_scoring_complete(
        {
            "request_id": "SCR-1",
            "decision": "APPROVE",
            "scoring_timestamp": "not-a-date",
            "credit_risk": {
                "risk_tier": "LOW",
                "model_version": "v1",
                "pd_confidence": 0.9,
                "probability_of_default": 0.05,
                "shap_contributions": [{"feature": "age", "value": 0.1}],
            },
            "fraud_detection": {"fraud_risk_flag": "LOW"},
            "loan_amount": {"recommended_loan_tier": "MEDIUM"},
            "scoring_metadata": {"credit_score": 780, "score_grade": "A"},
        }
    )
    assert score_request.status == ScoreRequestStatus.COMPLETED
    assert score_request.score_value == 780
    assert score_request.risk_category == "LOW"
    assert len(main_session.added) == 2  # scoring result + decision metric


@pytest.mark.asyncio
async def test_batch_complete_updates_and_dispatches_ingestion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.models.bulk_job import BulkJobStatus, IngestionStatus
    from app.subscribers import batch_complete

    await batch_complete.handle_batch_score_complete({})
    job = SimpleNamespace(status=BulkJobStatus.PROCESSING)
    session = FakeSession([job])
    ingest = AsyncMock()
    monkeypatch.setattr(batch_complete, "async_session_factory", SessionFactory(session))
    monkeypatch.setattr(batch_complete, "_ingest_results", ingest)
    await batch_complete.handle_batch_score_complete(
        {
            "job_id": "BATCH-1",
            "completed_at": "bad-date",
            "succeeded": 2,
            "failed": 1,
            "results_blob_path": "batch/results.jsonl",
        }
    )
    assert job.status == BulkJobStatus.COMPLETED
    assert job.ingestion_status == IngestionStatus.INGESTING
    assert job.processed_records == 2 and job.error_count == 1
    ingest.assert_awaited_once_with("BATCH-1", "batch/results.jsonl")


@pytest.mark.asyncio
async def test_batch_result_ingestion_valid_invalid_duplicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.models.bulk_job import IngestionStatus
    from app.storage import batch_blob
    from app.subscribers import batch_complete

    job = SimpleNamespace(id=uuid4(), ingested_count=0, ingestion_status=None)
    fetch_session = FakeSession([job])
    first_result = FakeSession([None])
    duplicate_result = FakeSession([uuid4()])
    update_session = FakeSession([job])
    monkeypatch.setattr(
        batch_complete,
        "async_session_factory",
        SessionFactory(fetch_session, first_result, duplicate_result, update_session),
    )
    monkeypatch.setattr(
        batch_blob,
        "stream_batch_results",
        lambda path: iter(
            [
                {},
                {"request_id": "bad", "decision": "UNKNOWN"},
                {
                    "request_id": "SCR-1",
                    "decision": "APPROVE",
                    "scoring_timestamp": "bad-date",
                    "credit_risk": {"risk_tier": "LOW"},
                },
                {"request_id": "SCR-2", "decision": "DECLINE"},
            ]
        ),
    )
    await batch_complete._ingest_results("BATCH-1", "batch/results.jsonl")
    assert len(first_result.added) == 1
    assert job.ingested_count == 2
    assert job.ingestion_status == IngestionStatus.COMPLETE


@pytest.mark.asyncio
async def test_batch_worker_demo_simulation_completes_without_external_services(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.db
    from app.config import get_settings
    from app.models.batch_job_item import BatchJobItemStatus
    from app.models.bulk_job import BulkJobStatus
    from app.workers import batch_worker

    item = SimpleNamespace(
        id=uuid4(),
        batch_job_id=uuid4(),
        index=0,
        status=BatchJobItemStatus.PENDING,
        payload={
            "full_name": "Kofi Owusu",
            "date_of_birth": "1966-12-15",
            "identification": "FUW3142495073",
        },
        started_at=None,
        completed_at=None,
        score_request_id=None,
        score_tracking_id=None,
    )
    job = SimpleNamespace(
        id=item.batch_job_id,
        job_id="BATCH-DEMO",
        organization_id=uuid4(),
        created_by_user_id=uuid4(),
        status=BulkJobStatus.QUEUED,
        started_at=None,
        completed_at=None,
        processed_records=0,
        error_count=0,
        total_records=1,
    )
    session = FakeSession(
        [
            item,
            job,
            [(BatchJobItemStatus.COMPLETED, 1)],
        ]
    )
    monkeypatch.setattr(app.db, "async_session_factory", SessionFactory(session))
    monkeypatch.setattr(get_settings(), "demo_simulation_mode", True)

    async def no_sleep(delay: float) -> None:
        return None

    monkeypatch.setattr(batch_worker.asyncio, "sleep", no_sleep)
    await batch_worker._process_item(str(item.id))
    assert item.status == BatchJobItemStatus.COMPLETED
    assert item.score_tracking_id.startswith("SCR-")
    assert job.status == BulkJobStatus.COMPLETED
    assert job.processed_records == 1
    assert len(session.added) == 2  # ScoreRequest + ScoringResult
