import pytest

from algo_engine.config.settings import ConfigError, DbSettings

ENV = {"PGHOST": "h", "PGDATABASE": "d", "PGUSER": "u", "PGPASSWORD": "s3cret"}


def test_from_env_reads_values_and_defaults():
    s = DbSettings.from_env(ENV)
    assert (s.host, s.database, s.user, s.port) == ("h", "d", "u", 5432)


def test_missing_variables_are_reported_without_secrets():
    with pytest.raises(ConfigError, match="PGPASSWORD"):
        DbSettings.from_env({"PGHOST": "h", "PGDATABASE": "d", "PGUSER": "u"})


def test_invalid_port_raises():
    with pytest.raises(ConfigError):
        DbSettings.from_env({**ENV, "PGPORT": "abc"})


def test_password_not_in_repr():
    assert "s3cret" not in repr(DbSettings.from_env(ENV))


def test_connection_is_read_only_with_timeout():
    opts = DbSettings.from_env({**ENV, "PG_STATEMENT_TIMEOUT_MS": "5000"}).connect_args()["options"]
    assert "default_transaction_read_only=on" in opts
    assert "statement_timeout=5000" in opts


def test_url_uses_psycopg_driver():
    assert DbSettings.from_env(ENV).url().drivername == "postgresql+psycopg"
