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
    {"code": "admin.organizations.create",        "name": "Create Organization",        "group_name": "Organizations",   "description": "Create new organizations",                            "scope": "platform"},
    {"code": "admin.organizations.read",          "name": "View Organizations",         "group_name": "Organizations",   "description": "View organizations and their members",                "scope": "platform"},
    {"code": "admin.organizations.update",        "name": "Update Organization",        "group_name": "Organizations",   "description": "Update organization details and member profiles",     "scope": "platform"},
    {"code": "admin.organizations.provision",     "name": "Provision Organization",     "group_name": "Organizations",   "description": "Provision API keys and sandbox for an organization",  "scope": "platform"},
    {"code": "admin.organizations.suspend",       "name": "Suspend Organization",       "group_name": "Organizations",   "description": "Suspend or reactivate an organization",               "scope": "platform"},
    # Training data
    {"code": "admin.training_data.upload",        "name": "Upload Training Data",       "group_name": "Training Data",   "description": "Upload training files and manage field mappings",     "scope": "platform"},
    {"code": "admin.training_data.read",          "name": "View Training Data",         "group_name": "Training Data",   "description": "View training uploads, status, and mappings",         "scope": "platform"},
    {"code": "admin.training_data.approve",       "name": "Approve Training Data",      "group_name": "Training Data",   "description": "Approve or reject training data uploads",             "scope": "platform"},
    # Data sources
    {"code": "admin.sources.manage",              "name": "Manage Data Sources",        "group_name": "Data Sources",    "description": "Create and manage data source definitions",           "scope": "platform"},
    {"code": "admin.sources.read",                "name": "View Data Sources",          "group_name": "Data Sources",    "description": "View data sources and mapping templates",             "scope": "platform"},
    # Platform role management
    {"code": "admin.roles.read",                  "name": "View Platform Roles",        "group_name": "Role Management", "description": "View platform roles and their permissions",           "scope": "platform"},
    {"code": "admin.roles.manage",                "name": "Manage Platform Roles",      "group_name": "Role Management", "description": "Create, update, and delete platform roles",           "scope": "platform"},
    {"code": "admin.roles.assign",                "name": "Assign Platform Roles",      "group_name": "Role Management", "description": "Assign and unassign roles to platform users",         "scope": "platform"},
    # Platform admin user management
    {"code": "admin.admins.invite",               "name": "Invite Platform Admin",      "group_name": "Admin Users",     "description": "Invite new platform administrators",                  "scope": "platform"},
    {"code": "admin.admins.list",                 "name": "List Platform Admins",       "group_name": "Admin Users",     "description": "List platform administrators",                        "scope": "platform"},
    {"code": "admin.admins.read",                 "name": "View Platform Admin",        "group_name": "Admin Users",     "description": "View an individual platform administrator",           "scope": "platform"},
    {"code": "admin.admins.update",               "name": "Update Platform Admin",      "group_name": "Admin Users",     "description": "Update a platform administrator's name or role",      "scope": "platform"},
    {"code": "admin.admins.suspend",              "name": "Suspend Platform Admin",     "group_name": "Admin Users",     "description": "Suspend or reactivate platform administrators",       "scope": "platform"},
    {"code": "admin.admins.delete",               "name": "Remove Platform Admin",      "group_name": "Admin Users",     "description": "Remove a platform administrator",                     "scope": "platform"},
    # Monitoring
    {"code": "monitoring.risk",                   "name": "Risk Monitoring",            "group_name": "Monitoring",      "description": "View risk monitoring dashboard",                      "scope": "platform"},
    {"code": "monitoring.infrastructure",         "name": "Infrastructure Monitoring",  "group_name": "Monitoring",      "description": "View infrastructure monitoring dashboard",            "scope": "platform"},
    {"code": "monitoring.model_ops",              "name": "Model Operations",           "group_name": "Monitoring",      "description": "View model operations dashboard",                     "scope": "platform"},
    {"code": "monitoring.compliance",             "name": "Compliance Monitoring",      "group_name": "Monitoring",      "description": "View compliance monitoring dashboard",                "scope": "platform"},
    {"code": "monitoring.alerts",                 "name": "Platform Alerts",            "group_name": "Monitoring",      "description": "View and manage platform alerts",                     "scope": "platform"},
    # Models & Rules
    {"code": "models.read",                       "name": "View Champion Model",        "group_name": "Models & Rules",  "description": "View current champion model metadata",                "scope": "platform"},
    {"code": "rules.evaluate",                    "name": "Evaluate Rules",             "group_name": "Models & Rules",  "description": "Run sandbox rule evaluations",                        "scope": "platform"},
    # Platform API Logs
    {"code": "admin.api_logs.read",               "name": "View Platform API Logs",     "group_name": "Platform Logs",   "description": "View platform-level API request logs",                "scope": "platform"},

    # ── Org scope ─────────────────────────────────────────────────────────
    # Scoring
    {"code": "score_requests.create",             "name": "Submit Score Request",       "group_name": "Scoring",         "description": "Submit credit score requests",                        "scope": "org"},
    {"code": "score_requests.list",               "name": "List Score Requests",        "group_name": "Scoring",         "description": "List credit score requests",                          "scope": "org"},
    {"code": "score_requests.read",               "name": "View Score Request",         "group_name": "Scoring",         "description": "View individual score request and scoring result",    "scope": "org"},
    {"code": "score_requests.override",           "name": "Override Decision",          "group_name": "Scoring",         "description": "Override a REFER decision manually",                  "scope": "org"},
    {"code": "score_requests.report_outcome",     "name": "Report Loan Outcome",        "group_name": "Scoring",         "description": "Record a loan decision outcome",                      "scope": "org"},
    {"code": "score_requests.report_performance", "name": "Report Loan Performance",    "group_name": "Scoring",         "description": "Report loan performance updates",                     "scope": "org"},
    # Batch scoring
    {"code": "batch_scoring.create",              "name": "Submit Batch Job",           "group_name": "Batch Scoring",   "description": "Submit batch scoring jobs",                           "scope": "org"},
    {"code": "batch_scoring.list",                "name": "List Batch Jobs",            "group_name": "Batch Scoring",   "description": "List batch scoring jobs",                             "scope": "org"},
    {"code": "batch_scoring.read",                "name": "View Batch Job",             "group_name": "Batch Scoring",   "description": "View batch job status and results",                   "scope": "org"},
    {"code": "batch_scoring.cancel",              "name": "Cancel Batch Job",           "group_name": "Batch Scoring",   "description": "Cancel a queued batch job",                           "scope": "org"},
    # Bureau
    {"code": "bureau.lookup",                     "name": "Bureau Lookup",              "group_name": "Bureau",          "description": "Look up credit bureau data for an applicant",         "scope": "org"},
    # Org settings
    {"code": "org.read",                          "name": "View Organization Profile",  "group_name": "Organization",    "description": "View organization profile",                           "scope": "org"},
    {"code": "org.update",                        "name": "Update Organization Profile","group_name": "Organization",    "description": "Update organization profile",                         "scope": "org"},
    # User management
    {"code": "users.invite",                      "name": "Invite User",                "group_name": "User Management", "description": "Invite new users to the organization",                "scope": "org"},
    {"code": "users.list",                        "name": "List Users",                 "group_name": "User Management", "description": "List organization users",                             "scope": "org"},
    {"code": "users.update",                      "name": "Update User",                "group_name": "User Management", "description": "Update a user's name or role",                        "scope": "org"},
    {"code": "users.suspend",                     "name": "Suspend User",               "group_name": "User Management", "description": "Suspend or reactivate organization users",            "scope": "org"},
    {"code": "users.delete",                      "name": "Remove User",                "group_name": "User Management", "description": "Remove a user from the organization",                 "scope": "org"},
    # API keys
    {"code": "api_keys.create",                   "name": "Create API Key",             "group_name": "API Keys",        "description": "Generate new API keys",                               "scope": "org"},
    {"code": "api_keys.list",                     "name": "List API Keys",              "group_name": "API Keys",        "description": "List API keys",                                       "scope": "org"},
    {"code": "api_keys.revoke",                   "name": "Revoke API Key",             "group_name": "API Keys",        "description": "Revoke an API key",                                   "scope": "org"},
    # Webhooks
    {"code": "webhooks.manage",                   "name": "Manage Webhooks",            "group_name": "Webhooks",        "description": "Create, update, and delete webhook configurations",   "scope": "org"},
    {"code": "webhooks.list",                     "name": "List Webhooks",              "group_name": "Webhooks",        "description": "List webhooks and supported events",                  "scope": "org"},
    # API logs
    {"code": "api_logs.read",                     "name": "View API Logs",              "group_name": "API Logs",        "description": "View API request logs",                               "scope": "org"},
    # Role management
    {"code": "roles.read",                        "name": "View Roles",                 "group_name": "Role Management", "description": "View organization roles and their permissions",       "scope": "org"},
    {"code": "roles.manage",                      "name": "Manage Roles",               "group_name": "Role Management", "description": "Create, update, and delete organization roles",       "scope": "org"},
    {"code": "roles.assign",                      "name": "Assign Roles",               "group_name": "Role Management", "description": "Assign and unassign roles to organization users",     "scope": "org"},
]

