"""Seed rich, realistic demo data for a business demo (no AI / no live scoring).

This populates Postgres so every read-only dashboard renders full data:
overview stats, score history, risk / compliance / infrastructure / model-ops
monitoring, batch jobs, alerts, audit logs, org portal (keys, webhooks, users).

It writes ONLY through the ORM models, so column names and enum encodings
always match the live schema. Every enum value / "test outcome" is represented
at least once (all decisions, all statuses, all model lifecycle states, all
loan-performance states, all alert severities, etc.).

Usage:
    # bring up local Postgres first:  docker compose up -d postgres
    # apply schema:                   alembic upgrade head
    python scripts/seed_demo_data.py            # seed an empty DB
    python scripts/seed_demo_data.py --reset    # wipe demo tables, then seed
    python scripts/seed_demo_data.py --scale small   # smaller dataset

The same script runs against Railway: point DATABASE_URL at the Railway
Postgres, run `alembic upgrade head`, then run this with --reset.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import random
import sys
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

sys.path.append(os.getcwd())

from sqlalchemy import text  # noqa: E402

from app.core.security import hash_password  # noqa: E402
from app.db import async_session_factory  # noqa: E402
from app.models.alert import Alert  # noqa: E402
from app.models.api_key import ApiKey, ApiKeyEnvironment, ApiKeyStatus  # noqa: E402
from app.models.api_log import ApiLog  # noqa: E402
from app.models.audit import AuditLog  # noqa: E402
from app.models.batch_job_item import BatchJobItem, BatchJobItemStatus  # noqa: E402
from app.models.bulk_job import BulkJobStatus, BulkScoringJob, IngestionStatus  # noqa: E402

# Training-data models (admin Data Sources / Datasets screens) + FK registration.
from app.models.data_source import (  # noqa: E402, F401
    ConfidenceLevel,
    DataSource,
    FieldMapping,
    FileFormat,
    MappingSource,
    MappingTemplate,
    TrainingUpload,
    UploadStatus,
)
from app.models.data_source import SourceType as DSSourceType  # noqa: E402
from app.models.decision_metric import DecisionMetric  # noqa: E402
from app.models.model_metric import ModelMetric  # noqa: E402
from app.models.model_version import (  # noqa: E402
    ModelStatus,
    ModelVersion,
    SourceType,
    TrainingDataSource,
    TrainingDataStatus,
)

# ── Models ──────────────────────────────────────────────────────────────────
from app.models.organization import IndustryType, Organization, OrgStatus  # noqa: E402

# Registers roles/permissions tables so FKs (e.g. api_keys.role_id) resolve.
from app.models.permission import (  # noqa: E402, F401
    AdminUserRole,
    OrgUserRole,
    Permission,
    Role,
    RolePermission,
)
from app.models.request_metric import RequestMetric  # noqa: E402
from app.models.score_request import (  # noqa: E402
    DecisionType,
    LoanPerformance,
    LoanPerformanceStatus,
    RequestSource,
    ScoreDecision,
    ScoreRequest,
    ScoreRequestStatus,
)
from app.models.scoring_result import ScoringDecision, ScoringResult  # noqa: E402
from app.models.user import (  # noqa: E402
    AdminRole,
    AdminUser,
    AdminUserStatus,
    OrgRoleLabel,
    OrgUser,
    OrgUserStatus,
)
from app.models.webhook import (  # noqa: E402
    WebhookConfig,
    WebhookDelivery,
    WebhookDeliveryStatus,
)

# Deterministic so reruns are identical.
RNG = random.Random(20260604)
NOW = datetime.now(UTC)
DEMO_PASSWORD = "DemoPass!2026"

# Tables this script owns — wiped on --reset (CASCADE clears dependents like
# the *_roles junctions). Permissions/roles are seeded at app startup and are
# intentionally left untouched.
DEMO_TABLES = [
    "audit_logs",
    "api_logs",
    "request_metrics",
    "alerts",
    "decision_metrics",
    "batch_job_items",
    "bulk_scoring_jobs",
    "loan_performances",
    "scoring_results",
    "score_decisions",
    "score_requests",
    "model_metrics",
    "model_versions",
    "field_mappings",
    "mapping_templates",
    "training_uploads",
    "data_sources",
    "training_data_sources",
    "webhook_deliveries",
    "webhook_configs",
    "api_keys",
    "org_users",
    "organizations",
    "admin_users",
]

SCALES = {
    "small": {"score_requests": 220, "days": 30, "req_metric_buckets": 24 * 7},
    "medium": {"score_requests": 750, "days": 45, "req_metric_buckets": 24 * 14},
    "large": {"score_requests": 1800, "days": 60, "req_metric_buckets": 24 * 21},
}

# ── Ghanaian demo flavour ───────────────────────────────────────────────────
ORG_SEED = [
    ("Ghana Commercial Microfinance", "gcm", IndustryType.MFI, OrgStatus.ACTIVE),
    ("Accra Digital Bank", "accradigital", IndustryType.BANK, OrgStatus.ACTIVE),
    ("Kumasi Fintech Labs", "kumasifin", IndustryType.FINTECH, OrgStatus.ACTIVE),
    ("Volta Savings & Credit Co-op", "voltasacco", IndustryType.SACCO, OrgStatus.SUSPENDED),
    ("Tamale Lending Partners", "tamalelend", IndustryType.OTHER, OrgStatus.PENDING),
]

FIRST_NAMES = [
    "Kwame",
    "Ama",
    "Kofi",
    "Akosua",
    "Yaw",
    "Abena",
    "Kojo",
    "Esi",
    "Kwabena",
    "Adwoa",
    "Kwaku",
    "Afua",
    "Yaa",
    "Fiifi",
    "Nana",
    "Akua",
    "Ksi",
    "Mensah",
    "Selorm",
    "Efua",
    "Bright",
    "Gifty",
    "Emmanuel",
    "Comfort",
    "Daniel",
    "Linda",
]
LAST_NAMES = [
    "Mensah",
    "Owusu",
    "Asante",
    "Boateng",
    "Agyeman",
    "Darko",
    "Adjei",
    "Annan",
    "Appiah",
    "Osei",
    "Frimpong",
    "Acheampong",
    "Gyasi",
    "Quaye",
    "Tetteh",
    "Addo",
    "Sarpong",
    "Antwi",
    "Bonsu",
    "Danso",
]
REGIONS = [
    "Greater Accra",
    "Ashanti",
    "Western",
    "Eastern",
    "Central",
    "Northern",
    "Volta",
    "Bono",
    "Upper East",
    "Upper West",
]
LOAN_PRODUCTS = [
    "payroll_loan",
    "sme_working_capital",
    "asset_finance",
    "personal_loan",
    "agri_loan",
]


def rand_name() -> str:
    return f"{RNG.choice(FIRST_NAMES)} {RNG.choice(LAST_NAMES)}"


def short_id(n: int = 6) -> str:
    return "".join(RNG.choices("ABCDEFGHJKLMNPQRSTUVWXYZ23456789", k=n))


def grade_for_score(score: int) -> str:
    if score >= 740:
        return "A"
    if score >= 670:
        return "B"
    if score >= 580:
        return "C"
    if score >= 500:
        return "D"
    if score >= 400:
        return "E"
    return "F"


# Per-grade decision mix → covers every ScoringDecision value across the set.
GRADE_DECISION_MIX = {
    "A": [("APPROVE", 0.90), ("CONDITIONAL_APPROVE", 0.07), ("REFER", 0.03)],
    "B": [("APPROVE", 0.70), ("CONDITIONAL_APPROVE", 0.18), ("REFER", 0.08), ("DECLINE", 0.04)],
    "C": [("APPROVE", 0.38), ("CONDITIONAL_APPROVE", 0.22), ("REFER", 0.18), ("DECLINE", 0.22)],
    "D": [("CONDITIONAL_APPROVE", 0.14), ("REFER", 0.20), ("DECLINE", 0.60), ("FRAUD_HOLD", 0.06)],
    "E": [("REFER", 0.12), ("DECLINE", 0.78), ("FRAUD_HOLD", 0.10)],
    "F": [("DECLINE", 0.80), ("FRAUD_HOLD", 0.15), ("ERROR", 0.05)],
}


def weighted_choice(pairs: list[tuple[str, float]]) -> str:
    r = RNG.random()
    cum = 0.0
    for label, w in pairs:
        cum += w
        if r <= cum:
            return label
    return pairs[-1][0]


def risk_tier_for(grade: str) -> str:
    if grade in ("A", "B"):
        return "LOW"
    if grade == "C":
        return "MEDIUM"
    return "HIGH"


def loan_tier_for_amount(amount: float) -> str:
    if amount >= 30000:
        return "LARGE"
    if amount >= 8000:
        return "MEDIUM"
    return "SMALL"


# ── Reset ───────────────────────────────────────────────────────────────────
async def reset_tables() -> None:
    async with async_session_factory() as db:
        stmt = f"TRUNCATE {', '.join(DEMO_TABLES)} RESTART IDENTITY CASCADE"
        await db.execute(text(stmt))
        await db.commit()
    print(f"[reset] Truncated {len(DEMO_TABLES)} demo tables.")


# A fresh `alembic upgrade head` leaves four enum types out of sync with the
# ORM models: industry_type / source_type / training_data_status were created
# with UPPERCASE labels, and decision_type only has the legacy
# APPROVED/DECLINED/REFERRED/PENDING set. The models bind the lowercase `.value`
# (and the new decision values), so the app itself can't read/write these tables
# until they're normalized. This aligns the DB enums to the models. Idempotent.
ENUM_RENAMES = {
    "industry_type": {
        "BANK": "bank",
        "FINTECH": "fintech",
        "MFI": "mfi",
        "SACCO": "sacco",
        "OTHER": "other",
    },
    "source_type": {
        "BANK": "bank",
        "BUREAU": "bureau",
        "TELCO": "telco",
        "UTILITY": "utility",
        "OTHER": "other",
    },
    "training_data_status": {
        "PENDING": "pending",
        "PROCESSING": "processing",
        "COMPLETED": "completed",
        "APPROVED": "approved",
        "REJECTED": "rejected",
        "FAILED": "failed",
    },
    "decision_type": {"APPROVED": "approved", "DECLINED": "declined", "REFERRED": "referred"},
}
ENUM_ADDS = {
    "decision_type": ["APPROVE", "CONDITIONAL_APPROVE", "REFER", "DECLINE", "FRAUD_HOLD", "ERROR"],
}


async def normalize_enums() -> None:
    from app.db import engine

    async with engine.connect() as conn:
        ac = await conn.execution_options(isolation_level="AUTOCOMMIT")
        for typ, mapping in ENUM_RENAMES.items():
            rows = await ac.execute(
                text(
                    "SELECT e.enumlabel FROM pg_enum e JOIN pg_type t "
                    "ON t.oid = e.enumtypid WHERE t.typname = :t"
                ),
                {"t": typ},
            )
            labels = {r[0] for r in rows}
            for old, new in mapping.items():
                if old in labels and new not in labels:
                    await ac.execute(text(f"ALTER TYPE {typ} RENAME VALUE '{old}' TO '{new}'"))
                    labels.discard(old)
                    labels.add(new)
        for typ, vals in ENUM_ADDS.items():
            for v in vals:
                await ac.execute(text(f"ALTER TYPE {typ} ADD VALUE IF NOT EXISTS '{v}'"))
    print(
        "[enums] normalized industry_type / source_type / "
        "training_data_status / decision_type to match ORM models."
    )


async def already_seeded() -> bool:
    async with async_session_factory() as db:
        row = await db.execute(text("SELECT 1 FROM organizations WHERE short_name = 'gcm' LIMIT 1"))
        return row.first() is not None


# ── Seed: identity & org config ─────────────────────────────────────────────
async def seed_admins(db) -> list[AdminUser]:
    admins = [
        AdminUser(
            email="admin@payswitch.demo",
            name="Platform Administrator",
            password_hash=hash_password(DEMO_PASSWORD),
            role=AdminRole.SUPER_ADMIN,
            status=AdminUserStatus.ACTIVE,
            last_login_at=NOW - timedelta(hours=3),
        ),
        AdminUser(
            email="datascience@payswitch.demo",
            name="Akua Data-Science",
            password_hash=hash_password(DEMO_PASSWORD),
            role=AdminRole.DATA_SCIENCE,
            status=AdminUserStatus.ACTIVE,
            last_login_at=NOW - timedelta(days=1),
        ),
        AdminUser(
            email="ops@payswitch.demo",
            name="Kofi Operations",
            password_hash=hash_password(DEMO_PASSWORD),
            role=AdminRole.OPERATIONS,
            status=AdminUserStatus.ACTIVE,
            last_login_at=NOW - timedelta(days=2),
        ),
        AdminUser(
            email="former.admin@payswitch.demo",
            name="Former Staffer",
            password_hash=hash_password(DEMO_PASSWORD),
            role=AdminRole.OPERATIONS,
            status=AdminUserStatus.DEACTIVATED,
        ),
    ]
    db.add_all(admins)
    await db.flush()
    return admins


async def seed_orgs(db) -> list[Organization]:
    orgs = []
    for name, short, industry, status in ORG_SEED:
        contact = rand_name()
        org = Organization(
            name=name,
            short_name=short,
            industry_type=industry,
            status=status,
            primary_contact_name=contact,
            primary_contact_email=f"ops@{short}.com.gh",
            primary_contact_phone=f"+2332{RNG.randint(10000000, 49999999)}",
            address=f"{RNG.randint(1, 200)} High Street, {RNG.choice(REGIONS)}, Ghana",
            suspension_reason=(
                "Overdue settlement — compliance hold" if status == OrgStatus.SUSPENDED else None
            ),
            webhook_secret=short_id(32).lower(),
            created_at=NOW - timedelta(days=RNG.randint(120, 400)),
        )
        orgs.append(org)
    db.add_all(orgs)
    await db.flush()
    return orgs


async def seed_org_users(db, orgs) -> dict:
    users_by_org: dict = {}
    labels = [
        ("admin", OrgRoleLabel.ADMIN, OrgUserStatus.ACTIVE),
        ("credit", OrgRoleLabel.CREDIT_OFFICER, OrgUserStatus.ACTIVE),
        ("dev", OrgRoleLabel.DEVELOPER, OrgUserStatus.ACTIVE),
        ("analyst", OrgRoleLabel.VIEWER, OrgUserStatus.ACTIVE),
        ("invitee", OrgRoleLabel.VIEWER, OrgUserStatus.PENDING),
    ]
    for org in orgs:
        users = []
        # suspended/pending orgs get a thinner roster
        roster = labels if org.status == OrgStatus.ACTIVE else labels[:2]
        for slug, label, status in roster:
            users.append(
                OrgUser(
                    organization_id=org.id,
                    email=f"{slug}@{org.short_name}.com.gh",
                    name=rand_name(),
                    password_hash=hash_password(DEMO_PASSWORD),
                    role_label=label,
                    status=status,
                    last_login_at=(
                        NOW - timedelta(days=RNG.randint(0, 14))
                        if status == OrgUserStatus.ACTIVE
                        else None
                    ),
                    created_at=org.created_at + timedelta(days=RNG.randint(0, 10)),
                )
            )
        db.add_all(users)
        users_by_org[org.id] = users
    await db.flush()
    return users_by_org


async def seed_api_keys(db, orgs, users_by_org) -> dict:
    keys_by_org: dict = {}
    for org in orgs:
        owner = users_by_org[org.id][0]
        keys = [
            ApiKey(
                organization_id=org.id,
                key_hash=short_id(40).lower(),
                key_prefix="sk_test_" + short_id(4).lower(),
                name="Sandbox key",
                environment=ApiKeyEnvironment.SANDBOX,
                status=ApiKeyStatus.ACTIVE,
                last_used_at=NOW - timedelta(hours=RNG.randint(1, 72)),
                created_by_user_id=owner.id,
                created_at=org.created_at + timedelta(days=1),
            ),
            ApiKey(
                organization_id=org.id,
                key_hash=short_id(40).lower(),
                key_prefix="sk_live_" + short_id(4).lower(),
                name="Production key",
                environment=ApiKeyEnvironment.PRODUCTION,
                status=ApiKeyStatus.ACTIVE,
                last_used_at=NOW - timedelta(minutes=RNG.randint(5, 600)),
                created_by_user_id=owner.id,
                created_at=org.created_at + timedelta(days=2),
            ),
            ApiKey(
                organization_id=org.id,
                key_hash=short_id(40).lower(),
                key_prefix="sk_live_" + short_id(4).lower(),
                name="Rotated (old) key",
                environment=ApiKeyEnvironment.PRODUCTION,
                status=ApiKeyStatus.REVOKED,
                last_used_at=NOW - timedelta(days=RNG.randint(20, 60)),
                revoked_at=NOW - timedelta(days=RNG.randint(5, 19)),
                created_by_user_id=owner.id,
                created_at=org.created_at + timedelta(days=2),
            ),
        ]
        db.add_all(keys)
        keys_by_org[org.id] = keys
    await db.flush()
    return keys_by_org


async def seed_webhooks(db, orgs) -> None:
    for org in orgs:
        if org.status != OrgStatus.ACTIVE:
            continue
        cfg = WebhookConfig(
            organization_id=org.id,
            url=f"https://{org.short_name}.com.gh/webhooks/payswitch",
            events=["score.completed", "batch.completed"],
            is_active=True,
            description="Primary scoring callback",
            created_at=org.created_at + timedelta(days=3),
        )
        db.add(cfg)
        await db.flush()
        # Delivery history covering all delivery statuses
        for status, code, attempts in [
            (WebhookDeliveryStatus.DELIVERED, 200, 1),
            (WebhookDeliveryStatus.DELIVERED, 200, 1),
            (WebhookDeliveryStatus.FAILED, 503, 5),
            (WebhookDeliveryStatus.PENDING, None, 0),
        ]:
            ts = NOW - timedelta(hours=RNG.randint(1, 240))
            db.add(
                WebhookDelivery(
                    webhook_config_id=cfg.id,
                    event_type="score.completed",
                    payload={"tracking_id": f"SCR-{short_id()}", "decision": "APPROVE"},
                    status=status,
                    attempt_count=attempts,
                    last_attempt_at=(ts if status != WebhookDeliveryStatus.PENDING else None),
                    response_status_code=code,
                    response_body=(
                        "ok" if code == 200 else ("service unavailable" if code else None)
                    ),
                    error_message=(
                        "max retries exceeded" if status == WebhookDeliveryStatus.FAILED else None
                    ),
                    delivered_at=(ts if status == WebhookDeliveryStatus.DELIVERED else None),
                    created_at=ts,
                )
            )
    await db.flush()


# ── Seed: models / ML metadata ──────────────────────────────────────────────
CREDIT_FEATURES = [
    "income_stability",
    "debt_to_income_ratio",
    "payment_history_strength",
    "credit_utilization",
    "employment_tenure_months",
    "account_age_months",
    "num_active_loans",
    "recent_inquiries",
    "savings_balance_trend",
    "mobile_money_velocity",
]


def model_metrics_blob(auc: float) -> dict:
    return {
        "auc": round(auc, 4),
        "ks": round(auc - 0.37, 4),
        "gini": round(2 * auc - 1, 4),
        "f1": round(auc - 0.05, 4),
        "precision": round(auc - 0.02, 4),
        "recall": round(auc - 0.08, 4),
        "accuracy": round(auc - 0.01, 4),
    }


async def seed_models(db, admins) -> None:
    ds = admins[1]  # data-science admin
    # Training data sources (covers source types + a rejected one)
    sources = [
        TrainingDataSource(
            name="GCB Bank historical loan book",
            source_type=SourceType.BANK,
            provider="GCB Bank",
            data_period_start=(NOW - timedelta(days=900)).date(),
            data_period_end=(NOW - timedelta(days=120)).date(),
            record_count=184_500,
            quality_score=Decimal("0.93"),
            transformation_status=TrainingDataStatus.APPROVED,
            uploaded_by_id=ds.id,
            created_at=NOW - timedelta(days=110),
        ),
        TrainingDataSource(
            name="XDS Credit Bureau extract",
            source_type=SourceType.BUREAU,
            provider="XDS Data Ghana",
            record_count=242_100,
            quality_score=Decimal("0.88"),
            transformation_status=TrainingDataStatus.COMPLETED,
            uploaded_by_id=ds.id,
            created_at=NOW - timedelta(days=70),
        ),
        TrainingDataSource(
            name="MTN MoMo telco signals",
            source_type=SourceType.TELCO,
            provider="MTN Ghana",
            record_count=510_000,
            quality_score=Decimal("0.71"),
            transformation_status=TrainingDataStatus.PROCESSING,
            uploaded_by_id=ds.id,
            created_at=NOW - timedelta(days=12),
        ),
        TrainingDataSource(
            name="ECG utility payment history",
            source_type=SourceType.UTILITY,
            provider="ECG",
            record_count=98_000,
            quality_score=Decimal("0.42"),
            transformation_status=TrainingDataStatus.REJECTED,
            uploaded_by_id=ds.id,
            created_at=NOW - timedelta(days=40),
        ),
    ]
    db.add_all(sources)

    # ── model_versions: cover EVERY ModelStatus for credit_risk ──
    credit_versions = [
        (
            "1.0.0",
            ModelStatus.ARCHIVED,
            0.781,
            NOW - timedelta(days=300),
            NOW - timedelta(days=290),
        ),
        (
            "1.1.0",
            ModelStatus.DEPRECATED,
            0.804,
            NOW - timedelta(days=210),
            NOW - timedelta(days=200),
        ),
        (
            "2.0.0",
            ModelStatus.PRODUCTION,
            0.857,
            NOW - timedelta(days=45),
            NOW - timedelta(days=40),
        ),
        ("2.1.0", ModelStatus.STAGING, 0.864, NOW - timedelta(days=10), None),
        ("2.2.0-rc1", ModelStatus.VALIDATION, 0.869, NOW - timedelta(days=4), None),
        ("2.3.0-dev", ModelStatus.TRAINING, None, None, None),
    ]
    for ver, status, auc, trained_at, promoted_at in credit_versions:
        tid = f"TRAIN-{(trained_at or NOW).strftime('%Y%m%d-%H%M%S')}" if trained_at else None
        db.add(
            ModelVersion(
                version=ver,
                model_type="credit_risk",
                status=status,
                metrics=(model_metrics_blob(auc) if auc else None),
                feature_count=len(CREDIT_FEATURES),
                description=f"Credit risk PD model {ver}",
                trained_at=trained_at,
                promoted_at=promoted_at,
                promoted_by_id=(admins[0].id if promoted_at else None),
                training_id=tid,
                data_source="GCB Bank historical loan book",
                mlflow_run_id=(uuid.uuid4().hex if trained_at else None),
                training_duration_seconds=(RNG.uniform(900, 4200) if trained_at else None),
                training_status=("succeeded" if auc else "running"),
                dataset_info=(
                    {"rows": 184_500, "features": len(CREDIT_FEATURES), "positive_rate": 0.142}
                    if trained_at
                    else None
                ),
                created_at=(trained_at or NOW),
            )
        )

    # fraud_detection lifecycle
    for ver, status, auc, trained_at, promoted_at in [
        (
            "0.9.0",
            ModelStatus.DEPRECATED,
            0.901,
            NOW - timedelta(days=160),
            NOW - timedelta(days=150),
        ),
        (
            "1.0.0",
            ModelStatus.PRODUCTION,
            0.934,
            NOW - timedelta(days=38),
            NOW - timedelta(days=35),
        ),
        ("1.1.0-rc1", ModelStatus.VALIDATION, 0.939, NOW - timedelta(days=3), None),
    ]:
        db.add(
            ModelVersion(
                version=ver,
                model_type="fraud_detection",
                status=status,
                metrics=model_metrics_blob(auc),
                feature_count=22,
                description=f"Fraud anomaly model {ver}",
                trained_at=trained_at,
                promoted_at=promoted_at,
                promoted_by_id=(admins[0].id if promoted_at else None),
                training_id=f"TRAIN-{trained_at.strftime('%Y%m%d-%H%M%S')}",
                training_status="succeeded",
                created_at=trained_at,
            )
        )

    # Phase-2 models so model-ops shows the full suite
    db.add(
        ModelVersion(
            version="1.0.0",
            model_type="loan_amount",
            status=ModelStatus.PRODUCTION,
            metrics={"mae_ghs": 612.4, "rmse_ghs": 1043.0, "r2": 0.78},
            feature_count=15,
            description="Loan amount recommender",
            trained_at=NOW - timedelta(days=30),
            promoted_at=NOW - timedelta(days=28),
            promoted_by_id=admins[0].id,
            training_status="succeeded",
            created_at=NOW - timedelta(days=30),
        )
    )
    # income_verification champion (PRODUCTION) — the 4th model in the suite.
    db.add(
        ModelVersion(
            version="1.0.0",
            model_type="income_verification",
            status=ModelStatus.PRODUCTION,
            metrics={"auc": 0.861, "accuracy": 0.83, "macro_f1": 0.80},
            feature_count=12,
            description="Income tier classifier",
            trained_at=NOW - timedelta(days=26),
            promoted_at=NOW - timedelta(days=24),
            promoted_by_id=admins[0].id,
            training_status="succeeded",
            created_at=NOW - timedelta(days=26),
        )
    )
    # plus a staging challenger so the lifecycle still shows a non-prod version
    db.add(
        ModelVersion(
            version="1.1.0-rc1",
            model_type="income_verification",
            status=ModelStatus.STAGING,
            metrics={"auc": 0.868, "accuracy": 0.84, "macro_f1": 0.81},
            feature_count=12,
            description="Income tier classifier (challenger)",
            trained_at=NOW - timedelta(days=5),
            training_status="succeeded",
            created_at=NOW - timedelta(days=5),
        )
    )
    await db.flush()

    # ── model_metrics: training, drift, score_psi over time ──
    # training rows (one per completed credit_risk retraining)
    for tid, before, after, when in [
        ("TRAIN-credit-1", 0.781, 0.804, NOW - timedelta(days=200)),
        ("TRAIN-credit-2", 0.804, 0.857, NOW - timedelta(days=40)),
    ]:
        db.add(
            ModelMetric(
                model_type="credit_risk",
                model_version="2.0.0",
                training_id=tid,
                metric_type="training",
                metrics={
                    "result": "success",
                    "metrics_before": model_metrics_blob(before),
                    "metrics_after": model_metrics_blob(after),
                },
                timestamp=when,
                created_at=when,
            )
        )
    # drift + score_psi snapshots across the demo window (weekly)
    for wk in range(8, 0, -1):
        when = NOW - timedelta(days=wk * 5)
        feature_drift = {}
        for f in CREDIT_FEATURES:
            base = RNG.uniform(0.01, 0.12)
            # let two features creep into warning/critical near the present
            if wk <= 2 and f in ("credit_utilization", "mobile_money_velocity"):
                base = RNG.uniform(0.22, 0.34)
            feature_drift[f] = round(base, 4)
        db.add(
            ModelMetric(
                model_type="credit_risk",
                model_version="2.0.0",
                metric_type="drift",
                metrics=None,
                feature_drift=feature_drift,
                timestamp=when,
                created_at=when,
            )
        )
        db.add(
            ModelMetric(
                model_type="credit_risk",
                model_version="2.0.0",
                metric_type="score_psi",
                metrics={
                    "psi": round(RNG.uniform(0.02, 0.18), 4),
                    "sample_size": RNG.randint(800, 2400),
                },
                timestamp=when,
                created_at=when,
            )
        )
    await db.flush()


# ── Seed: data sources & datasets (admin Data Sources / Datasets screens) ───
DATA_SOURCES = [
    ("GCB Bank", "GCB", DSSourceType.BANK, "Tier-1 commercial bank loan book"),
    ("XDS Data Ghana", "XDS", DSSourceType.BUREAU, "Credit bureau reference data"),
    ("MTN Mobile Money", "MTNMOMO", DSSourceType.TELCO, "Mobile-money transaction signals"),
    ("Electricity Company of Ghana", "ECG", DSSourceType.UTILITY, "Utility payment history"),
    ("Adehyeman Savings & Loans", "ADEHYE", DSSourceType.MFI, "Microfinance repayment history"),
]

TARGET_FEATURES = [
    "monthly_income",
    "employment_tenure_months",
    "debt_to_income_ratio",
    "num_active_loans",
    "total_outstanding_debt",
    "credit_utilization",
    "payment_history_strength",
    "num_delinquencies_24m",
    "account_age_months",
    "recent_inquiries_6m",
    "savings_balance_trend",
    "mobile_money_velocity",
    "num_dependents",
    "applicant_age",
    "region_code",
    "loan_amount_requested",
    "loan_tenure_months",
    "collateral_value",
]


def _conf_level(c: float) -> ConfidenceLevel:
    if c > 0.90:
        return ConfidenceLevel.GREEN
    if c >= 0.60:
        return ConfidenceLevel.YELLOW
    return ConfidenceLevel.RED


async def seed_data_sources(db, admins) -> None:
    ds_admin = admins[1]  # data-science
    ops_admin = admins[2]  # operations (approver)

    # Walk every UploadStatus across the uploads so all states are visible.
    status_cycle = list(UploadStatus)
    fmt_cycle = list(FileFormat)
    mapping_src_cycle = list(MappingSource)
    si = 0  # status index
    total_uploads = 0
    total_fields = 0

    for name, code, stype, desc in DATA_SOURCES:
        src = DataSource(
            name=name,
            short_code=code,
            source_type=stype,
            description=desc,
            total_uploads=0,
            created_at=NOW - timedelta(days=RNG.randint(120, 300)),
        )
        db.add(src)
        await db.flush()

        # An active mapping template for the source (reused on re-upload).
        tmpl = MappingTemplate(
            data_source_id=src.id,
            version=1,
            is_active=True,
            mappings=[
                {"source_field": f"col_{i}", "target_feature": tf}
                for i, tf in enumerate(TARGET_FEATURES[:12])
            ],
            created_at=src.created_at + timedelta(days=2),
        )
        db.add(tmpl)

        n_uploads = RNG.randint(3, 5)
        last_upload_at = None
        for u in range(n_uploads):
            status = status_cycle[si % len(status_cycle)]
            si += 1
            fmt = fmt_cycle[(si + u) % len(fmt_cycle)]
            created = src.created_at + timedelta(days=RNG.randint(5, 110), hours=RNG.randint(0, 23))
            last_upload_at = max(last_upload_at or created, created)
            records = RNG.randint(20_000, 480_000)
            valid = int(records * RNG.uniform(0.9, 0.995))
            mapped = RNG.randint(12, 18)
            qscore = round(RNG.uniform(0.55, 0.97), 2)
            done = status in (UploadStatus.TRANSFORMED, UploadStatus.TRAINED)
            errored = status == UploadStatus.ERROR
            tid = (
                f"TRAIN-{created.strftime('%Y%m%d-%H%M%S')}"
                if status in (UploadStatus.TRAINING, UploadStatus.TRAINED)
                else None
            )

            upload = TrainingUpload(
                data_source_id=src.id,
                status=status,
                file_name=f"{code.lower()}_loanbook_{created.strftime('%Y%m')}.{fmt.value}",
                file_format=fmt,
                file_size_bytes=RNG.randint(2_000_000, 220_000_000),
                raw_file_path=f"training-data/{code}/{created.strftime('%Y%m%d')}/raw.{fmt.value}",
                transformed_file_path=(
                    f"training-data/{code}/transformed.parquet" if done else None
                ),
                file_metadata={
                    "columns": [f"col_{i}" for i in range(mapped + RNG.randint(0, 4))],
                    "sample_rows": [
                        {"col_0": "GH-123", "col_1": "4500"},
                        {"col_0": "GH-987", "col_1": "12000"},
                    ],
                    "row_count": records,
                },
                record_count=records,
                valid_record_count=valid,
                training_id=tid,
                models_to_train=["credit_risk", "fraud_detection"],
                features_mapped=mapped,
                target_features_total=18,
                quality_report={
                    "completeness_pct": round(RNG.uniform(82, 99), 1),
                    "duplicate_pct": round(RNG.uniform(0.1, 4.0), 2),
                    "issues": (
                        [] if qscore > 0.85 else ["missing_values:income", "outliers:loan_amount"]
                    ),
                },
                quality_score=qscore,
                rejection_reason=None,
                error_message=(
                    "Schema validation failed: column 'income' not found" if errored else None
                ),
                uploaded_by_id=ds_admin.id,
                approved_by_id=(ops_admin.id if done else None),
                approved_at=(created + timedelta(hours=3) if done else None),
                completed_at=(created + timedelta(hours=5) if done else None),
                created_at=created,
                updated_at=created,
            )
            db.add(upload)
            await db.flush()
            total_uploads += 1

            # Field mappings — cover all confidence levels + mapping sources.
            n_fields = RNG.randint(8, 14)
            for fi in range(n_fields):
                tf = TARGET_FEATURES[fi % len(TARGET_FEATURES)]
                # spread confidences so green/yellow/red all appear
                conf = RNG.choice(
                    [RNG.uniform(0.91, 0.99), RNG.uniform(0.6, 0.89), RNG.uniform(0.2, 0.59)]
                )
                msrc = mapping_src_cycle[(fi + si) % len(mapping_src_cycle)]
                db.add(
                    FieldMapping(
                        training_upload_id=upload.id,
                        source_field=f"col_{fi}",
                        target_feature=tf,
                        confidence=Decimal(str(round(conf, 4))),
                        confidence_level=_conf_level(conf),
                        mapping_source=msrc,
                        sample_values=["4500", "12000", "8800"],
                        data_type_detected=RNG.choice(["numeric", "string", "date"]),
                        is_approved=done,
                        created_at=created,
                    )
                )
                total_fields += 1

        src.total_uploads = n_uploads
        src.last_upload_at = last_upload_at

    await db.flush()
    print(
        f"[sources] {len(DATA_SOURCES)} data sources, {total_uploads} dataset "
        f"uploads (all statuses), {total_fields} field mappings."
    )


# ── Seed: scoring history (the heart of the demo) ───────────────────────────
def build_scoring_jsons(score: int, grade: str, decision: str, amount: float):
    """Return (credit_risk, fraud_detection, loan_amount, income_verification,
    scoring_metadata) dicts matching the shapes the app stores."""
    pd = round(max(0.01, min(0.95, (850 - score) / 700)), 4)
    risk_tier = risk_tier_for(grade)
    fraud_flag = "LOW"
    if decision == "FRAUD_HOLD":
        fraud_flag = "HIGH"
    elif grade in ("E", "F"):
        fraud_flag = RNG.choice(["MEDIUM", "HIGH"])
    elif grade == "D":
        fraud_flag = RNG.choice(["LOW", "MEDIUM"])

    shap = sorted(
        (
            {
                "feature": f,
                "value": round(RNG.uniform(-0.25, 0.25), 4),
                "direction": "positive" if RNG.random() > 0.5 else "negative",
            }
            for f in RNG.sample(CREDIT_FEATURES, 5)
        ),
        key=lambda d: abs(d["value"]),
        reverse=True,
    )
    credit_risk = {
        "probability_of_default": pd,
        "pd_confidence": round(RNG.uniform(0.78, 0.97), 4),
        "risk_tier": risk_tier,
        "shap_contributions": shap,
        "decision_reason_codes": RNG.sample(["R01", "R03", "R06", "R09", "R12"], 2),
        "model_version": "credit_risk_v2.0.0",
    }
    fraud_detection = {
        "fraud_anomaly_score": round(
            RNG.uniform(0.5, 0.95) if fraud_flag == "HIGH" else RNG.uniform(0.01, 0.3), 4
        ),
        "fraud_risk_flag": fraud_flag,
        "model_version": "fraud_v1.0.0",
    }
    loan_tier = loan_tier_for_amount(amount)
    loan_amount = (
        {
            "recommended_amount_ghs": round(amount, 2),
            "recommended_loan_tier": loan_tier,
            "model_version": "loan_v1.0.0",
        }
        if decision in ("APPROVE", "CONDITIONAL_APPROVE")
        else None
    )
    income_verification = {
        "income_tier": RNG.randint(0, 3),
        "income_tier_label": RNG.choice(["LOWER", "LOWER_MID", "UPPER_MID", "UPPER"]),
        "income_confidence": round(RNG.uniform(0.7, 0.95), 4),
        "model_version": "income_v1.0.0",
    }
    age = RNG.randint(21, 68)
    dqs = round(RNG.uniform(0.55, 0.98), 3)
    scoring_metadata = {
        "credit_score": score,
        "score_grade": grade,
        "data_quality_score": dqs,
        "bureau_hit_status": "HIT" if RNG.random() > 0.12 else "NO_RECORD",
        "product_source": RNG.choice(["45", "49", "45+49"]),
        "applicant_age_at_application": str(age),
        "credit_age_months_at_application": str(RNG.randint(0, 180)),
    }
    return (
        credit_risk,
        fraud_detection,
        loan_amount,
        income_verification,
        scoring_metadata,
        pd,
        age,
        dqs,
        fraud_flag,
        loan_tier,
        risk_tier,
    )


async def seed_score_requests(db, orgs, users_by_org, keys_by_org, cfg) -> list:
    n = cfg["score_requests"]
    days = cfg["days"]
    active_orgs = [o for o in orgs if o.status != OrgStatus.PENDING]
    created_results = []

    # Distribution of request statuses (covers all four)
    # completed dominate; the rest spread across pending/processing/failed.
    rows_sr, rows_sd, rows_res, rows_dm, rows_lp = [], [], [], [], []

    for i in range(n):
        org = RNG.choice(active_orgs)
        users = users_by_org[org.id]
        keys = keys_by_org[org.id]
        # Bias ~20% of requests into the last 2 days so "today" / "last 24h"
        # dashboard windows are well populated; the rest spread over the window.
        if RNG.random() < 0.20:
            age_days = RNG.uniform(0, 2)
        else:
            age_days = RNG.uniform(0, days)
        created = NOW - timedelta(days=age_days)
        source = weighted_choice([("api", 0.6), ("web_portal", 0.25), ("bulk", 0.15)])
        applicant = rand_name()
        amount = RNG.choice([1500, 3000, 5000, 8000, 12000, 20000, 35000, 50000]) * RNG.uniform(
            0.8, 1.2
        )
        tenure = RNG.choice([3, 6, 9, 12, 18, 24, 36])
        dob = (NOW - timedelta(days=RNG.randint(22 * 365, 65 * 365))).date().isoformat()
        # Keys match what GET /v1/score-requests/{id} reads to render the
        # applicant + loan panels (full_name/date_of_birth/... and loan_request).
        payload = {
            "applicant": {
                "full_name": applicant,
                "date_of_birth": dob,
                "national_id_number": (
                    f"GHA-{RNG.randint(100000000, 999999999)}-{RNG.randint(0, 9)}"
                ),
                "phone": f"+2332{RNG.randint(10000000, 49999999)}",
                "account_number": str(RNG.randint(1000000000, 9999999999)),
                "gender": RNG.choice(["male", "female"]),
                "marital_status": RNG.choice(["single", "married"]),
                "dependents": RNG.randint(0, 4),
                "region": RNG.choice(REGIONS),
            },
            "loan_request": {
                "amount": round(amount, 2),
                "tenure_months": tenure,
                "purpose": RNG.choice(LOAN_PRODUCTS),
            },
            "identifiers": {"ghana_card_hash": short_id(24).lower()},
        }

        # status mix
        roll = RNG.random()
        if roll < 0.82:
            status = ScoreRequestStatus.COMPLETED
        elif roll < 0.90:
            status = ScoreRequestStatus.PROCESSING
        elif roll < 0.96:
            status = ScoreRequestStatus.PENDING
        else:
            status = ScoreRequestStatus.FAILED

        tracking = f"SCR-{short_id(8)}"
        sr = ScoreRequest(
            tracking_id=tracking,
            organization_id=org.id,
            reference_id=f"REF-{RNG.randint(100000, 999999)}",
            status=status,
            request_source=RequestSource(source),
            request_payload=payload,
            applicant_hash=short_id(40).lower(),
            api_key_id=(keys[1].id if source == "api" else None),
            created_by_user_id=(users[1].id if source == "web_portal" else None),
            idempotency_key=uuid.uuid4().hex,
            callback_url=(f"https://{org.short_name}.com.gh/cb" if source == "api" else None),
            created_at=created,
            updated_at=created,
        )

        if status == ScoreRequestStatus.COMPLETED:
            score = max(300, min(850, int(RNG.gauss(625, 95))))
            grade = grade_for_score(score)
            decision = weighted_choice(GRADE_DECISION_MIX[grade])
            (
                credit_risk,
                fraud_detection,
                loan_amount,
                income_verification,
                smeta,
                pd,
                age,
                dqs,
                fraud_flag,
                loan_tier,
                risk_tier,
            ) = build_scoring_jsons(score, grade, decision, amount)
            scored = created + timedelta(seconds=RNG.uniform(2, 25))
            proc_ms = int(RNG.uniform(800, 4200))
            sr.score_value = score
            sr.risk_category = risk_tier
            sr.confidence = round(RNG.uniform(0.75, 0.97), 4)
            sr.model_version = "credit_risk_v2.0.0"
            sr.processing_time_ms = proc_ms
            sr.scored_at = scored
            sr.valid_until = scored + timedelta(days=14)
        rows_sr.append(sr)

        # We need sr.id before children — flush in batches below.
        created_results.append((sr, status, created))

    db.add_all(rows_sr)
    await db.flush()

    # Guarantee every ScoringDecision value appears at least a few times
    # (esp. ERROR / FRAUD_HOLD, which the grade mix produces only rarely).
    must_have = [d.value for d in ScoringDecision] * 3  # ~3 of each, front-loaded
    completed_seq = [sr for sr, st, _ in created_results if st == ScoreRequestStatus.COMPLETED]
    forced: dict = {}
    for i, sr in enumerate(completed_seq):
        if i < len(must_have):
            forced[sr.tracking_id] = must_have[i]

    # Now children, with parent ids available
    for sr, status, created in created_results:
        if status != ScoreRequestStatus.COMPLETED:
            continue
        score = sr.score_value
        grade = grade_for_score(score)
        # forced coverage first, otherwise grade-weighted mix
        decision = forced.get(sr.tracking_id) or weighted_choice(GRADE_DECISION_MIX[grade])
        amount = sr.request_payload["loan_request"]["amount"]
        (
            credit_risk,
            fraud_detection,
            loan_amount,
            income_verification,
            smeta,
            pd,
            age,
            dqs,
            fraud_flag,
            loan_tier,
            risk_tier,
        ) = build_scoring_jsons(score, grade, decision, amount)
        scored = sr.scored_at

        res = ScoringResult(
            request_id=sr.tracking_id,
            score_request_id=sr.id,
            scoring_timestamp=scored,
            decision=ScoringDecision(decision),
            condition_applied=(
                ["income_proof_required"] if decision == "CONDITIONAL_APPROVE" else []
            ),
            credit_risk=credit_risk,
            fraud_detection=fraud_detection,
            loan_amount=loan_amount,
            income_verification=income_verification,
            scoring_metadata=smeta,
            errors=(["model_timeout"] if decision == "ERROR" else None),
            created_at=scored,
            updated_at=scored,
        )
        rows_res.append(res)

        # org-side human decision (ScoreDecision) — maps onto DecisionType.
        # REFER means "awaiting a manual decision", so it must NOT have a
        # ScoreDecision yet — otherwise the override endpoint (which inserts
        # one) collides on the unique score_request_id constraint. Leaving
        # REFER open makes the override flow demoable.
        approved = decision in ("APPROVE", "CONDITIONAL_APPROVE")
        if decision != "REFER":
            sd = ScoreDecision(
                score_request_id=sr.id,
                decision=DecisionType(decision),
                approved_amount=(
                    Decimal(str(round(amount * RNG.uniform(0.6, 1.0), 2))) if approved else None
                ),
                approved_tenure=(RNG.choice([6, 12, 18, 24]) if approved else None),
                interest_rate=(
                    Decimal(str(round(RNG.uniform(0.018, 0.035), 4))) if approved else None
                ),
                decline_reason=(
                    None
                    if approved
                    else RNG.choice(
                        [
                            "Insufficient affordability",
                            "Adverse bureau record",
                            "Failed fraud screening",
                            "Policy hard-stop",
                        ]
                    )
                ),
                decision_notes="Auto-generated demo decision",
                decided_by_user_id=None,
                decided_at=scored,
                created_at=scored,
                updated_at=scored,
            )
            rows_sd.append(sd)

        # store children needing result.id
        sr._pending = (
            res,
            smeta,
            pd,
            age,
            dqs,
            fraud_flag,
            loan_tier,
            risk_tier,
            decision,
            grade,
            score,
            scored,
        )

    db.add_all(rows_res)
    db.add_all(rows_sd)
    await db.flush()

    # decision_metrics + a subset of loan_performances (need result.id)
    completed = [sr for sr, st, _ in created_results if st == ScoreRequestStatus.COMPLETED]
    lp_status_cycle = list(LoanPerformanceStatus)  # guarantee all states appear
    lp_idx = 0
    for sr in completed:
        (
            res,
            smeta,
            pd,
            age,
            dqs,
            fraud_flag,
            loan_tier,
            risk_tier,
            decision,
            grade,
            score,
            scored,
        ) = sr._pending
        rows_dm.append(
            DecisionMetric(
                scoring_result_id=res.id,
                decision=decision,
                credit_score=score,
                score_grade=grade,
                risk_tier=risk_tier,
                fraud_flag=fraud_flag,
                loan_tier=(loan_tier if decision in ("APPROVE", "CONDITIONAL_APPROVE") else None),
                probability_of_default=pd,
                data_quality_score=dqs,
                applicant_age=age,
                has_explainability=bool(res.credit_risk.get("shap_contributions")),
                timestamp=scored,
                created_at=scored,
            )
        )
        # ~30% of approved loans get a performance record; cycle all statuses
        if decision in ("APPROVE", "CONDITIONAL_APPROVE") and RNG.random() < 0.30:
            st = lp_status_cycle[lp_idx % len(lp_status_cycle)]
            lp_idx += 1
            dpd_map = {
                LoanPerformanceStatus.CURRENT: 0,
                LoanPerformanceStatus.PAST_DUE_30: 30,
                LoanPerformanceStatus.PAST_DUE_60: 60,
                LoanPerformanceStatus.PAST_DUE_90: 90,
                LoanPerformanceStatus.DEFAULT: 120,
                LoanPerformanceStatus.PAID_OFF: 0,
                LoanPerformanceStatus.WRITE_OFF: 180,
            }
            bal = sr.request_payload["loan_request"]["amount"]
            rows_lp.append(
                LoanPerformance(
                    score_request_id=sr.id,
                    status=st,
                    days_past_due=dpd_map[st],
                    outstanding_balance=Decimal(
                        str(
                            round(
                                0
                                if st == LoanPerformanceStatus.PAID_OFF
                                else bal * RNG.uniform(0.2, 0.9),
                                2,
                            )
                        )
                    ),
                    as_of_date=NOW.date(),
                    reported_at=NOW - timedelta(days=RNG.randint(1, 20)),
                    created_at=scored,
                )
            )

    db.add_all(rows_dm)
    db.add_all(rows_lp)
    await db.flush()
    print(
        f"[score] {len(rows_sr)} requests "
        f"({len(completed)} completed → results/decisions/metrics), "
        f"{len(rows_lp)} loan-performance rows."
    )
    return created_results


# ── Seed: batch jobs ────────────────────────────────────────────────────────
async def seed_batch_jobs(db, orgs, users_by_org) -> None:
    active = [o for o in orgs if o.status == OrgStatus.ACTIVE]
    # one job per BulkJobStatus to guarantee full coverage, then a few extra
    plan = [
        (BulkJobStatus.COMPLETED, IngestionStatus.COMPLETE, 500, 500, 478),
        (BulkJobStatus.COMPLETED, IngestionStatus.COMPLETE, 1200, 1200, 1150),
        (BulkJobStatus.PROCESSING, IngestionStatus.PENDING, 800, 800, 430),
        (BulkJobStatus.QUEUED, IngestionStatus.PENDING, 300, 0, 0),
        (BulkJobStatus.VALIDATING, IngestionStatus.PENDING, 0, 0, 0),
        (BulkJobStatus.FAILED, IngestionStatus.FAILED, 600, 540, 60),
        (BulkJobStatus.CANCELLED, IngestionStatus.PENDING, 450, 200, 180),
    ]
    for k, (status, ing, total, valid, processed) in enumerate(plan):
        org = RNG.choice(active)
        owner = users_by_org[org.id][0]
        created = NOW - timedelta(days=RNG.randint(1, 25), hours=RNG.randint(0, 23))
        started = (
            created + timedelta(minutes=2)
            if status not in (BulkJobStatus.QUEUED, BulkJobStatus.VALIDATING)
            else None
        )
        completed = (
            started + timedelta(minutes=RNG.randint(5, 90))
            if status in (BulkJobStatus.COMPLETED, BulkJobStatus.FAILED, BulkJobStatus.CANCELLED)
            and started
            else None
        )
        job_id = f"BATCH-{created.strftime('%Y%m%d')}-{short_id(8).lower()}"
        errors = (
            max(0, (valid - processed)) if status == BulkJobStatus.FAILED else RNG.randint(0, 20)
        )
        job = BulkScoringJob(
            job_id=job_id,
            organization_id=org.id,
            file_name=f"loan_book_{created.strftime('%Y%m')}.csv",
            file_path=f"bulk-uploads/{org.short_name}/{job_id}.csv",
            status=status,
            models_to_run=["credit_risk", "fraud_detection"],
            total_records=total,
            valid_records=valid,
            invalid_records=max(0, total - valid),
            processed_records=processed,
            error_count=errors,
            results_blob_path=(
                f"batch/{job_id}/results.jsonl" if status == BulkJobStatus.COMPLETED else None
            ),
            ingestion_status=ing,
            ingested_count=(processed if ing == IngestionStatus.COMPLETE else 0),
            created_by_user_id=owner.id,
            started_at=started,
            completed_at=completed,
            created_at=created,
            updated_at=(completed or started or created),
        )
        db.add(job)
        await db.flush()

        # Sample of batch_job_items covering all item statuses
        sample = min(40, max(5, total // 20)) if total else 5
        for idx in range(sample):
            if status == BulkJobStatus.QUEUED:
                ist = BatchJobItemStatus.PENDING
            elif status == BulkJobStatus.VALIDATING:
                ist = BatchJobItemStatus.PENDING
            elif status == BulkJobStatus.PROCESSING:
                ist = RNG.choice(
                    [
                        BatchJobItemStatus.COMPLETED,
                        BatchJobItemStatus.PROCESSING,
                        BatchJobItemStatus.PENDING,
                    ]
                )
            elif status == BulkJobStatus.CANCELLED:
                ist = RNG.choice([BatchJobItemStatus.COMPLETED, BatchJobItemStatus.CANCELLED])
            elif status == BulkJobStatus.FAILED:
                ist = RNG.choice([BatchJobItemStatus.COMPLETED, BatchJobItemStatus.FAILED])
            else:
                ist = RNG.choice([BatchJobItemStatus.COMPLETED] * 5 + [BatchJobItemStatus.FAILED])
            failed = ist == BatchJobItemStatus.FAILED
            db.add(
                BatchJobItem(
                    batch_job_id=job.id,
                    index=idx,
                    status=ist,
                    payload={
                        "row": idx,
                        "ghana_card_hash": short_id(20).lower(),
                        "amount_ghs": RNG.choice([2000, 5000, 10000]),
                    },
                    score_request_id=None,
                    score_tracking_id=(
                        f"SCR-{short_id(8)}" if ist == BatchJobItemStatus.COMPLETED else None
                    ),
                    error_code=("bureau_failed" if failed else None),
                    error_message=("Bureau lookup timed out" if failed else None),
                    started_at=(started if ist != BatchJobItemStatus.PENDING else None),
                    completed_at=(
                        completed
                        if ist
                        in (
                            BatchJobItemStatus.COMPLETED,
                            BatchJobItemStatus.FAILED,
                            BatchJobItemStatus.CANCELLED,
                        )
                        else None
                    ),
                    created_at=created,
                )
            )
    await db.flush()
    print(f"[batch] {len(plan)} bulk jobs (all statuses) + sampled items.")


# ── Seed: infra telemetry, api logs ─────────────────────────────────────────
ENDPOINTS = [
    ("POST", "/v1/score-requests"),
    ("GET", "/v1/score-requests"),
    ("GET", "/v1/score-requests/stats"),
    ("POST", "/v1/score/batch"),
    ("GET", "/v1/score/batch"),
    ("GET", "/v1/monitoring/risk"),
    ("GET", "/v1/monitoring/model-ops"),
    ("GET", "/v1/monitoring/infrastructure"),
    ("GET", "/v1/monitoring/compliance"),
    ("GET", "/v1/monitoring/alerts"),
    ("POST", "/auth/login"),
    ("GET", "/org/profile"),
]


async def seed_request_metrics(db, orgs, keys_by_org, cfg) -> None:
    buckets = cfg["req_metric_buckets"]
    rows = []
    log_rows = []
    active = [o for o in orgs if o.status != OrgStatus.PENDING]
    for h in range(buckets, 0, -1):
        bucket_time = NOW - timedelta(hours=h)
        # diurnal traffic: more during business hours (UTC≈GMT in Ghana)
        hour = bucket_time.hour
        base = 18 if 7 <= hour <= 19 else 5
        count = max(1, int(RNG.gauss(base, base * 0.4)))
        for _ in range(count):
            method, ep = RNG.choice(ENDPOINTS)
            roll = RNG.random()
            if roll < 0.93:
                status = RNG.choice([200, 200, 200, 201, 200])
            elif roll < 0.985:
                status = RNG.choice([400, 401, 404, 422, 429])
            else:
                status = RNG.choice([500, 502, 503])
            # latency: scoring + monitoring are heavier; add a p99 tail
            if "score-requests" in ep and method == "POST":
                lat = int(RNG.uniform(900, 3200))
            elif ep.startswith("/v1/monitoring"):
                lat = int(RNG.uniform(120, 800))
            else:
                lat = int(RNG.uniform(25, 350))
            if RNG.random() < 0.02:  # tail
                lat = int(lat * RNG.uniform(3, 6))
            ts = bucket_time + timedelta(minutes=RNG.uniform(0, 59))
            rid = uuid.uuid4().hex[:16]
            rows.append(
                RequestMetric(
                    endpoint=ep,
                    method=method,
                    status_code=status,
                    response_time_ms=lat,
                    timestamp=ts,
                    request_id=rid,
                )
            )
            # org-scoped api log for a subset of /v1 + /org calls
            if ep.startswith("/v1") or ep.startswith("/org"):
                org = RNG.choice(active)
                keys = keys_by_org[org.id]
                log_rows.append(
                    ApiLog(
                        organization_id=org.id,
                        method=method,
                        path=ep,
                        status_code=status,
                        response_time_ms=lat,
                        ip_address=(
                            f"41.{RNG.randint(0, 255)}.{RNG.randint(0, 255)}.{RNG.randint(1, 254)}"
                        ),
                        user_agent="payswitch-sdk/1.4 (+https://payswitch.com.gh)",
                        request_id=rid,
                        api_key_id=keys[1].id,
                        api_key_prefix=keys[1].key_prefix,
                        error_detail=(f"HTTP {status}" if status >= 400 else None),
                        created_at=ts,
                        updated_at=ts,
                    )
                )
    # a handful of platform (admin) api logs with null org
    for _ in range(60):
        ts = NOW - timedelta(hours=RNG.uniform(0, buckets))
        method, ep = RNG.choice(
            [
                ("GET", "/admin/organizations"),
                ("GET", "/admin/score-requests/stats"),
                ("POST", "/admin/admins"),
                ("GET", "/admin/api-logs"),
            ]
        )
        status = RNG.choice([200, 200, 200, 403, 500])
        log_rows.append(
            ApiLog(
                organization_id=None,
                method=method,
                path=ep,
                status_code=status,
                response_time_ms=int(RNG.uniform(30, 400)),
                ip_address="102.176.0.10",
                user_agent="payswitch-admin-console/2.0",
                request_id=uuid.uuid4().hex[:16],
                user_id=None,
                error_detail=(f"HTTP {status}" if status >= 400 else None),
                created_at=ts,
                updated_at=ts,
            )
        )
    # bulk insert in chunks to keep memory sane
    for chunk_start in range(0, len(rows), 2000):
        db.add_all(rows[chunk_start : chunk_start + 2000])
        await db.flush()
    for chunk_start in range(0, len(log_rows), 2000):
        db.add_all(log_rows[chunk_start : chunk_start + 2000])
        await db.flush()
    print(f"[infra] {len(rows)} request_metrics + {len(log_rows)} api_logs.")


# ── Seed: alerts (all dashboards/severities/statuses) ───────────────────────
async def seed_alerts(db) -> None:
    today = NOW.strftime("%Y%m%d")
    specs = [
        # (metric, dashboard, severity, status, current, threshold, feature, model_type, action)
        (
            "feature_drift_psi",
            "model_ops",
            "critical",
            "firing",
            0.33,
            0.30,
            "credit_utilization",
            "credit_risk",
            "Investigate upstream data; consider retraining.",
        ),
        (
            "feature_drift_psi",
            "model_ops",
            "warning",
            "firing",
            0.24,
            0.20,
            "mobile_money_velocity",
            "credit_risk",
            "Monitor; drift approaching threshold.",
        ),
        (
            "champion_auc_drop",
            "model_ops",
            "warning",
            "resolved",
            0.018,
            0.02,
            None,
            "credit_risk",
            "AUC recovered after retraining v2.0.0.",
        ),
        (
            "api_p99_latency_ms",
            "infrastructure",
            "critical",
            "firing",
            5200.0,
            3000.0,
            None,
            None,
            "Scale API replicas; check DB pool saturation.",
        ),
        (
            "error_rate_pct",
            "infrastructure",
            "warning",
            "resolved",
            1.8,
            1.5,
            None,
            None,
            "5xx spike subsided after restart.",
        ),
        (
            "approval_rate_shift",
            "risk",
            "warning",
            "firing",
            0.71,
            0.65,
            None,
            None,
            "Approval rate elevated vs trailing baseline — review policy.",
        ),
        (
            "decline_rate_shift",
            "risk",
            "warning",
            "ok",
            0.28,
            0.40,
            None,
            None,
            "Within expected range.",
        ),
        (
            "demographic_parity_gap",
            "compliance",
            "critical",
            "firing",
            0.16,
            0.10,
            None,
            None,
            "Disparity across age bands exceeds policy; audit features.",
        ),
        (
            "explainability_coverage",
            "compliance",
            "warning",
            "resolved",
            0.97,
            0.95,
            None,
            None,
            "Coverage restored above 95%.",
        ),
        (
            "data_quality_score",
            "compliance",
            "warning",
            "firing",
            0.61,
            0.70,
            None,
            None,
            "Low DQS batch detected; verify source feeds.",
        ),
    ]
    for i, (metric, dash, sev, status, cur, thr, feat, mt, action) in enumerate(specs, 1):
        created = NOW - timedelta(hours=RNG.randint(1, 240))
        db.add(
            Alert(
                alert_id=f"ALT-{today}-{i:03d}",
                metric=metric,
                dashboard=dash,
                severity=sev,
                status=status,
                current_value=cur,
                threshold=thr,
                detail=f"{metric} = {cur} (threshold {thr})",
                feature=feat,
                model_type=mt,
                recommended_action=action,
                resolved_at=(
                    NOW - timedelta(hours=RNG.randint(1, 40)) if status == "resolved" else None
                ),
                created_at=created,
                updated_at=created,
            )
        )
    await db.flush()
    print(f"[alerts] {len(specs)} alerts across all dashboards.")


# ── Seed: audit logs ────────────────────────────────────────────────────────
async def seed_audit_logs(db, admins, orgs) -> None:
    actions = [
        ("admin", admins[0].email, None, "admin.login", "session", "Admin signed in"),
        (
            "admin",
            admins[0].email,
            orgs[0].id,
            "organization.create",
            "organization",
            f"Created org {orgs[0].short_name}",
        ),
        (
            "admin",
            admins[1].email,
            None,
            "model.promote",
            "model_version",
            "Promoted credit_risk v2.0.0 to production",
        ),
        (
            "admin",
            admins[2].email,
            orgs[3].id,
            "organization.suspend",
            "organization",
            "Suspended org for overdue settlement",
        ),
        (
            "org_user",
            f"admin@{orgs[0].short_name}.com.gh",
            orgs[0].id,
            "api_key.create",
            "api_key",
            "Generated production key",
        ),
        (
            "org_user",
            f"dev@{orgs[1].short_name}.com.gh",
            orgs[1].id,
            "webhook.update",
            "webhook_config",
            "Updated webhook URL",
        ),
        (
            "api_key",
            "sk_live_****",
            orgs[1].id,
            "score.create",
            "score_request",
            "Submitted scoring request via API",
        ),
        (
            "system",
            None,
            None,
            "model.drift_check",
            "model_metric",
            "Scheduled drift evaluation completed",
        ),
    ]
    for actor_type, email, org_id, action, rtype, desc in actions:
        ts = NOW - timedelta(hours=RNG.randint(1, 300))
        db.add(
            AuditLog(
                actor_type=actor_type,
                actor_id=(short_id(8) if email else None),
                actor_email=email,
                organization_id=org_id,
                action=action,
                resource_type=rtype,
                resource_id=short_id(10),
                description=desc,
                ip_address="102.176.0.10",
                user_agent="payswitch-admin-console/2.0",
                request_id=uuid.uuid4().hex[:16],
                changes=(
                    {"before": {"status": "active"}, "after": {"status": "suspended"}}
                    if action.endswith("suspend")
                    else None
                ),
                created_at=ts,
                updated_at=ts,
            )
        )
    await db.flush()
    print(f"[audit] {len(actions)} audit log entries.")


# ── Orchestration ───────────────────────────────────────────────────────────
async def main() -> int:
    parser = argparse.ArgumentParser(description="Seed PaySwitch demo data")
    parser.add_argument("--reset", action="store_true", help="Truncate demo tables before seeding")
    parser.add_argument("--scale", choices=list(SCALES), default="medium")
    parser.add_argument(
        "--minimal",
        action="store_true",
        help="Seed only logins + config (orgs, users, keys, RBAC, models, data "
        "sources) and NO score history. For the live demo, so the only "
        "score requests are the ones run through the 5 simulation records.",
    )
    args = parser.parse_args()
    cfg = SCALES[args.scale]

    # Anchor the data window to the DATABASE clock, not this host's clock.
    # Otherwise clock skew between the seeding machine and the live server
    # leaves the "today" / "last 24h" dashboard windows empty.
    global NOW
    async with async_session_factory() as _db:
        db_now = (await _db.execute(text("SELECT now()"))).scalar()
    if db_now is not None:
        NOW = db_now
        print(f"[clock] anchored data window to DB now() = {NOW.isoformat()}")

    await normalize_enums()

    if args.reset:
        await reset_tables()
    elif await already_seeded():
        print("[abort] Demo data already present. Re-run with --reset to rebuild.")
        return 1

    async with async_session_factory() as db:
        print(f"[seed] scale={args.scale} window={cfg['days']}d")
        admins = await seed_admins(db)
        orgs = await seed_orgs(db)
        users_by_org = await seed_org_users(db, orgs)
        keys_by_org = await seed_api_keys(db, orgs, users_by_org)
        await seed_webhooks(db, orgs)
        await seed_models(db, admins)
        await seed_data_sources(db, admins)
        # Wire up RBAC: (re)create system roles/permissions and assign the
        # SUPER_ADMIN role to every admin + the ADMIN role to every org user.
        # This is what makes the seeded logins actually carry permissions, and
        # it rebuilds the role rows that --reset's CASCADE truncates.
        from app.services.permission_seeder import seed_permissions_and_roles

        await seed_permissions_and_roles(db)
        await db.commit()
        print("[rbac] roles + permissions seeded and assigned to demo users.")

        if args.minimal:
            # Logins + config only — no score history. The 5 simulation records
            # run live during the demo become the only score requests.
            await seed_alerts(db)
            await db.commit()
            print(
                "[minimal] logins + models + data sources + alerts seeded; "
                "score history intentionally empty."
            )
        else:
            await seed_score_requests(db, orgs, users_by_org, keys_by_org, cfg)
            await db.commit()

            await seed_batch_jobs(db, orgs, users_by_org)
            await seed_request_metrics(db, orgs, keys_by_org, cfg)
            await seed_alerts(db)
            await seed_audit_logs(db, admins, orgs)
            await db.commit()

    print("\n[done] Demo data seeded.")
    print(f"  Admin login : admin@payswitch.demo / {DEMO_PASSWORD}")
    print(f"  Org logins  : admin@<short>.com.gh / {DEMO_PASSWORD}  (e.g. admin@gcm.com.gh)")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
