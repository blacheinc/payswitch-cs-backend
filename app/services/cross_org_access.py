"""Resolve the effective organisation filter for cross-org-capable endpoints.

The same pattern recurs across the score-request and batch-scoring read
endpoints: org users (and API keys) must always be scoped to their own
``organization_id``; platform admins with the matching ``admin.*.read``
permission may pass an explicit ``organization_id`` query param to
inspect a single org's data, or omit it entirely to read across the
whole platform.

This module centralises that decision and writes an :class:`AuditLog`
row whenever an admin reads data they don't own — so cross-org access
leaves a deliberate trail without each handler having to remember.
"""
from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AuthorizationError, ValidationError
from app.dependencies import AuthContext


@dataclass(frozen=True)
class OrgFilter:
    """Resolution result for a cross-org-aware request.

    ``organization_id`` is the value the handler should use in its WHERE
    clause:

    * A specific UUID → scope the query to that org.
    * ``None`` → cross-org read (only valid when ``cross_org`` is True).

    ``cross_org`` is a convenience flag for handlers that branch on
    "scoping vs. unbounded" — e.g. to attach a ``by_org`` breakdown.
    """

    organization_id: UUID | None
    cross_org: bool


def _coerce_uuid(value: UUID | str | None) -> UUID | None:
    if value is None or isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except ValueError as exc:
        raise ValidationError(f"organization_id is not a valid UUID: {value!r}") from exc


async def resolve_org_filter(
    db: AsyncSession,
    auth: AuthContext,
    *,
    requested_org_id: UUID | str | None,
    admin_permission: str,
    resource_type: str,
) -> OrgFilter:
    """Decide which organisation(s) the caller is allowed to see.

    Behavioural matrix:

    +---------------------+--------------------------+----------------------+
    | Caller              | requested_org_id is …    | Outcome              |
    +=====================+==========================+======================+
    | Org user / API key  | ``None``                 | scope to own org     |
    |                     | matches own org          | scope to own org     |
    |                     | differs from own org     | 403                  |
    +---------------------+--------------------------+----------------------+
    | Admin w/o admin perm| ``None`` or any value    | 403                  |
    +---------------------+--------------------------+----------------------+
    | Admin with perm     | specific UUID            | scope, audit-logged  |
    |                     | ``None``                 | cross-org            |
    +---------------------+--------------------------+----------------------+

    The audit row is only written when an admin actually reads data
    outside their (non-existent) home org, so day-to-day org user
    activity doesn't generate audit noise.
    """
    requested_org_uuid = _coerce_uuid(requested_org_id)
    is_admin = auth.user_type == "admin"
    has_admin_perm = bool(auth.permissions) and (
        "*" in auth.permissions or admin_permission in auth.permissions
    )

    # ── Admin path ───────────────────────────────────────────────────────
    if is_admin:
        if not has_admin_perm:
            # Caller is admin but lacks the cross-org permission — refuse
            # rather than silently returning empty.  Without this, an
            # under-privileged admin would see "0 results" and assume the
            # platform was empty.
            raise AuthorizationError(
                f"Permission required to read this resource: {admin_permission}"
            )

        if requested_org_uuid is not None:
            await _audit_cross_org_read(
                db, auth,
                organization_id=requested_org_uuid,
                resource_type=resource_type,
                description=f"Admin scoped read of {resource_type} for org {requested_org_uuid}",
            )
            return OrgFilter(organization_id=requested_org_uuid, cross_org=False)

        # Cross-org read — also audit-logged because it's privileged.
        await _audit_cross_org_read(
            db, auth,
            organization_id=None,
            resource_type=resource_type,
            description=f"Admin cross-org read of {resource_type}",
        )
        return OrgFilter(organization_id=None, cross_org=True)

    # ── Non-admin path ───────────────────────────────────────────────────
    own_org = auth.organization_id
    if own_org is None:
        # Should not happen — non-admin without an org_id means the auth
        # layer didn't populate the context properly.  Fail loud.
        raise AuthorizationError("Caller has no organization context")

    if requested_org_uuid is None:
        return OrgFilter(organization_id=own_org, cross_org=False)

    if str(requested_org_uuid) != str(own_org):
        raise AuthorizationError(
            "Cross-organisation reads require admin permission"
        )

    return OrgFilter(organization_id=own_org, cross_org=False)


async def _audit_cross_org_read(
    db: AsyncSession,
    auth: AuthContext,
    *,
    organization_id: UUID | None,
    resource_type: str,
    description: str,
) -> None:
    """Persist a single audit row for an admin cross-org read."""
    from app.models.audit import AuditLog

    db.add(AuditLog(
        actor_type="admin",
        actor_id=str(auth.user_id) if auth.user_id else None,
        actor_email=auth.user_email,
        organization_id=str(organization_id) if organization_id else None,
        action=f"{resource_type}.read_cross_org",
        resource_type=resource_type,
        resource_id=str(organization_id) if organization_id else None,
        description=description,
    ))
    # Don't await commit here — the caller's transaction will commit on
    # request success, and on failure we'd rather not record a read that
    # never returned data.
