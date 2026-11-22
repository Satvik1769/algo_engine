"""Database settings, read from environment variables (optionally seeded from an untracked .env). Secrets are never stored in code."""
from __future__ import annotations

import getpass
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field

from dotenv import load_dotenv
from sqlalchemy.engine import URL


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


@dataclass(frozen=True)
class DbSettings:
    host: str
    database: str
    user: str
    password: str = field(repr=False)  # excluded from repr so it cannot leak into logs
    port: int = 5432
    statement_timeout_ms: int = 120_000
    connect_timeout_s: int = 10

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> DbSettings:
        if env is None:
            # Real environment variables win over .env (override=False); .env is optional.
            load_dotenv(override=False)
            env = os.environ
        missing = [k for k in ("PGHOST", "PGDATABASE", "PGUSER", "PGPASSWORD") if not env.get(k)]
        if missing:
            raise ConfigError(f"Missing required environment variables: {', '.join(missing)}")
        try:
            port = int(env.get("PGPORT", "5432"))
            timeout = int(env.get("PG_STATEMENT_TIMEOUT_MS", "120000"))
        except ValueError as exc:
            raise ConfigError(f"PGPORT and PG_STATEMENT_TIMEOUT_MS must be integers: {exc}") from exc
        return cls(
            host=env["PGHOST"],
            database=env["PGDATABASE"],
            user=env["PGUSER"],
            password=env["PGPASSWORD"],
            port=port,
            statement_timeout_ms=timeout,
        )

    def url(self) -> URL:
        return URL.create(
            "postgresql+psycopg",
            username=self.user,
            password=self.password,
            host=self.host,
            port=self.port,
            database=self.database,
        )

    def connect_args(self) -> dict[str, object]:
        """Session options that make the connection read-only with a bounded query time."""
        return {
            "connect_timeout": self.connect_timeout_s,
            "options": (
                "-c default_transaction_read_only=on "
                f"-c statement_timeout={self.statement_timeout_ms}"
            ),
        }


_IDENT = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


@dataclass(frozen=True)
class StoreSettings:
    """Where the normalized bars table lives (local PostgreSQL by default, read-write).

    Separate from `DbSettings`, which is the read-only market-data source. Variables: ALGO_PGHOST
    (default `/tmp`, the local unix socket), ALGO_PGPORT, ALGO_PGDATABASE (default `postgres`),
    ALGO_PGUSER (default: OS user), ALGO_PGPASSWORD (optional), ALGO_SCHEMA (default `algo`).
    """
    host: str = "/tmp"
    port: int = 5432
    database: str = "postgres"
    user: str = field(default_factory=getpass.getuser)
    password: str | None = field(default=None, repr=False)
    schema: str = "algo"

    def __post_init__(self) -> None:
        if not _IDENT.match(self.schema):
            raise ConfigError(f"ALGO_SCHEMA must match {_IDENT.pattern}: {self.schema!r}")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> StoreSettings:
        if env is None:
            load_dotenv(override=False)
            env = os.environ
        try:
            port = int(env.get("ALGO_PGPORT", "5432"))
        except ValueError as exc:
            raise ConfigError(f"ALGO_PGPORT must be an integer: {exc}") from exc
        return cls(
            host=env.get("ALGO_PGHOST", "/tmp"),
            port=port,
            database=env.get("ALGO_PGDATABASE", "postgres"),
            user=env.get("ALGO_PGUSER") or getpass.getuser(),
            password=env.get("ALGO_PGPASSWORD") or None,
            schema=env.get("ALGO_SCHEMA", "algo"),
        )

    def url(self) -> URL:
        return URL.create("postgresql+psycopg", username=self.user, password=self.password,
                          host=self.host, port=self.port, database=self.database)
