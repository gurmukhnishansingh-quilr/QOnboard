"""
Environment registry — maps a Jira environment name to a (PostgresClient, Neo4jClient) pair.

Clients are created lazily on first use and cached for the lifetime of the agent run.
Each environment has its own config file under envs/.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import NamedTuple

from ..env_config import EnvDbConfig
from .postgres_client import PostgresClient
from .neo4j_client import Neo4jClient

logger = logging.getLogger(__name__)

# Resolved at runtime — env files live next to where the user runs the command
def _env_file(filename: str) -> str:
    return str(Path.cwd() / filename)


# Maps Jira environment field value → .env file in the project root
ENV_FILE_MAP: dict[str, str | None] = {
    "UAE POC":  _env_file(".env_uae"),
    "UAE PROD": _env_file(".env_uae"),   # shares infra with UAE POC for now
    "IND POC":  _env_file(".env_ind"),
    "IND PROD": _env_file(".env_ind_prod"),
    "USA POC":  _env_file(".env_us"),
    "USA PROD": _env_file(".env_us_prod"),
}


class EnvClients(NamedTuple):
    pg: PostgresClient
    neo4j: Neo4jClient


class EnvRegistry:
    """Lazily creates and caches DB clients per environment."""

    def __init__(self) -> None:
        self._cache: dict[str, EnvClients] = {}

    def get(self, env_name: str) -> EnvClients:
        """Return the (PostgresClient, Neo4jClient) for the given environment.

        Raises ValueError if the environment is unknown or not yet available.
        Raises FileNotFoundError if the env file is missing.
        """
        key = env_name.strip()

        if key not in ENV_FILE_MAP:
            raise ValueError(
                f"Unknown environment '{key}'. "
                f"Valid values: {list(ENV_FILE_MAP.keys())}"
            )

        file_path = ENV_FILE_MAP[key]
        if file_path is None:
            raise ValueError(
                f"Environment '{key}' is not available yet — skipping."
            )

        if key not in self._cache:
            logger.info("Initialising DB clients for environment '%s'", key)
            cfg = EnvDbConfig.from_file(key, file_path)
            self._cache[key] = EnvClients(
                pg=PostgresClient(cfg),
                neo4j=Neo4jClient(cfg),
            )

        return self._cache[key]

    def close_all(self) -> None:
        """Close all open connections."""
        for env_name, clients in self._cache.items():
            try:
                clients.pg.close()
            except Exception:  # noqa: BLE001
                logger.warning("Error closing PG client for '%s'", env_name)
            try:
                clients.neo4j.close()
            except Exception:  # noqa: BLE001
                logger.warning("Error closing Neo4j client for '%s'", env_name)
        self._cache.clear()
        logger.debug("All DB clients closed")
