"""Synchronous SurrealDB 2.0 WebSocket connection and schema lifecycle."""

from __future__ import annotations

import re
import threading
from collections.abc import Iterator, Mapping
from importlib import resources
from typing import Any
from uuid import UUID

from surrealdb import (
    BlockingSurrealSession,
    BlockingSurrealTransaction,
    BlockingWsSurrealConnection,
    NotFoundError,
    RecordID,
    Surreal,
)

from .config import SurrealConfig
from .errors import ClosedStoreError, ConfigurationError, LedgerIntegrityError
from .keys import record_key

SCHEMA_VERSION = 1
SCHEMA_RECORD = RecordID("ag_schema", "singleton")
_VERSION_RE = re.compile(r"(?:surrealdb[- ]v?)?(?P<version>\d+\.\d+\.\d+)", re.I)


class SurrealConnection:
    """One authenticated blocking WS connection with one independent session.

    The Python SDK's client-side transaction handle is scoped to a WebSocket
    session.  Each provider store therefore owns a connection/session pair;
    stores never share process-global clients or transaction state.
    """

    def __init__(self, config: SurrealConfig) -> None:
        self.config = config
        self._closed = False
        self._schema_ready = False
        self._schema_lock = threading.RLock()

        connection = Surreal(config.endpoint)
        if not isinstance(connection, BlockingWsSurrealConnection):
            connection.close()
            raise ConfigurationError(
                "the provider requires the SurrealDB blocking WebSocket transport"
            )
        self._connection: BlockingWsSurrealConnection = connection
        self._session: BlockingSurrealSession | None = None

        try:
            self._connection.signin({"username": config.username, "password": config.password})
            self._connection.use(config.namespace, config.database)
            session = self._connection.new_session()
            session.signin({"username": config.username, "password": config.password})
            session.use(config.namespace, config.database)
            self._session = session
        except BaseException:
            self._connection.close()
            self._closed = True
            raise

    @property
    def session(self) -> BlockingSurrealSession:
        """Return the live SDK session used for all provider operations."""

        self._ensure_open()
        assert self._session is not None
        return self._session

    @property
    def session_id(self) -> UUID:
        """Expose the real SDK session ID for qualification diagnostics."""

        return self.session._session_id  # noqa: SLF001 - SDK has no public accessor

    def query(self, statement: str, variables: Mapping[str, Any] | None = None) -> Any:
        """Execute one SurrealQL statement with bound values.

        SDK 2.0 only checks the first result in a multi-statement query, so the
        provider intentionally sends schema and mutation statements one at a
        time.  Callers should follow the same rule.
        """

        return self.session.query(statement, dict(variables or {}))

    def raw_query(self, statement: str, variables: Mapping[str, Any] | None = None) -> Any:
        """Return the decoded SurrealQL value without provider conversion."""

        return self.query(statement, variables)

    def begin_transaction(self) -> BlockingSurrealTransaction:
        """Begin a real SDK 2.0 transaction on this connection's session."""

        return self.session.begin_transaction()

    def record_id(self, table: str, *parts: str) -> RecordID:
        """Build a typed record ID with an opaque, scope-safe identifier."""

        return RecordID(table, record_key(table, *(str(part) for part in parts)))

    def server_version(self) -> str:
        """Return a normalized server semantic version when one is reported."""

        raw = str(self._connection.version())
        match = _VERSION_RE.search(raw)
        return match.group("version") if match else raw

    def ensure_schema(self) -> int:
        """Install schema v1 idempotently and reject unknown schema versions."""

        with self._schema_lock:
            self._ensure_open()
            if self._schema_ready:
                return SCHEMA_VERSION

            try:
                existing = _one(
                    self.query(
                        "SELECT * FROM ONLY $schema;",
                        {"schema": SCHEMA_RECORD},
                    )
                )
            except NotFoundError:
                # A SCHEMAFULL database reports an undefined table as a
                # NotFound error; that is the clean-install case, not damage.
                existing = None
            if existing is not None:
                self._assert_schema_version(existing)

            schema_text = (
                resources.files("activegraph_surrealdb")
                .joinpath("schema.surql")
                .read_text(encoding="utf-8")
            )
            for statement in _schema_statements(schema_text):
                self.query(statement)

            current = _one(
                self.query(
                    "SELECT * FROM ONLY $schema;",
                    {"schema": SCHEMA_RECORD},
                )
            )
            if current is None:
                try:
                    self.query(
                        "CREATE $schema CONTENT $content;",
                        {
                            "schema": SCHEMA_RECORD,
                            "content": {
                                "version": SCHEMA_VERSION,
                                "installed_at": _now_expression_result(self),
                            },
                        },
                    )
                except BaseException:
                    # A concurrently constructed store may have won creation.
                    current = _one(
                        self.query(
                            "SELECT * FROM ONLY $schema;",
                            {"schema": SCHEMA_RECORD},
                        )
                    )
                    if current is None:
                        raise
                else:
                    current = _one(
                        self.query(
                            "SELECT * FROM ONLY $schema;",
                            {"schema": SCHEMA_RECORD},
                        )
                    )

            if current is None:
                raise LedgerIntegrityError("the provider schema record is missing")
            self._assert_schema_version(current)
            self._schema_ready = True
            return SCHEMA_VERSION

    def close(self) -> None:
        """Best-effort, idempotent teardown of the session and socket."""

        if self._closed:
            return
        self._closed = True
        session, self._session = self._session, None
        try:
            if session is not None:
                session.close_session()
        finally:
            self._connection.close()

    def _assert_schema_version(self, row: Mapping[str, Any]) -> None:
        try:
            version = int(row["version"])
        except (KeyError, TypeError, ValueError) as exc:
            raise LedgerIntegrityError("the provider schema version is absent or invalid") from exc
        if version != SCHEMA_VERSION:
            raise LedgerIntegrityError(
                f"unsupported provider schema version {version}; expected {SCHEMA_VERSION}"
            )

    def _ensure_open(self) -> None:
        if self._closed:
            raise ClosedStoreError("the SurrealDB connection is closed")


def _schema_statements(source: str) -> Iterator[str]:
    """Yield the simple semicolon-delimited DDL statements in schema.surql."""

    uncommented = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("--")
    )
    for fragment in uncommented.split(";"):
        statement = fragment.strip()
        if statement:
            yield statement + ";"


def _one(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, list) and value and isinstance(value[0], dict):
        return dict(value[0])
    return None


def _now_expression_result(connection: SurrealConnection) -> str:
    value = connection.query("RETURN time::now();")
    if isinstance(value, list):
        value = value[0] if value else None
    return str(value)


__all__ = [
    "SCHEMA_RECORD",
    "SCHEMA_VERSION",
    "SurrealConnection",
]
