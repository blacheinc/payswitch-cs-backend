"""Actor enrichment for API log responses.

Given a list of ApiLog ORM rows, resolves each row's actor (org user, admin user,
or API key) in two batch queries and returns a mapping from the actor's UUID string
to an ActorInfo object.

Usage::

    actors = await resolve_actors(db, logs)
    for log in logs:
        actor = actors.get(str(log.api_key_id or log.user_id))
        response = ApiLogResponse(..., actor=actor)
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.api_log import ActorInfo


async def resolve_actors(
    db: AsyncSession,
    logs: list,
) -> dict[str, ActorInfo]:
    """Batch-load actor info for a page of ApiLog rows.

    Distinguishes admin users from org users by the absence of organization_id:
    - organization_id IS NULL  → user_id refers to AdminUser
    - organization_id IS NOT NULL → user_id refers to OrgUser
    - api_key_id IS NOT NULL   → actor is an ApiKey (may coexist with either above)
    """
    from app.models.api_key import ApiKey
    from app.models.user import AdminUser, OrgUser

    admin_user_ids: set[str] = set()
    org_user_ids: set[str] = set()
    api_key_ids: set[str] = set()

    for log in logs:
        if log.api_key_id:
            api_key_ids.add(str(log.api_key_id))
        elif log.user_id:
            if log.organization_id is None:
                admin_user_ids.add(str(log.user_id))
            else:
                org_user_ids.add(str(log.user_id))

    actors: dict[str, ActorInfo] = {}

    if admin_user_ids:
        rows = (
            await db.execute(select(AdminUser).where(AdminUser.id.in_(admin_user_ids)))
        ).scalars().all()
        for u in rows:
            actors[str(u.id)] = ActorInfo(
                id=str(u.id), type="admin_user", name=u.name, email=u.email
            )

    if org_user_ids:
        rows = (
            await db.execute(select(OrgUser).where(OrgUser.id.in_(org_user_ids)))
        ).scalars().all()
        for u in rows:
            actors[str(u.id)] = ActorInfo(
                id=str(u.id), type="org_user", name=u.name, email=u.email
            )

    if api_key_ids:
        rows = (
            await db.execute(select(ApiKey).where(ApiKey.id.in_(api_key_ids)))
        ).scalars().all()
        for k in rows:
            actors[str(k.id)] = ActorInfo(
                id=str(k.id), type="api_key", name=k.name, email=None
            )

    return actors


def pick_actor(log, actors: dict[str, ActorInfo]) -> ActorInfo | None:
    """Return the ActorInfo for a single log row, preferring API key over user."""
    if log.api_key_id and str(log.api_key_id) in actors:
        return actors[str(log.api_key_id)]
    if log.user_id and str(log.user_id) in actors:
        return actors[str(log.user_id)]
    return None