# Codes for the ADMIN system role short-circuit
ALL_ORG_PERMISSION_CODES: frozenset[str] = frozenset(
    p["code"] for p in PERMISSIONS if p["scope"] == "org"
)

# All permission codes indexed by code for fast lookup
ALL_PERMISSION_CODES: frozenset[str] = frozenset(p["code"] for p in PERMISSIONS)

# All platform-scoped codes (convenience for SUPER_ADMIN sync)
ALL_PLATFORM_PERMISSION_CODES: frozenset[str] = frozenset(
    p["code"] for p in PERMISSIONS if p["scope"] == "platform"
)


# ── System platform role definitions ──────────────────────────────────────────
#
# Four operational roles aligned 1:1 with the client's role-specific
# dashboards, plus SUPER_ADMIN as the break-glass tier.  Each set is the
# authoritative permission matrix — the seeder syncs RolePermission rows
# to match exactly (adds missing, removes stale).
#
# Shared across operational roles:
#   * monitoring.alerts — the unified alert feed is filtered per-dashboard
#     on the FE, so one permission suffices for all four personas.
#
# Only SUPER_ADMIN holds admin.organizations.{create,update,provision,suspend}
# and admin.roles.* — those are administration, not day-to-day operations.

SYSTEM_PLATFORM_ROLES: dict[str, frozenset[str]] = {
    # Break-glass + bootstrap.  Explicitly enumerated (not implicit "*") so
    # RolePermission rows match the data model.  The wildcard short-circuit
    # in permissions._admin_permissions() is an optimization, not a source
    # of truth.
    "SUPER_ADMIN": ALL_PLATFORM_PERMISSION_CODES,

    # Risk Dashboard persona — approval rates, score distributions,
    # delinquency trends.  Gets rules.evaluate + models.read (the FE
    # monitoring guide co-locates these with the risk dashboard) and
    # read-only org context for portfolio segmentation.
    "RISK_ANALYST": frozenset({
        "monitoring.risk",
        "monitoring.alerts",
        "models.read",
        "rules.evaluate",
        "admin.organizations.read",
    }),

    # ModelOps Dashboard persona — champion AUC, PSI by feature,
    # retraining history, challenger comparison.  Full training-data and
    # data-source lifecycle because retraining/challenger work needs write
    # access to those.
    "MODEL_OPS_ENGINEER": frozenset({
        "monitoring.model_ops",
        "monitoring.alerts",
        "models.read",
        "rules.evaluate",
        "admin.training_data.upload",
        "admin.training_data.read",
        "admin.training_data.approve",
        "admin.sources.manage",
        "admin.sources.read",
    }),

    # Compliance Dashboard persona — fairness metrics, audit log volume,
    # regulation breach alerts, data subject requests.  Read-only access to
    # the risk dashboard (fairness analysis needs approval/decline signals)
    # and to org context for incident attribution.
    "COMPLIANCE_OFFICER": frozenset({
        "monitoring.compliance",
        "monitoring.risk",
        "monitoring.alerts",
        "admin.api_logs.read",
        "admin.organizations.read",
    }),

    # Infrastructure Dashboard persona — API latency, error rates, pod
    # health, data pipeline success rates.  Read-only org + source context
    # for correlating tenant / pipeline issues during incidents.
    "INFRASTRUCTURE_ENGINEER": frozenset({
        "monitoring.infrastructure",
        "monitoring.alerts",
        "admin.api_logs.read",
        "admin.organizations.read",
        "admin.sources.read",
    }),
}

