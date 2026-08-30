"""Provider-specific, secret-safe error types."""

from __future__ import annotations

from activegraph.errors import StorageError


class ConfigurationError(ValueError):
    """The provider configuration is invalid or unsupported."""


class SurrealProviderError(StorageError):
    """Base class for failures raised by this provider."""


class LedgerIntegrityError(SurrealProviderError):
    """The persisted event sequence or hash chain is inconsistent."""


class ConcurrentWriterError(SurrealProviderError):
    """The run head changed after this store handle observed it."""


class ForkPointNotFoundError(SurrealProviderError, KeyError):
    """A requested inclusive fork boundary is absent from the parent run."""


class RunAlreadyExistsError(SurrealProviderError):
    """A fork destination already contains a run."""


class UnsupportedCompactedRunError(SurrealProviderError):
    """The preview was asked to replay or fork a compacted event log."""


class ClosedStoreError(SurrealProviderError):
    """An operation was attempted after the store was closed."""
