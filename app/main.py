"""FastAPI application factory and startup/shutdown lifecycle."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.core.exceptions import register_exception_handlers
from app.core.middleware import RequestIdMiddleware

settings = get_settings()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup/shutdown lifecycle."""
    # Startup
    logger.info(f"Starting {settings.app_name} ({settings.app_env})")

    # Seed permissions and system roles (idempotent)
    from app.db import async_session_factory
    from app.services.permission_seeder import seed_permissions_and_roles

    async with async_session_factory() as db:
        await seed_permissions_and_roles(db)

    # Initialize Redis connection
    from app.storage.cache import get_redis_cache

    redis = get_redis_cache()
    try:
        if await redis.ping():
            logger.info("Redis connected successfully")
        else:
            logger.warning("Redis ping failed – caching/rate-limiting degraded")
    except Exception as e:
        logger.warning(f"Redis connection error: {e}")

    # Start subscribers
    from app.core.events import get_event_publisher
    from app.subscribers.batch_complete import handle_batch_score_complete
    from app.subscribers.scoring_complete import handle_scoring_complete
    from app.subscribers.training import handle_model_training_completed
    from app.subscribers.training_started import handle_model_training_started

    event_publisher = get_event_publisher()
    event_publisher.subscribe(
        topic_name=settings.azure_servicebus_topic_training_started,
        subscription_name=settings.azure_servicebus_sub_cs_backend,
        handler=handle_model_training_started,
    )
    event_publisher.subscribe(
        topic_name=settings.azure_servicebus_topic_training_completed,
        subscription_name=settings.azure_servicebus_sub_cs_backend,
        handler=handle_model_training_completed,
    )
    event_publisher.subscribe(
        topic_name=settings.azure_servicebus_topic_scoring_complete,
        subscription_name=settings.azure_servicebus_sub_backend,
        handler=handle_scoring_complete,
    )
    event_publisher.subscribe(
        topic_name=settings.azure_servicebus_topic_batch_score_complete,
        subscription_name=settings.azure_servicebus_sub_backend,
        handler=handle_batch_score_complete,
    )

    yield

    # Shutdown
    logger.info("Shutting down...")
    from app.core.events import get_event_publisher

    event_publisher = get_event_publisher()
    await event_publisher.close()

    from app.db.cosmos import get_cosmos_client

    cosmos = get_cosmos_client()
    await cosmos.close()

    redis_cache = get_redis_cache()
    await redis_cache.close()


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title=settings.app_name,
        description="B2B SaaS Credit Scoring Platform API for Ghanaian Financial Institutions",
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # ── Middleware ────────────────────────────────────────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RequestIdMiddleware)

    # ── Exception Handlers ───────────────────────────────────────────────
    register_exception_handlers(app)

    # ── Routers ──────────────────────────────────────────────────────────
    from app.routers.health import router as health_router
    from app.routers.auth import router as auth_router
    from app.routers.admin_organizations import router as admin_orgs_router
    from app.routers.admin_training_data import router as admin_training_data_router
    from app.routers.admin_sources import router as admin_sources_router
    from app.routers.score_requests import router as score_requests_router
    from app.routers.org_portal import (
        api_keys_router,
        api_logs_router,
        profile_router,
        users_router,
        webhooks_router,
    )

    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(admin_orgs_router)
    app.include_router(admin_training_data_router)
    app.include_router(admin_sources_router)
    app.include_router(score_requests_router)

    from app.routers.bureau_lookup import router as bureau_lookup_router
    app.include_router(bureau_lookup_router)

    # ── New: Models, Rules, Monitoring ───────────────────────────────────
    from app.routers.models import router as models_router
    from app.routers.rules import router as rules_router
    from app.routers.monitoring import monitoring_router
    from app.routers.batch_scoring import router as batch_scoring_router
    from app.routers.permissions import router as permissions_router
    from app.routers.admin_api_logs import router as admin_api_logs_router

    app.include_router(models_router)
    app.include_router(rules_router)
    app.include_router(monitoring_router)
    app.include_router(batch_scoring_router)
    app.include_router(permissions_router)
    app.include_router(admin_api_logs_router)

    app.include_router(profile_router)
    app.include_router(users_router)
    app.include_router(api_keys_router)
    app.include_router(webhooks_router)
    app.include_router(api_logs_router)

    return app


# Module-level app instance for `uvicorn app.main:app`
app = create_app()