# ── Sanity checks (fail fast at import time if matrix drifts) ─────────────────

for _role_name, _codes in SYSTEM_PLATFORM_ROLES.items():
    _unknown = _codes - ALL_PLATFORM_PERMISSION_CODES
    if _unknown:
        raise RuntimeError(
            f"SYSTEM_PLATFORM_ROLES[{_role_name}] references unknown "
            f"permission codes: {sorted(_unknown)}"
        )


async def seed_permissions_and_roles(db: AsyncSession) -> None:
    """Idempotent seed — runs at every startup.

    1. Upserts all Permission rows.
    2. Ensures system roles exist:
         - ADMIN (org scope)
         - SUPER_ADMIN, RISK_ANALYST, MODEL_OPS_ENGINEER, COMPLIANCE_OFFICER,
           INFRASTRUCTURE_ENGINEER (platform scope)
    3. Syncs each platform system role's RolePermission rows against the
       SYSTEM_PLATFORM_ROLES matrix — additive for new permissions,
       pruning stale ones.
    4. Migrates existing users: inserts role assignments if they don't already have one.
    """
    from app.models.permission import AdminUserRole, OrgUserRole, Permission, Role

    # ── 1. Upsert permissions ─────────────────────────────────────────────
    existing_rows: dict[str, Permission] = {
        row.code: row
        for row in (await db.execute(select(Permission))).scalars().all()
    }

    new_permissions: list[Permission] = []
    updated = 0
    for p in PERMISSIONS:
        if p["code"] not in existing_rows:
            new_permissions.append(Permission(
                code=p["code"],
                name=p["name"],
                group_name=p["group_name"],
                description=p["description"],
                scope=p["scope"],
            ))
        else:
            # Keep name/description/group_name in sync with the catalog
            row = existing_rows[p["code"]]
            changed = False
            if row.name != p["name"]:
                row.name = p["name"]
                changed = True
            if row.description != p["description"]:
                row.description = p["description"]
                changed = True
            if row.group_name != p["group_name"]:
                row.group_name = p["group_name"]
                changed = True
            if changed:
                updated += 1

    if new_permissions:
        db.add_all(new_permissions)
        await db.flush()
        logger.info("[seeder] Inserted %d new permissions", len(new_permissions))
    if updated:
        await db.flush()
        logger.info("[seeder] Updated %d existing permissions", updated)

    # ── 2. Ensure system roles exist ──────────────────────────────────────
    # Org-side (unchanged)
    admin_role = await _ensure_system_role(db, "ADMIN", "org")

    # Platform-side: SUPER_ADMIN + four dashboard-aligned operational roles.
    platform_roles: dict[str, Role] = {}
    for role_name in SYSTEM_PLATFORM_ROLES:
        platform_roles[role_name] = await _ensure_system_role(
            db, role_name, "platform"
        )

    # ── 2b. Sync RolePermission rows to match the matrix ─────────────────
    # Build a code→Permission map once (all permissions are now seeded).
    perm_by_code: dict[str, Permission] = {
        row.code: row
        for row in (await db.execute(select(Permission))).scalars().all()
    }
    for role_name, codes in SYSTEM_PLATFORM_ROLES.items():
        await _sync_role_permissions(
            db, platform_roles[role_name], codes, perm_by_code
        )

    # ── 3. Migrate existing users (idempotent) ────────────────────────────
    super_admin_role = platform_roles["SUPER_ADMIN"]
    await _migrate_admin_users(db, super_admin_role.id)
    await _migrate_org_users(db, admin_role.id)

    await db.commit()
    logger.info("[seeder] Permission seed complete")


