"""
PostgreSQL client — queries tenant info and applies onboarding updates.

Database: quilr_auth

Operations:
  1. SELECT tenant id + subscriberid WHERE name = <email_domain>
  2. UPDATE tenant SET license_config
  3. UPDATE subscriber SET is_onboarded / is_analysisComplete / areControlsEnabled
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import psycopg2
import psycopg2.extras

from env_config import EnvDbConfig

logger = logging.getLogger(__name__)


@dataclass
class TenantRecord:
    id: str
    subscriberid: str
    name: str


class PostgresClient:
    def __init__(self, cfg: EnvDbConfig) -> None:
        self._conn = psycopg2.connect(
            host=cfg.pg_host,
            port=cfg.pg_port,
            dbname=cfg.pg_dbname,
            user=cfg.pg_user,
            password=cfg.pg_password,
            sslmode=cfg.pg_sslmode,
        )
        self._conn.autocommit = False
        logger.info(
            "Connected to PostgreSQL %s:%s/%s [%s]",
            cfg.pg_host, cfg.pg_port, cfg.pg_dbname, cfg.env_name,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def user_exists(self, email: str) -> bool:
        """Return True if the email already exists in the user table."""
        sql = 'SELECT 1 FROM public."user" WHERE "email" = %s LIMIT 1'
        with self._conn.cursor() as cur:
            cur.execute(sql, (email,))
            return cur.fetchone() is not None

    def get_tenant(self, email_domain: str) -> TenantRecord:
        """Fetch id and subscriberid from tenant table for the given domain."""
        sql = """
            SELECT "id", "subscriberId", "name"
            FROM public.tenant
            WHERE "name" = %s
        """
        with self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (email_domain,))
            row = cur.fetchone()
            if row is None:
                raise LookupError(
                    f"No tenant found in quilr_auth.public.tenant with name = '{email_domain}'"
                )
            return TenantRecord(
                id=str(row["id"]),
                subscriberid=str(row["subscriberId"]),
                name=str(row["name"]),
            )

    def apply_onboarding_updates(self, email_domain: str) -> None:
        """Run both UPDATE statements inside a single transaction."""
        update_tenant_sql = """
            UPDATE public.tenant
            SET "license_config" = %s::jsonb
            WHERE "name" = %s
        """
        update_subscriber_sql = """
            UPDATE public.subscriber
            SET
                "is_onboarded"        = TRUE,
                "is_analysisComplete" = TRUE,
                "areControlsEnabled"  = TRUE
            WHERE "name" = %s
        """
        license_config = '{"ai_axis_enabled": true}'

        try:
            with self._conn.cursor() as cur:
                cur.execute(update_tenant_sql, (license_config, email_domain))
                tenant_rows = cur.rowcount
                logger.info(
                    "tenant UPDATE affected %d row(s) for domain '%s'",
                    tenant_rows,
                    email_domain,
                )

                cur.execute(update_subscriber_sql, (email_domain,))
                subscriber_rows = cur.rowcount
                logger.info(
                    "subscriber UPDATE affected %d row(s) for domain '%s'",
                    subscriber_rows,
                    email_domain,
                )

            self._conn.commit()
            logger.info("PostgreSQL transaction committed for domain '%s'", email_domain)
        except Exception:
            self._conn.rollback()
            logger.exception("PostgreSQL transaction rolled back for domain '%s'", email_domain)
            raise

    def close(self) -> None:
        self._conn.close()
        logger.debug("PostgreSQL connection closed")
