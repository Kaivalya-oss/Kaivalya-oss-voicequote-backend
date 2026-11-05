"""Storage for showroom database credentials.

The central database holds only the reference returned by put(); the credentials live here.
Production needs a real secrets manager: implement SecretStore for it and register it in
get_secret_store(). LocalEncryptedSecretStore exists for development and tests, and the
application refuses to start with it when APP_ENV=production.
"""
import json
import os
import uuid
from abc import ABC, abstractmethod
from typing import Any, Dict

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


class SecretNotFound(Exception):
    pass


class SecretStore(ABC):
    @abstractmethod
    def put(self, tenant_id: uuid.UUID, value: Dict[str, Any]) -> str:
        """Store a new secret version and return its reference. Never overwrites an existing one."""

    @abstractmethod
    def get(self, reference: str) -> Dict[str, Any]:
        ...

    @abstractmethod
    def delete(self, reference: str) -> None:
        """Remove the secret. Deleting one that is already gone is not an error."""


class LocalEncryptedSecretStore(SecretStore):
    """Encrypted files on local disk. Development and test use only."""

    PREFIX = "local:"

    def __init__(self, directory: str, master_key: str):
        if not master_key:
            raise RuntimeError("SECRETS_MASTER_KEY is required for the local secret store.")
        self._fernet = Fernet(master_key.encode("utf-8"))
        self._directory = directory
        os.makedirs(directory, mode=0o700, exist_ok=True)

    def _path(self, reference: str) -> str:
        if not reference.startswith(self.PREFIX):
            raise SecretNotFound()
        try:
            # Parsing as a UUID rules out path traversal through a crafted reference.
            name = uuid.UUID(reference[len(self.PREFIX):]).hex
        except ValueError:
            raise SecretNotFound()
        return os.path.join(self._directory, f"{name}.enc")

    def put(self, tenant_id: uuid.UUID, value: Dict[str, Any]) -> str:
        reference = f"{self.PREFIX}{uuid.uuid4()}"
        token = self._fernet.encrypt(json.dumps({"tenant_id": str(tenant_id), "value": value}).encode("utf-8"))
        fd = os.open(self._path(reference), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(token)
        return reference

    def get(self, reference: str) -> Dict[str, Any]:
        try:
            with open(self._path(reference), "rb") as fh:
                return json.loads(self._fernet.decrypt(fh.read()))["value"]
        except (FileNotFoundError, InvalidToken):
            raise SecretNotFound()

    def delete(self, reference: str) -> None:
        try:
            os.remove(self._path(reference))
        except FileNotFoundError:
            pass


_store: SecretStore | None = None


def get_secret_store() -> SecretStore:
    global _store
    if _store is None:
        if settings.SECRETS_BACKEND != "local":
            raise RuntimeError(f"Unsupported SECRETS_BACKEND '{settings.SECRETS_BACKEND}'.")
        _store = LocalEncryptedSecretStore(settings.SECRETS_LOCAL_DIR, settings.SECRETS_MASTER_KEY)
    return _store
