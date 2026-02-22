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

from ..env_config import EnvDbConfig

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

    def get_user_account_type(self, email: str) -> str | None:
        """Return the accountType for the email if it exists in the user table, else None.

        Known values: 'credentials' (internal user), 'OAuth' (SSO / external user).
        Returns None when the email is not found (new user — safe to onboard).
        """
        sql = 'SELECT "accountType" FROM public."user" WHERE "email" = %s LIMIT 1'
        with self._conn.cursor() as cur:
            cur.execute(sql, (email,))
            row = cur.fetchone()
            return row[0] if row else None

    def get_tenant_role_ids(self, tenant_id: str) -> list[str]:
        """Return all role IDs for the given tenant."""
        sql = 'SELECT "id" FROM public.roles WHERE "tenantId" = %s::uuid AND "deletedAt" IS NULL'
        with self._conn.cursor() as cur:
            cur.execute(sql, (tenant_id,))
            return [str(r[0]) for r in cur.fetchall()]

    def get_tenant_group_ids(self, tenant_id: str) -> list[str]:
        """Return all group IDs for the given tenant."""
        sql = 'SELECT "id" FROM public."group" WHERE "tenantId" = %s::uuid AND "deletedAt" IS NULL'
        with self._conn.cursor() as cur:
            cur.execute(sql, (tenant_id,))
            return [str(r[0]) for r in cur.fetchall()]

    def create_monitoring_user(
        self,
        email: str,
        tenant: TenantRecord,
        password_hash: str,
        role_ids: list[str],
        group_ids: list[str],
    ) -> bool:
        """Insert a credentials-type monitoring user for the tenant.

        Returns True if created, False if the email already exists (idempotent).
        """
        check_sql = 'SELECT 1 FROM public."user" WHERE "email" = %s LIMIT 1'
        with self._conn.cursor() as cur:
            cur.execute(check_sql, (email,))
            if cur.fetchone():
                logger.info("Monitoring user '%s' already exists — skipping", email)
                return False

        insert_sql = """
            INSERT INTO public."user" (
                "firstname", "lastname", "username", "email", "password",
                "subscriberId", "tenantIds", "roleIds", "groupIds",
                "status", "verification_status", "createdby", "updatedby",
                "createdon", "updatedon", "accountType", "emailSent"
            )
            VALUES (
                'Quilr', 'Monitor', %s, %s, %s,
                %s::uuid, %s, %s, %s,
                'active', 'unverified', 'QOnboard', 'QOnboard',
                NOW(), NOW(), 'credentials', FALSE
            )
        """
        try:
            with self._conn.cursor() as cur:
                cur.execute(insert_sql, (
                    email,
                    email,
                    password_hash,
                    tenant.subscriberid,
                    [tenant.id],
                    role_ids,
                    group_ids,
                ))
            self._conn.commit()
            logger.info("Monitoring user '%s' created", email)
            return True
        except Exception:
            self._conn.rollback()
            logger.exception("Failed to create monitoring user '%s'", email)
            raise

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
