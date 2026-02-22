"""
Per-environment database configuration.

Each Quilr environment has its own PostgreSQL and Neo4j instance.
Settings are read from the SQLite config store (.qonboard.db).

On first run the store auto-ingests from .env_* files in cwd.
Use `qonboard config set KEY VALUE --env ENV_NAME` to update values.

Expected keys per environment:
    PG_HOST, PG_PORT, PG_USER, PG_PASSWORD, PG_DBNAME, PG_SSLMODE
    NEO4J_HOST, NEO4J_PORT, NEO4J_USER, NEO4J_PASSWORD, NEO4J_DATABASE
"""

from __future__ import annotations

from dataclasses import dataclass


def _need(env_name: str, key: str, value: str | None) -> str:
    if not value or not value.strip():
        raise EnvironmentError(
            f"Required config key '{key}' is missing for environment '{env_name}'. "
            f"Run 'qonboard config set {key} VALUE --env \"{env_name}\"' to set it."
        )
    return value.strip()


def _opt(value: str | None, default: str = "") -> str:
    return value.strip() if value else default


@dataclass(frozen=True)
class EnvDbConfig:
    """PostgreSQL + Neo4j settings for a single Quilr environment."""

    env_name: str

    # PostgreSQL
    pg_host: str
    pg_port: int
    pg_dbname: str
    pg_user: str
    pg_password: str
    pg_sslmode: str

    # Neo4j (bolt URI assembled from host + port)
    neo4j_uri: str
    neo4j_username: str
    neo4j_password: str
    neo4j_database: str

    @classmethod
    def from_db(cls, env_name: str) -> "EnvDbConfig":
        """Load config from the SQLite config store for the given environment."""
        from .config_store import ConfigStore

        store = ConfigStore.instance()
        g = lambda k, default=None: store.get_env(env_name, k, default)  # noqa: E731

        neo4j_host = _need(env_name, "NEO4J_HOST", g("NEO4J_HOST"))
        neo4j_port = _opt(g("NEO4J_PORT"), "7687")
        neo4j_uri  = f"bolt://{neo4j_host}:{neo4j_port}"

        return cls(
            env_name=env_name,
            # PostgreSQL
            pg_host    = _need(env_name, "PG_HOST",     g("PG_HOST")),
            pg_port    = int(_opt(g("PG_PORT"), "5432")),
            pg_dbname  = _opt(g("PG_DBNAME"),  "quilr_auth"),
            pg_user    = _need(env_name, "PG_USER",     g("PG_USER")),
            pg_password= _need(env_name, "PG_PASSWORD", g("PG_PASSWORD")),
            pg_sslmode = _opt(g("PG_SSLMODE"), "require"),
            # Neo4j
            neo4j_uri      = neo4j_uri,
            neo4j_username = _need(env_name, "NEO4J_USER",     g("NEO4J_USER")),
            neo4j_password = _need(env_name, "NEO4J_PASSWORD", g("NEO4J_PASSWORD")),
            neo4j_database = _opt(g("NEO4J_DATABASE"), "neo4j"),
        )
