"""Cross-reference tests for the system platform role matrix.

These tests do NOT touch the database. They validate three invariants:

1. Every code in SYSTEM_PLATFORM_ROLES exists in the PERMISSIONS catalog.
2. Every platform permission code that appears in a router's
   ``require_permission("...")`` call is declared in PERMISSIONS.
3. Each of the five dashboard permissions is granted to exactly the
   persona roles documented in docs and nothing more.

If any of these drift, the test fails fast — cheaper than discovering it
by a 403 in staging.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.services.permission_seeder import (
    ALL_PLATFORM_PERMISSION_CODES,
    PERMISSIONS,
    SYSTEM_PLATFORM_ROLES,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
ADMIN_ROUTER_DIRS = [
    REPO_ROOT / "app" / "routers" / "monitoring",
]
ADMIN_ROUTER_FILES = [
    REPO_ROOT / "app" / "routers" / "admin_organizations.py",
    REPO_ROOT / "app" / "routers" / "admin_sources.py",
    REPO_ROOT / "app" / "routers" / "admin_training_data.py",
    REPO_ROOT / "app" / "routers" / "admin_api_logs.py",
]


# ── 1. Matrix integrity ───────────────────────────────────────────────────────


def test_matrix_only_references_declared_platform_codes():
    """Every code in SYSTEM_PLATFORM_ROLES must be a real platform permission."""
    for role_name, codes in SYSTEM_PLATFORM_ROLES.items():
        unknown = codes - ALL_PLATFORM_PERMISSION_CODES
        assert not unknown, (
            f"Role {role_name!r} references unknown permission codes: {sorted(unknown)}"
        )


def test_super_admin_holds_every_platform_permission():
    """SUPER_ADMIN must remain the full-platform break-glass role."""
    assert SYSTEM_PLATFORM_ROLES["SUPER_ADMIN"] == ALL_PLATFORM_PERMISSION_CODES


def test_all_five_roles_defined():
    expected = {
        "SUPER_ADMIN",
        "RISK_ANALYST",
        "MODEL_OPS_ENGINEER",
        "COMPLIANCE_OFFICER",
        "INFRASTRUCTURE_ENGINEER",
    }
    assert set(SYSTEM_PLATFORM_ROLES.keys()) == expected


# ── 2. Router ↔ catalog drift ────────────────────────────────────────────────


def _extract_require_permission_codes(path: Path) -> set[str]:
    """Return every string literal passed to require_permission(...) in *path*."""
    tree = ast.parse(path.read_text())
    codes: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        func_name = (
            func.attr
            if isinstance(func, ast.Attribute)
            else func.id
            if isinstance(func, ast.Name)
            else None
        )
        if func_name != "require_permission":
            continue
        if node.args and isinstance(node.args[0], ast.Constant):
            if isinstance(node.args[0].value, str):
                codes.add(node.args[0].value)
    return codes


def _all_admin_router_files() -> list[Path]:
    files = list(ADMIN_ROUTER_FILES)
    for d in ADMIN_ROUTER_DIRS:
        files.extend(p for p in d.glob("*.py") if p.name != "__init__.py")
    return files


def test_every_enforced_platform_permission_is_catalogued():
    catalog_codes = {p["code"] for p in PERMISSIONS}
    missing: dict[str, set[str]] = {}
    for path in _all_admin_router_files():
        for code in _extract_require_permission_codes(path):
            if code not in catalog_codes:
                missing.setdefault(str(path.relative_to(REPO_ROOT)), set()).add(code)
    assert not missing, (
        f"Routers enforce permission codes that aren't in the seeder catalog: {missing}"
    )


# ── 3. Dashboard → role grant matrix ─────────────────────────────────────────


@pytest.mark.parametrize(
    "perm_code,expected_roles",
    [
        (
            "monitoring.risk",
            {"SUPER_ADMIN", "RISK_ANALYST", "COMPLIANCE_OFFICER"},
        ),
        (
            "monitoring.model_ops",
            {"SUPER_ADMIN", "MODEL_OPS_ENGINEER"},
        ),
        (
            "monitoring.compliance",
            {"SUPER_ADMIN", "COMPLIANCE_OFFICER"},
        ),
        (
            "monitoring.infrastructure",
            {"SUPER_ADMIN", "INFRASTRUCTURE_ENGINEER"},
        ),
        (
            "monitoring.alerts",
            {
                "SUPER_ADMIN",
                "RISK_ANALYST",
                "MODEL_OPS_ENGINEER",
                "COMPLIANCE_OFFICER",
                "INFRASTRUCTURE_ENGINEER",
            },
        ),
    ],
)
def test_dashboard_permissions_granted_to_expected_roles(perm_code, expected_roles):
    actual = {role_name for role_name, codes in SYSTEM_PLATFORM_ROLES.items() if perm_code in codes}
    assert actual == expected_roles, (
        f"{perm_code!r} is granted to {actual}; expected {expected_roles}"
    )


@pytest.mark.parametrize(
    "admin_only_code",
    [
        "admin.organizations.create",
        "admin.organizations.update",
        "admin.organizations.provision",
        "admin.organizations.suspend",
        "admin.roles.read",
        "admin.roles.manage",
        "admin.roles.assign",
    ],
)
def test_administration_permissions_restricted_to_super_admin(admin_only_code):
    """Org mutation and platform-role management stay in SUPER_ADMIN only."""
    holders = {
        role_name for role_name, codes in SYSTEM_PLATFORM_ROLES.items() if admin_only_code in codes
    }
    assert holders == {"SUPER_ADMIN"}, f"{admin_only_code!r} leaked to {holders - {'SUPER_ADMIN'}}"


@pytest.mark.parametrize(
    "perm_code,expected_holders",
    [
        # Cross-org read access goes to SUPER_ADMIN, RISK_ANALYST (portfolio
        # drill-down), COMPLIANCE_OFFICER (audits) — but NOT MODEL_OPS
        # (aggregate view only) or INFRASTRUCTURE_ENGINEER (no business data).
        (
            "admin.score_requests.read",
            {"SUPER_ADMIN", "RISK_ANALYST", "COMPLIANCE_OFFICER"},
        ),
        (
            "admin.batch_scoring.read",
            {"SUPER_ADMIN", "RISK_ANALYST", "COMPLIANCE_OFFICER"},
        ),
    ],
)
def test_cross_org_read_permissions_match_personas(perm_code, expected_holders):
    """Verify cross-org read perms only land on the personas that need them."""
    actual = {role_name for role_name, codes in SYSTEM_PLATFORM_ROLES.items() if perm_code in codes}
    assert actual == expected_holders, f"{perm_code!r} → expected {expected_holders}, got {actual}"
