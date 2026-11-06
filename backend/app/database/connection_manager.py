"""One small engine per showroom, shared across requests and capped across the process.

Engines are cached rather than created per request. Sessions are not cached: every request gets
its own, from its own tenant's engine, so a session can never be reused for another tenant.
The cap on engines and on concurrent sessions stops many active showrooms from exhausting
connections on this process or on any showroom's database.
"""
import asyncio
import time
import uuid
from collections import OrderedDict
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator, Coroutine, Dict

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.logging import logger
from app.database.secrets import SecretNotFound, get_secret_store
from app.database.tenant_connection import (
    TenantDatabaseError,
    TenantDbCredentials,
    classify_connection_error,
    engine_arguments,
)


@dataclass
class _Entry:
    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]
    secret_reference: str
    created_at: float
    last_used: float = 0.0


_background_cleanups: set[asyncio.Task] = set()


def _in_background(cleanup: Coroutine) -> None:
    """Closing connections to a hung server can itself hang, so no request waits for it."""
    task = asyncio.create_task(cleanup)
    _background_cleanups.add(task)
    task.add_done_callback(_background_cleanups.discard)


def dispose_in_background(engine: AsyncEngine) -> None:
    _in_background(engine.dispose())


def build_engine(creds: TenantDbCredentials, pool_size: int | None = None) -> AsyncEngine:
    url, connect_args = engine_arguments(creds)
    return create_async_engine(
        url,
        connect_args=connect_args,
        pool_size=pool_size or settings.TENANT_DB_POOL_SIZE,
        max_overflow=settings.TENANT_DB_MAX_OVERFLOW,
        pool_timeout=settings.TENANT_DB_POOL_TIMEOUT,
        pool_recycle=settings.TENANT_DB_POOL_RECYCLE,
        pool_pre_ping=True,
        hide_parameters=True,
    )


