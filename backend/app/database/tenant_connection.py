"""Validating and securing connections to showroom-owned PostgreSQL databases."""
import ipaddress
import re
import socket
import ssl
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import asyncpg
from fastapi import status
from sqlalchemy.engine import URL
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError

from app.core.config import settings
from app.core.exceptions import AppException

HOSTNAME_RE = re.compile(r"^(?=.{1,253}$)[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?)*$")
# Carrier-grade NAT is not flagged as private by every Python version.
_BLOCKED_NETWORKS = (ipaddress.ip_network("100.64.0.0/10"),)

_MESSAGES = {
    "TENANT_DB_HOST_NOT_ALLOWED": "This database host is not allowed. Use the public address of a managed PostgreSQL service.",
    "TENANT_DB_HOST_UNRESOLVED": "This database host could not be found. Check the host name.",
    "TENANT_DB_UNREACHABLE": "Could not reach the database. Check the host and port, and that it accepts connections from VoiceQuote.",
    "TENANT_DB_TIMEOUT": "The database did not respond in time.",
    "TENANT_DB_TLS_FAILED": "A verified TLS connection could not be established. The server must support TLS and present a valid certificate for this host.",
    "TENANT_DB_AUTH_FAILED": "The database rejected these credentials.",
    "TENANT_DB_NOT_FOUND": "This database does not exist on the server.",
    "TENANT_DB_VERSION_UNSUPPORTED": "PostgreSQL 13 or newer is required.",
    "TENANT_DB_ROLE_TOO_PRIVILEGED": "Use a restricted database user. Superusers are not accepted.",
    "TENANT_DB_PERMISSIONS": "This database user cannot create tables in the public schema.",
    "TENANT_DB_SCHEMA_CONFLICT": "This database already has tables that were not created by VoiceQuote. Use an empty database.",
    "TENANT_DB_SCHEMA_NEWER": "This database was migrated by a newer VoiceQuote version.",
    "TENANT_DB_MIGRATION_FAILED": "Setting up the database failed and was rolled back. It is safe to retry.",
    "TENANT_DB_VERIFICATION_FAILED": "The database could not be verified after setup.",
    "TENANT_DB_UNAVAILABLE": "The showroom database is unavailable right now. Try again shortly.",
    "TENANT_DB_BUSY": "Too many requests are using showroom databases right now. Try again shortly.",
}
_STATUS = {
    "TENANT_DB_UNAVAILABLE": status.HTTP_503_SERVICE_UNAVAILABLE,
    "TENANT_DB_BUSY": status.HTTP_503_SERVICE_UNAVAILABLE,
}


class TenantDatabaseError(AppException):
    """Carries only a stable code and fixed message, never driver text that could echo credentials."""

    def __init__(self, code: str):
        super().__init__(code, _MESSAGES[code], _STATUS.get(code, 422))


@dataclass(frozen=True)
class TenantDbCredentials:
    host: str
    port: int
    database: str
    username: str
    password: str = field(repr=False)
    ca_certificate: Optional[str] = field(default=None, repr=False)

    def to_secret(self) -> Dict[str, Any]:
        return {
            "host": self.host,
            "port": self.port,
            "database": self.database,
            "username": self.username,
            "password": self.password,
            "ca_certificate": self.ca_certificate,
        }

    @classmethod
    def from_secret(cls, value: Dict[str, Any]) -> "TenantDbCredentials":
        return cls(**value)


def _is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast and not any(ip in net for net in _BLOCKED_NETWORKS)


def resolve_allowed_address(host: str) -> str:
    """Resolve a host and return an address that passes the SSRF policy.

    Every address is checked, not just the first, so one public record cannot smuggle in an
    internal one. Callers connect to the returned address, closing the DNS-rebinding window
    between this check and the connection.
    """
    if not HOSTNAME_RE.match(host):
        raise TenantDatabaseError("TENANT_DB_HOST_NOT_ALLOWED")
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise TenantDatabaseError("TENANT_DB_HOST_UNRESOLVED")
    addresses = list(dict.fromkeys(info[4][0] for info in infos))
    if not addresses:
        raise TenantDatabaseError("TENANT_DB_HOST_UNRESOLVED")
    if not settings.TENANT_DB_ALLOW_PRIVATE_HOSTS and not all(_is_public(a) for a in addresses):
        raise TenantDatabaseError("TENANT_DB_HOST_NOT_ALLOWED")
    return addresses[0]


class _PinnedHostnameContext(ssl.SSLContext):
    """Verifies the certificate against the showroom's host name while connecting to a pinned IP."""

    pinned_hostname: str

    def wrap_bio(self, incoming, outgoing, server_side=False, server_hostname=None, session=None):
        return super().wrap_bio(incoming, outgoing, server_side, self.pinned_hostname, session)


def build_ssl_context(hostname: str, ca_certificate: Optional[str]) -> ssl.SSLContext:
    """TLS 1.2+, certificate chain and host name always verified; there is no unverified mode."""
    context = _PinnedHostnameContext(ssl.PROTOCOL_TLS_CLIENT)
    context.pinned_hostname = hostname
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    if ca_certificate:
        try:
            context.load_verify_locations(cadata=ca_certificate)
        except (ssl.SSLError, ValueError):
            raise TenantDatabaseError("TENANT_DB_TLS_FAILED")
    else:
        context.load_default_certs()
    return context


def engine_arguments(creds: TenantDbCredentials) -> tuple[URL, Dict[str, Any]]:
    """SQLAlchemy URL and connect args. The URL holds the pinned IP, and asyncpg enforces TLS."""
    address = resolve_allowed_address(creds.host)
    url = URL.create(
        "postgresql+asyncpg",
        username=creds.username,
        password=creds.password,
        host=address,
        port=creds.port,
        database=creds.database,
    )
    connect_args = {
        "ssl": build_ssl_context(creds.host, creds.ca_certificate),
        "timeout": settings.TENANT_DB_CONNECT_TIMEOUT,
        "command_timeout": settings.TENANT_DB_COMMAND_TIMEOUT,
        # Compatible with transaction-mode poolers such as PgBouncer and Supavisor.
        "statement_cache_size": 0,
        "server_settings": {"application_name": "voicequote"},
    }
    return url, connect_args


def classify_connection_error(error: BaseException) -> TenantDatabaseError:
    """Map a driver failure to a safe, stable error. The original text is deliberately discarded."""
    original = error
    if isinstance(error, (DBAPIError, OperationalError, InterfaceError)) and error.orig is not None:
        original = error.orig
        original = getattr(original, "__cause__", None) or original
    if isinstance(original, TenantDatabaseError):
        return original
    if isinstance(original, (asyncpg.InvalidPasswordError, asyncpg.InvalidAuthorizationSpecificationError)):
        return TenantDatabaseError("TENANT_DB_AUTH_FAILED")
    if isinstance(original, asyncpg.InvalidCatalogNameError):
        return TenantDatabaseError("TENANT_DB_NOT_FOUND")
    if isinstance(original, ssl.SSLError):
        return TenantDatabaseError("TENANT_DB_TLS_FAILED")
    if isinstance(original, ConnectionError) and "ssl" in str(original).lower():
        # asyncpg reports a server that refuses the TLS upgrade this way.
        return TenantDatabaseError("TENANT_DB_TLS_FAILED")
    if isinstance(original, TimeoutError):
        return TenantDatabaseError("TENANT_DB_TIMEOUT")
    return TenantDatabaseError("TENANT_DB_UNREACHABLE")