async def _sync_role_permissions(
    db: AsyncSession,
    role,
    desired_codes: frozenset[str] | set[str],
    perm_by_code: dict,
) -> None:
    """Upsert RolePermission rows so *role* holds exactly *desired_codes*.

    Additive on deploys that add new permissions, and prunes stale rows if a
    permission is removed from a role's declared set.  Idempotent.
    """
    from app.models.permission import RolePermission

    desired_ids = {
        perm_by_code[code].id for code in desired_codes if code in perm_by_code
    }

    # Current rows for this role
    existing_rows = (
        await db.execute(
            select(RolePermission).where(RolePermission.role_id == role.id)
        )
    ).scalars().all()
    existing_ids = {row.permission_id for row in existing_rows}

    # Add missing
    to_add = desired_ids - existing_ids
    for pid in to_add:
        db.add(RolePermission(role_id=role.id, permission_id=pid))

    # Remove stale (only applies to system roles whose declared set shrank
    # between deploys — custom roles are never passed through this helper)
    to_remove = existing_ids - desired_ids
    for row in existing_rows:
        if row.permission_id in to_remove:
            await db.delete(row)

    if to_add or to_remove:
        await db.flush()
        logger.info(
            "[seeder] Synced role '%s': +%d / -%d permissions",
            role.name, len(to_add), len(to_remove),
        )


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