class TenantConnectionManager:
    def __init__(self) -> None:
        self._entries: "OrderedDict[uuid.UUID, _Entry]" = OrderedDict()
        self._locks: Dict[uuid.UUID, asyncio.Lock] = {}
        self._semaphore: asyncio.Semaphore | None = None

    @property
    def engine_count(self) -> int:
        return len(self._entries)

    def engine_for(self, tenant_id: uuid.UUID) -> AsyncEngine | None:
        entry = self._entries.get(tenant_id)
        return entry.engine if entry else None

    def _fresh(self, entry: _Entry | None, secret_reference: str) -> bool:
        # Expiry re-resolves DNS through the SSRF policy, and follows a managed database's failover.
        return (
            entry is not None
            and entry.secret_reference == secret_reference
            and time.monotonic() - entry.created_at < settings.TENANT_DB_ENGINE_TTL
        )

    async def _entry(self, tenant_id: uuid.UUID, secret_reference: str) -> _Entry:
        entry = self._entries.get(tenant_id)
        if self._fresh(entry, secret_reference):
            self._entries.move_to_end(tenant_id)
            return entry
        # One lock per tenant: concurrent first requests build one engine, and one slow
        # showroom cannot hold up another's.
        lock = self._locks.setdefault(tenant_id, asyncio.Lock())
        async with lock:
            entry = self._entries.get(tenant_id)
            if self._fresh(entry, secret_reference):
                self._entries.move_to_end(tenant_id)
                return entry
            try:
                secret = await asyncio.to_thread(get_secret_store().get, secret_reference)
            except SecretNotFound:
                raise TenantDatabaseError("TENANT_DB_UNAVAILABLE")
            creds = TenantDbCredentials.from_secret(secret)
            # to_thread: DNS resolution blocks.
            engine = await asyncio.to_thread(build_engine, creds)
            new_entry = _Entry(
                engine=engine,
                sessions=async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False, autoflush=False),
                secret_reference=secret_reference,
                created_at=time.monotonic(),
            )
            await self._retire(tenant_id)
            self._entries[tenant_id] = new_entry
            await self._evict_over_limit()
            return new_entry

    async def _entry_for_request(self, tenant_id: uuid.UUID, secret_reference: str) -> _Entry:
        """For a stored connection any failure here is an outage, not a mistake the staff member can fix."""
        try:
            # Bounded: DNS resolution can stall for far longer than a request should wait.
            return await asyncio.wait_for(
                self._entry(tenant_id, secret_reference), timeout=settings.TENANT_DB_CONNECT_TIMEOUT
            )
        except TimeoutError:
            code = "TENANT_DB_TIMEOUT"
        except TenantDatabaseError as err:
            code = err.code
        logger.warning(f"Tenant {tenant_id} database connection failed: {code}")
        raise TenantDatabaseError("TENANT_DB_UNAVAILABLE") from None

    def _retire_without_waiting(self, tenant_id: uuid.UUID) -> None:
        old = self._entries.pop(tenant_id, None)
        if old is not None:
            dispose_in_background(old.engine)

    async def _retire(self, tenant_id: uuid.UUID) -> None:
        old = self._entries.pop(tenant_id, None)
        if old is not None:
            # Closes idle connections now; connections still in use close when returned.
            await old.engine.dispose()

    async def _evict_over_limit(self) -> None:
        while len(self._entries) > settings.TENANT_DB_MAX_ENGINES:
            tenant_id = next(iter(self._entries))  # least recently used
            await self._retire(tenant_id)
            logger.info(f"Evicted idle tenant database engine for {tenant_id}")

    def _limit(self) -> asyncio.Semaphore:
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(settings.TENANT_DB_MAX_CONCURRENT_SESSIONS)
        return self._semaphore

    @asynccontextmanager
    async def session(self, tenant_id: uuid.UUID, secret_reference: str) -> AsyncIterator[AsyncSession]:
        limit = self._limit()
        try:
            await asyncio.wait_for(limit.acquire(), timeout=settings.TENANT_DB_POOL_TIMEOUT)
        except TimeoutError:
            raise TenantDatabaseError("TENANT_DB_BUSY")
        try:
            entry = await self._entry_for_request(tenant_id, secret_reference)
            entry.last_used = time.monotonic()
            session = entry.sessions()
            try:
                # Connect now, so an unavailable database gives one clear, safe error rather than
                # a driver exception from the middle of a business operation. The pre-ping on a
                # pooled connection runs here too, so a hung server cannot hold a request for
                # the longer command timeout.
                await asyncio.wait_for(session.connection(), timeout=settings.TENANT_DB_CONNECT_TIMEOUT)
            except Exception as exc:
                code = classify_connection_error(exc).code
                logger.warning(f"Tenant {tenant_id} database connection failed: {code}")
                # Rebuild next time, in case the address or credentials changed.
                self._retire_without_waiting(tenant_id)
                _in_background(session.close())
                raise TenantDatabaseError("TENANT_DB_UNAVAILABLE") from None
            async with session:
                yield session
        finally:
            limit.release()

    async def dispose_idle(self) -> int:
        """Release connections of showrooms that have gone quiet, so they stay free on small plans."""
        cutoff = time.monotonic() - settings.TENANT_DB_IDLE_SECONDS
        idle = [t for t, e in self._entries.items() if e.last_used <= cutoff and e.engine.pool.checkedout() == 0]
        for tenant_id in idle:
            await self._retire(tenant_id)
        return len(idle)

    async def disconnect(self, tenant_id: uuid.UUID) -> None:
        """Drop this tenant's connections after revocation or rotation. Others are untouched."""
        lock = self._locks.setdefault(tenant_id, asyncio.Lock())
        async with lock:
            await self._retire(tenant_id)

    async def dispose_all(self) -> None:
        for tenant_id in list(self._entries):
            await self._retire(tenant_id)


connection_manager = TenantConnectionManager()
