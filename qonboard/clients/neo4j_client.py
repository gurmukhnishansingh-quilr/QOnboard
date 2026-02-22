"""
Neo4j client — MERGEs the TENANT node after onboarding.

Parameters fed to the Cypher query:
    TENANT_0_id          → tenant.id        (from PostgreSQL)
    TENANT_0_subscriber  → tenant.subscriberid (from PostgreSQL)
    TENANT_0_tenant      → tenant.id (from PostgreSQL)
    TENANT_0_creationTime→ ISO-8601 datetime string
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from neo4j import GraphDatabase, Driver

from ..env_config import EnvDbConfig
from .postgres_client import TenantRecord

logger = logging.getLogger(__name__)

_MERGE_TENANT = """
MERGE (TENANT_0:TENANT {
    id:         $TENANT_0_id,
    subscriber: $TENANT_0_subscriber,
    tenant:     $TENANT_0_tenant
})
ON CREATE SET
    TENANT_0.creationTime = $TENANT_0_creationTime,
    TENANT_0.subscriber   = $TENANT_0_subscriber,
    TENANT_0.tenant       = $TENANT_0_tenant,
    TENANT_0.internalId   = randomUUID(),
    TENANT_0.new          = true,
    TENANT_0.timestamp    = timestamp()
ON MATCH SET
    TENANT_0.subscriber   = $TENANT_0_subscriber,
    TENANT_0.tenant       = $TENANT_0_tenant,
    TENANT_0.new          = false,
    TENANT_0.timestamp    = timestamp()
RETURN TENANT_0.internalId AS internalId, TENANT_0.new AS isNew
"""


class Neo4jClient:
    def __init__(self, cfg: EnvDbConfig) -> None:
        self._driver: Driver = GraphDatabase.driver(
            cfg.neo4j_uri,
            auth=(cfg.neo4j_username, cfg.neo4j_password),
        )
        self._database = cfg.neo4j_database or None  # None → default db
        logger.info("Neo4j driver initialised for %s [%s]", cfg.neo4j_uri, cfg.env_name)

    def merge_tenant(self, tenant: TenantRecord) -> None:
        """MERGE a TENANT node using data from PostgreSQL."""
        params = {
            "TENANT_0_id": tenant.id,
            "TENANT_0_subscriber": tenant.subscriberid,
            "TENANT_0_tenant": tenant.id,
            "TENANT_0_creationTime": datetime.now(tz=timezone.utc).isoformat(),
        }

        logger.info(
            "Merging Neo4j TENANT node for id=%s, tenant=%s",
            tenant.id,
            tenant.name,
        )
        logger.debug("Neo4j params: %s", params)

        with self._driver.session(database=self._database) as session:
            result = session.run(_MERGE_TENANT, params)
            record = result.single()
            if record:
                action = "CREATED" if record["isNew"] else "MATCHED"
                logger.info(
                    "TENANT node %s (internalId=%s) for tenant '%s'",
                    action,
                    record["internalId"],
                    tenant.name,
                )

    def close(self) -> None:
        self._driver.close()
        logger.debug("Neo4j driver closed")
