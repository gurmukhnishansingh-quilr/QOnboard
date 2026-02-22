"""
Environment registry — maps a Jira environment name to a (PostgresClient, Neo4jClient) pair.

Clients are created lazily on first use and cached for the lifetime of the agent run.
DB credentials are read from the SQLite config store (.qonboard.db).
"""

from __future__ import annotations

import logging
from typing import NamedTuple

from ..config_store import ENV_FILE_MAP
from ..env_config import EnvDbConfig
from .postgres_client import PostgresClient
from .neo4j_client import Neo4jClient

logger = logging.getLogger(__name__)


class EnvClients(NamedTuple):
    pg: PostgresClient
    neo4j: Neo4jClient


class EnvRegistry:
    """Lazily creates and caches DB clients per environment."""

    def __init__(self) -> None:
        self._cache: dict[str, EnvClients] = {}

    def get(self, env_name: str) -> EnvClients:
        """Return the (PostgresClient, Neo4jClient) for the given environment.

        Raises ValueError for unknown environments.
        Raises EnvironmentError if DB credentials are missing from the config store.
        """
        key = env_name.strip()

        if key not in ENV_FILE_MAP:
            raise ValueError(
                f"Unknown environment '{key}'. "
                f"Valid values: {list(ENV_FILE_MAP.keys())}"
            )

        if key not in self._cache:
            logger.info("Initialising DB clients for environment '%s'", key)
            cfg = EnvDbConfig.from_db(key)
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
