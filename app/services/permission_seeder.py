"""Permission seeder — runs once at startup.

Seeds the full permission catalog and the two system roles (SUPER_ADMIN, ADMIN).
Idempotent — safe to call on every startup.

Also handles migration of existing users from the legacy role_label/role enum
to the new RBAC tables (inserts into admin_user_roles / org_user_roles).
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# ── Permission catalog ────────────────────────────────────────────────────────

PERMISSIONS: list[dict] = [
    # ── Platform scope ────────────────────────────────────────────────────
    # Organizations
    {"code": "admin.organizations.create",    "group_name": "Organizations",    "description": "Create new organizations",                            "scope": "platform"},
    {"code": "admin.organizations.read",      "group_name": "Organizations",    "description": "View organizations and their members",                "scope": "platform"},
    {"code": "admin.organizations.update",    "group_name": "Organizations",    "description": "Update organization details and member profiles",     "scope": "platform"},
    {"code": "admin.organizations.provision", "group_name": "Organizations",    "description": "Provision API keys and sandbox for an organization",  "scope": "platform"},
    {"code": "admin.organizations.suspend",   "group_name": "Organizations",    "description": "Suspend or reactivate an organization",               "scope": "platform"},
    # Training data
    {"code": "admin.training_data.upload",    "group_name": "Training Data",    "description": "Upload training files and manage field mappings",     "scope": "platform"},
    {"code": "admin.training_data.read",      "group_name": "Training Data",    "description": "View training uploads, status, and mappings",         "scope": "platform"},
    {"code": "admin.training_data.approve",   "group_name": "Training Data",    "description": "Approve or reject training data uploads",             "scope": "platform"},
    # Data sources
    {"code": "admin.sources.manage",          "group_name": "Data Sources",     "description": "Create and manage data source definitions",           "scope": "platform"},
    {"code": "admin.sources.read",            "group_name": "Data Sources",     "description": "View data sources and mapping templates",             "scope": "platform"},
    # Platform role management
    {"code": "admin.roles.read",              "group_name": "Role Management",  "description": "View platform roles and their permissions",           "scope": "platform"},
    {"code": "admin.roles.manage",            "group_name": "Role Management",  "description": "Create, update, and delete platform roles",           "scope": "platform"},
    {"code": "admin.roles.assign",            "group_name": "Role Management",  "description": "Assign and unassign roles to platform users",         "scope": "platform"},
    # Monitoring
    {"code": "monitoring.risk",               "group_name": "Monitoring",       "description": "View risk monitoring dashboard",                      "scope": "platform"},
    {"code": "monitoring.infrastructure",     "group_name": "Monitoring",       "description": "View infrastructure monitoring dashboard",            "scope": "platform"},
    {"code": "monitoring.model_ops",          "group_name": "Monitoring",       "description": "View model operations dashboard",                     "scope": "platform"},
    {"code": "monitoring.compliance",         "group_name": "Monitoring",       "description": "View compliance monitoring dashboard",                "scope": "platform"},
    {"code": "monitoring.alerts",             "group_name": "Monitoring",       "description": "View and manage platform alerts",                     "scope": "platform"},
    # Models & Rules
    {"code": "models.read",                   "group_name": "Models & Rules",   "description": "View current champion model metadata",               "scope": "platform"},
    {"code": "rules.evaluate",                "group_name": "Models & Rules",   "description": "Run sandbox rule evaluations",                        "scope": "platform"},

    # ── Org scope ─────────────────────────────────────────────────────────
    # Scoring
    {"code": "score_requests.create",         "group_name": "Scoring",          "description": "Submit credit score requests",                        "scope": "org"},
    {"code": "score_requests.list",           "group_name": "Scoring",          "description": "List credit score requests",                          "scope": "org"},
    {"code": "score_requests.read",           "group_name": "Scoring",          "description": "View individual score request and scoring result",    "scope": "org"},
    {"code": "score_requests.override",       "group_name": "Scoring",          "description": "Override a REFER decision manually",                  "scope": "org"},
    {"code": "score_requests.report_outcome", "group_name": "Scoring",          "description": "Record a loan decision outcome",                      "scope": "org"},
    {"code": "score_requests.report_performance", "group_name": "Scoring",      "description": "Report loan performance updates",                     "scope": "org"},
    # Batch scoring
    {"code": "batch_scoring.create",          "group_name": "Batch Scoring",    "description": "Submit batch scoring jobs",                           "scope": "org"},
    {"code": "batch_scoring.list",            "group_name": "Batch Scoring",    "description": "List batch scoring jobs",                             "scope": "org"},
    {"code": "batch_scoring.read",            "group_name": "Batch Scoring",    "description": "View batch job status and results",                   "scope": "org"},
    {"code": "batch_scoring.cancel",          "group_name": "Batch Scoring",    "description": "Cancel a queued batch job",                           "scope": "org"},
    # Bureau
    {"code": "bureau.lookup",                 "group_name": "Bureau",           "description": "Look up credit bureau data for an applicant",         "scope": "org"},
    # Org settings
    {"code": "org.read",                      "group_name": "Organization",     "description": "View organization profile",                           "scope": "org"},
    {"code": "org.update",                    "group_name": "Organization",     "description": "Update organization profile",                         "scope": "org"},
    # User management
    {"code": "users.invite",                  "group_name": "User Management",  "description": "Invite new users to the organization",                "scope": "org"},
    {"code": "users.list",                    "group_name": "User Management",  "description": "List organization users",                             "scope": "org"},
    {"code": "users.update",                  "group_name": "User Management",  "description": "Update a user's name or role",                        "scope": "org"},
    {"code": "users.suspend",                 "group_name": "User Management",  "description": "Suspend or reactivate organization users",            "scope": "org"},
    {"code": "users.delete",                  "group_name": "User Management",  "description": "Remove a user from the organization",                 "scope": "org"},
    # API keys
    {"code": "api_keys.create",               "group_name": "API Keys",         "description": "Generate new API keys",                               "scope": "org"},
    {"code": "api_keys.list",                 "group_name": "API Keys",         "description": "List API keys",                                       "scope": "org"},
    {"code": "api_keys.revoke",               "group_name": "API Keys",         "description": "Revoke an API key",                                   "scope": "org"},
    # Webhooks
    {"code": "webhooks.manage",               "group_name": "Webhooks",         "description": "Create, update, and delete webhook configurations",   "scope": "org"},
    {"code": "webhooks.list",                 "group_name": "Webhooks",         "description": "List webhooks and supported events",                  "scope": "org"},
    # API logs
    {"code": "api_logs.read",                 "group_name": "API Logs",         "description": "View API request logs",                               "scope": "org"},
    # Role management
    {"code": "roles.read",                    "group_name": "Role Management",  "description": "View organization roles and their permissions",       "scope": "org"},
    {"code": "roles.manage",                  "group_name": "Role Management",  "description": "Create, update, and delete organization roles",       "scope": "org"},
    {"code": "roles.assign",                  "group_name": "Role Management",  "description": "Assign and unassign roles to organization users",     "scope": "org"},
]

# Codes for the ADMIN system role short-circuit
ALL_ORG_PERMISSION_CODES: frozenset[str] = frozenset(
    p["code"] for p in PERMISSIONS if p["scope"] == "org"
)

# All permission codes indexed by code for fast lookup
ALL_PERMISSION_CODES: frozenset[str] = frozenset(p["code"] for p in PERMISSIONS)


async def seed_permissions_and_roles(db: AsyncSession) -> None:
    """Idempotent seed — runs at every startup.

    1. Upserts all Permission rows.
    2. Ensures SUPER_ADMIN and ADMIN system roles exist.
    3. Migrates existing users: inserts role assignments if they don't already have one.
    """
    from app.models.permission import AdminUserRole, OrgUserRole, Permission, Role

    # ── 1. Upsert permissions ─────────────────────────────────────────────
    existing_codes = set(
        (await db.execute(select(Permission.code))).scalars().all()
    )
    new_permissions = [
        Permission(
            code=p["code"],
            group_name=p["group_name"],
            description=p["description"],
            scope=p["scope"],
        )
        for p in PERMISSIONS
        if p["code"] not in existing_codes
    ]
    if new_permissions:
        db.add_all(new_permissions)
        await db.flush()
        logger.info("[seeder] Inserted %d new permissions", len(new_permissions))

    # ── 2. Ensure system roles exist ──────────────────────────────────────
    super_admin_role = await _ensure_system_role(db, "SUPER_ADMIN", "platform")
    admin_role = await _ensure_system_role(db, "ADMIN", "org")

    # ── 3. Migrate existing users (idempotent) ────────────────────────────
    await _migrate_admin_users(db, super_admin_role.id)
    await _migrate_org_users(db, admin_role.id)

    await db.commit()
    logger.info("[seeder] Permission seed complete")


async def _ensure_system_role(db: AsyncSession, name: str, scope: str):
    from app.models.permission import Role

    role = (await db.execute(
        select(Role).where(Role.name == name, Role.is_system == True)
    )).scalar_one_or_none()

    if not role:
        role = Role(name=name, scope=scope, is_system=True)
        db.add(role)
        await db.flush()
        logger.info("[seeder] Created system role: %s", name)

    return role


async def _migrate_admin_users(db: AsyncSession, super_admin_role_id) -> None:
    """Assign SUPER_ADMIN role to all existing admin users who don't have a role yet."""
    from app.models.permission import AdminUserRole
    from app.models.user import AdminUser

    admins = (await db.execute(select(AdminUser))).scalars().all()
    for admin in admins:
        already = (await db.execute(
            select(AdminUserRole).where(AdminUserRole.admin_user_id == admin.id)
        )).scalar_one_or_none()
        if not already:
            db.add(AdminUserRole(
                admin_user_id=admin.id,
                role_id=super_admin_role_id,
            ))
    if admins:
        await db.flush()
        logger.info("[seeder] Migrated %d admin users → SUPER_ADMIN role", len(admins))


async def _migrate_org_users(db: AsyncSession, admin_role_id) -> None:
    """Assign ADMIN role to all existing org users who don't have a role yet."""
    from app.models.permission import OrgUserRole
    from app.models.user import OrgUser, OrgUserStatus

    users = (await db.execute(
        select(OrgUser).where(OrgUser.status != OrgUserStatus.REMOVED)
    )).scalars().all()

    for user in users:
        already = (await db.execute(
            select(OrgUserRole).where(OrgUserRole.org_user_id == user.id)
        )).scalar_one_or_none()
        if not already:
            db.add(OrgUserRole(
                org_user_id=user.id,
                role_id=admin_role_id,
            ))
    if users:
        await db.flush()
        logger.info("[seeder] Migrated %d org users → ADMIN role", len(users))
