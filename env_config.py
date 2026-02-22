"""
Per-environment database configuration.

Each Quilr environment has its own PostgreSQL and Neo4j instance.
Settings are loaded from an isolated .env file so they never pollute os.environ.

Expected keys in each env file:
    PG_HOST, PG_PORT, PG_USER, PG_PASSWORD
    NEO4J_HOST, NEO4J_PORT, NEO4J_USER, NEO4J_PASSWORD
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import dotenv_values


def _need(values: dict, key: str, file_path: str) -> str:
    val = values.get(key, "").strip()
    if not val:
        raise EnvironmentError(
            f"Required key '{key}' is missing or empty in '{file_path}'"
        )
    return val


def _opt(values: dict, key: str, default: str = "") -> str:
    return values.get(key, default).strip()


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
    def from_file(cls, env_name: str, file_path: str) -> "EnvDbConfig":
        """Load config from an isolated dotenv file (does not touch os.environ)."""
        if not os.path.isfile(file_path):
            raise FileNotFoundError(
                f"Environment config file not found: '{file_path}'"
            )
        v = dotenv_values(file_path)

        neo4j_host = _need(v, "NEO4J_HOST", file_path)
        neo4j_port = _opt(v, "NEO4J_PORT", "7687")
        neo4j_uri = f"bolt://{neo4j_host}:{neo4j_port}"

        return cls(
            env_name=env_name,
            # PostgreSQL
            pg_host=_need(v, "PG_HOST", file_path),
            pg_port=int(_opt(v, "PG_PORT", "5432")),
            pg_dbname=_opt(v, "PG_DBNAME", "quilr_auth"),
            pg_user=_need(v, "PG_USER", file_path),
            pg_password=_need(v, "PG_PASSWORD", file_path),
            pg_sslmode=_opt(v, "PG_SSLMODE", "require"),
            # Neo4j
            neo4j_uri=neo4j_uri,
            neo4j_username=_need(v, "NEO4J_USER", file_path),
            neo4j_password=_need(v, "NEO4J_PASSWORD", file_path),
            neo4j_database=_opt(v, "NEO4J_DATABASE", "neo4j"),
        )
