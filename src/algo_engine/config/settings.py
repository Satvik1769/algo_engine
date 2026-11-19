"""Database settings, read from environment variables only. Secrets are never stored in code."""
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field

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
        env = os.environ if env is None else env
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
