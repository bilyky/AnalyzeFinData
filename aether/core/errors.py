"""Exception types the services raise. Callers catch these, not bare Exception."""


class AetherError(Exception):
    """Base class for every error a service raises on purpose."""


class DomainError(AetherError):
    """A business rule refused an action (for example, lowering a stop)."""


class StorageError(AetherError):
    """Stored state could not be read or written safely (for example, a backup failed)."""
