"""
Quilr Customer Onboarding Agent
================================
Usage:
    qonboard                            prompt for ticket ID, or process all open
    qonboard OPS-123                    process a specific ticket
    python -m qonboard OPS-123          alternative without installing

    qonboard config show [--env NAME]   list config values
    qonboard config set KEY VAL [--env] upsert a config value
    qonboard config init [--force]      ingest from .env files

Progress is saved to .onboard_state.json after each step per environment.
Restarting resumes from where it left off.
"""

from __future__ import annotations

import argparse
import bcrypt
import logging
import secrets
import sys
import traceback
from typing import Optional

from rich.console import Group
from rich.panel import Panel
from rich.prompt import Confirm
from rich.rule import Rule
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from .logger_setup import setup_logging, console
from .config import Config
from .config_store import ENV_FILE_MAP
from .clients.jira_client import JiraClient, OnboardTicket
from .clients.onboard_api import call_onboard_api_for_user, resolve_domain
from .clients.env_registry import EnvRegistry
from .state import StateManager

setup_logging()
logger = logging.getLogger("agent")

_TOTAL_STEPS = 5
_ALL_ENV_NAMES = list(ENV_FILE_MAP.keys())


# ── Consent ────────────────────────────────────────────────────────────────────

class UserSkipped(Exception):
    """Raised when the operator declines to proceed with a step."""


def confirm_step(step_num: int, title: str, content, env_name: str = "") -> None:
    """Render a coloured panel with the step preview, then prompt Y/N."""
    env_tag = f" [dim]({env_name})[/]" if env_name else ""
    panel = Panel(
        content,
        title=(
            f"[bold cyan]STEP {step_num}/{_TOTAL_STEPS}[/]"
            f" — [bold white]{title}[/]{env_tag}"
        ),
        border_style="cyan",
        padding=(1, 2),
    )
    console.print()
    console.print(panel)
    if not Confirm.ask("  [bold]Proceed?[/bold]"):
        raise UserSkipped(
            f"Step {step_num}/{_TOTAL_STEPS} — {title}{' (' + env_name + ')' if env_name else ''} "
            "skipped by operator"
        )


def skip_step(step_num: int, title: str, env_name: str = "") -> None:
    """Show a dimmed already-done indicator for a completed step."""
    env_tag = f" ({env_name})" if env_name else ""
    console.print()
    console.print(Rule(
        f"[dim] ✓  Step {step_num}/{_TOTAL_STEPS} — {title}{env_tag} — already completed [/]",
        style="dim green",
    ))


# ── Helpers ────────────────────────────────────────────────────────────────────

def extract_email_domain(email: str) -> str:
    if "@" not in email:
        raise ValueError(f"Invalid email address: '{email}'")
    return email.split("@", 1)[1].lower().strip()


def monitoring_email(email_domain: str) -> str:
    """Derive the monitoring user email: monitor+{domain-without-tld}@quilr.ai."""
    label = email_domain.rsplit(".", 1)[0]
    return f"monitor+{label}@quilr.ai"


def generate_password() -> tuple[str, str]:
    """Return (plaintext, bcrypt_hash) for a new random password."""
    plaintext = secrets.token_urlsafe(16)
    hashed = bcrypt.hashpw(plaintext.encode(), bcrypt.gensalt()).decode()
    return plaintext, hashed


# ── Per-environment processing ─────────────────────────────────────────────────

def process_env(
    ticket: OnboardTicket,
    env_name: str,
    email_domain: str,
    monitor_pw_plaintext: str,
    registry: EnvRegistry,
    cfg: Config,
    state: StateManager,
) -> None:
    """Run all 5 steps for a single environment."""

    console.print()
    console.print(Rule(
        f"[bold cyan] {env_name} [/]",
        style="cyan",
    ))

    env_clients = registry.get(env_name)

    # ── STEP 1 — Onboard API ───────────────────────────────────────────
    try:
        domain = resolve_domain(env_name)
    except ValueError:
        logger.warning("[yellow]⚡[/] No Onboard API domain for '%s' — skipping step 1", env_name)
        state.mark_step_done(ticket.key, env_name, 1)
        domain = None

    url = f"https://{domain}/bff/auth/auth/onboard" if domain else ""

    if state.is_step_done(ticket.key, env_name, 1):
        skip_step(1, "Onboard API", env_name)
    else:
        new_users = []
        existing_users: list[tuple] = []
        for u in ticket.users:
            account_type = env_clients.pg.get_user_account_type(u.email)
            if account_type is None:
                new_users.append(u)
            elif account_type.lower() == "credentials":
                existing_users.append((u, "internal user — credentials account"))
            else:
                existing_users.append((u, f"already onboarded — {account_type} account"))

        payload_preview = Table(box=None, padding=(0, 2), show_header=False)
        payload_preview.add_column(style="dim", no_wrap=True)
        payload_preview.add_column()
        for idx, u in enumerate(new_users, 1):
            payload_preview.add_row(
                f"[{idx}]",
                f"[cyan]{u.email}[/]  {u.firstname} {u.lastname}",
            )
        for u, reason in existing_users:
            payload_preview.add_row(
                "[dim][skip][/dim]",
                f"[dim]{u.email}  {u.firstname} {u.lastname}  ({reason})[/dim]",
            )

        if not new_users:
            logger.info(
                "[yellow]⚡[/] All %d user(s) skipped for %s — no Onboard API calls needed",
                len(existing_users), env_name,
            )
        else:
            title = f"Onboard API  ({len(new_users)} new"
            if existing_users:
                title += f", {len(existing_users)} skipped"
            title += " user(s))"
            confirm_step(
                1, title,
                Group(
                    Text(f"  POST  {url}", style="bold green"),
                    Text(""),
                    payload_preview,
                ),
                env_name,
            )
            for u in new_users:
                resp = call_onboard_api_for_user(u, domain, cfg)
                logger.info("[green]✓[/] Onboard API — [cyan]%s[/]: %s", u.email, resp)

        state.mark_step_done(ticket.key, env_name, 1)

    # ── STEP 2 — PostgreSQL: fetch tenant ─────────────────────────────
    if state.is_step_done(ticket.key, env_name, 2):
        tenant = state.get_tenant(ticket.key, env_name)
        if tenant is None:
            tenant = env_clients.pg.get_tenant(email_domain)
        skip_step(2, "PostgreSQL — Fetch Tenant", env_name)
    else:
        confirm_step(
            2, "PostgreSQL — Fetch Tenant",
            Syntax(
                f'SELECT "id", "subscriberId", "name"\n'
                f"FROM   public.tenant\n"
                f'WHERE  "name" = \'{email_domain}\';',
                "sql", theme="monokai",
            ),
            env_name,
        )
        tenant = env_clients.pg.get_tenant(email_domain)
        state.mark_step_done(ticket.key, env_name, 2)
        state.save_tenant(ticket.key, env_name, tenant)

    logger.info(
        "[green]✓[/] Tenant — id=[bold]%s[/]  subscriberid=[bold]%s[/]",
        tenant.id, tenant.subscriberid,
    )

    # ── STEP 3 — Create monitoring user ───────────────────────────────
    monitor_email_addr = monitoring_email(email_domain)

    if state.is_step_done(ticket.key, env_name, 3):
        skip_step(3, "PostgreSQL — Create Monitoring User", env_name)
        # Re-display password so the operator can record it
        console.print()
        console.print(Panel(
            Group(
                Text(f"  Email:    {monitor_email_addr}", style="cyan"),
                Text(f"  Password: {monitor_pw_plaintext}", style="bold yellow"),
            ),
            title="[dim] Monitoring user credentials (from previous run) [/]",
            border_style="dim",
        ))
    else:
        role_ids  = env_clients.pg.get_tenant_role_ids(tenant.id)
        group_ids = env_clients.pg.get_tenant_group_ids(tenant.id)
        # Re-hash the shared plaintext for this env's DB
        password_hash = bcrypt.hashpw(
            monitor_pw_plaintext.encode(), bcrypt.gensalt()
        ).decode()

        role_str  = "{" + ",".join(role_ids)  + "}" if role_ids  else "{}"
        group_str = "{" + ",".join(group_ids) + "}" if group_ids else "{}"

        confirm_step(
            3, "PostgreSQL — Create Monitoring User",
            Syntax(
                f'INSERT INTO public."user" (\n'
                f'    "firstname", "lastname", "username", "email", "password",\n'
                f'    "subscriberId", "tenantIds", "roleIds", "groupIds",\n'
                f'    "accountType", "status"\n'
                f') VALUES (\n'
                f"    'Quilr', 'Monitor', '{monitor_email_addr}', '{monitor_email_addr}', '<bcrypt>',\n"
                f"    '{tenant.subscriberid}', '{{{tenant.id}}}',\n"
                f"    '{role_str}', '{group_str}',\n"
                f"    'credentials', 'active'\n"
                f');',
                "sql", theme="monokai",
            ),
            env_name,
        )
        created = env_clients.pg.create_monitoring_user(
            monitor_email_addr, tenant, password_hash, role_ids, group_ids
        )
        state.mark_step_done(ticket.key, env_name, 3)
        state.save_monitoring_user(ticket.key, env_name, monitor_email_addr)

        console.print()
        if created:
            console.print(Panel(
                Group(
                    Text(f"  Email:    {monitor_email_addr}", style="cyan"),
                    Text(f"  Password: {monitor_pw_plaintext}", style="bold yellow"),
                ),
                title="[bold yellow] ⚠  Save this password — it will not be shown again [/]",
                border_style="yellow",
                padding=(1, 2),
            ))
            logger.info(
                "[green]✓[/] Monitoring user created: [cyan]%s[/] (%s)",
                monitor_email_addr, env_name,
            )
        else:
            logger.info(
                "[yellow]⚡[/] Monitoring user [cyan]%s[/] already existed in %s — skipped",
                monitor_email_addr, env_name,
            )

    # ── STEP 4 — PostgreSQL: apply updates ────────────────────────────
    if state.is_step_done(ticket.key, env_name, 4):
        skip_step(4, "PostgreSQL — Apply Updates", env_name)
    else:
        confirm_step(
            4, "PostgreSQL — Apply Updates",
            Syntax(
                f"UPDATE public.tenant\n"
                f'  SET "license_config" = \'{{"ai_axis_enabled": true}}\'\n'
                f'  WHERE "name" = \'{email_domain}\';\n'
                f"\n"
                f"UPDATE public.subscriber\n"
                f'  SET "is_onboarded"        = TRUE,\n'
                f'      "is_analysisComplete" = TRUE,\n'
                f'      "areControlsEnabled"  = TRUE\n'
                f'  WHERE "name" = \'{email_domain}\';',
                "sql", theme="monokai",
            ),
            env_name,
        )
        env_clients.pg.apply_onboarding_updates(email_domain)
        state.mark_step_done(ticket.key, env_name, 4)

    logger.info(
        "[green]✓[/] PostgreSQL updates applied for [bold]%s[/] (%s)",
        email_domain, env_name,
    )

    # ── STEP 5 — Neo4j: MERGE tenant node ─────────────────────────────
    if state.is_step_done(ticket.key, env_name, 5):
        skip_step(5, "Neo4j — MERGE Tenant Node", env_name)
    else:
        confirm_step(
            5, "Neo4j — MERGE Tenant Node",
            Syntax(
                f"MERGE (TENANT_0:TENANT {{\n"
                f"  id:         '{tenant.id}',\n"
                f"  subscriber: '{tenant.subscriberid}',\n"
                f"  tenant:     '{tenant.id}'\n"
                f"}})\n"
                f"ON CREATE SET\n"
                f"  TENANT_0.creationTime = <now>,\n"
                f"  TENANT_0.internalId   = randomUUID(),\n"
                f"  TENANT_0.new          = true\n"
                f"ON MATCH SET\n"
                f"  TENANT_0.new          = false,\n"
                f"  TENANT_0.timestamp    = timestamp()",
                "cypher", theme="monokai",
            ),
            env_name,
        )
        env_clients.neo4j.merge_tenant(tenant)
        state.mark_step_done(ticket.key, env_name, 5)

    logger.info(
        "[green]✓[/] Neo4j TENANT node merged for [bold]%s[/] (%s)",
        tenant.name, env_name,
    )

    state.mark_env_completed(ticket.key, env_name)
    console.print()
    console.print(Rule(
        f"[bold green] ✓  {env_name} — all steps completed [/]",
        style="green",
    ))


# ── Ticket-level processing ────────────────────────────────────────────────────

def process_ticket(
    ticket: OnboardTicket,
    jira: JiraClient,
    registry: EnvRegistry,
    cfg: Config,
    state: StateManager,
) -> None:
    # ── Ticket header ──────────────────────────────────────────────────
    user_table = Table(box=None, padding=(0, 2), show_header=False)
    user_table.add_column(style="dim", no_wrap=True)
    user_table.add_column()
    user_table.add_column(style="cyan")
    for i, u in enumerate(ticket.users, 1):
        user_table.add_row(f"  User {i}", f"{u.firstname} {u.lastname}", u.email)

    info = Table.grid(padding=(0, 2))
    info.add_column(style="dim")
    info.add_column()
    info.add_row("Ticket", f"[bold blue]{ticket.key}[/] — {ticket.summary}")
    info.add_row("Env",    f"[bold green]{ticket.environment}[/]")
    info.add_row("Users",  user_table)
    console.print()
    console.print(Panel(info, border_style="blue", title="[bold blue] Customer Onboarding [/]"))

    env_name = ticket.environment

    jira.mark_in_progress(ticket.key)

    email_domain = extract_email_domain(ticket.users[0].email)

    # ── Monitoring password (ticket-level) ─────────────────────────────
    monitor_pw_plaintext = state.get_monitor_password(ticket.key)
    if monitor_pw_plaintext is None:
        monitor_pw_plaintext, _ = generate_password()
        state.save_monitor_password(ticket.key, monitor_pw_plaintext)

    if state.is_env_completed(ticket.key, env_name):
        console.print()
        console.print(Rule(
            f"[dim] ✓  {env_name} — already completed, skipping [/]",
            style="dim green",
        ))
        return

    try:
        process_env(
            ticket, env_name, email_domain, monitor_pw_plaintext,
            registry, cfg, state,
        )
    except UserSkipped as exc:
        logger.warning("%s", exc)
        jira.add_comment(ticket.key, f"Onboarding paused — {exc}")
        return
    except Exception as exc:
        logger.error(
            "[red]✗[/] Failed to process [bold]%s[/] / [bold]%s[/]: %s",
            ticket.key, env_name, exc,
        )
        logger.debug(traceback.format_exc())
        jira.add_comment(
            ticket.key,
            f"*Onboarding failed for {env_name} — manual intervention required.*\n\n"
            f"{{code}}\n{traceback.format_exc()}\n{{code}}",
        )
        raise

    # ── Wrap up ────────────────────────────────────────────────────────
    monitor_email_addr = monitoring_email(email_domain)
    user_lines = "\n".join(
        f"  - {u.firstname} {u.lastname} `{u.email}`" for u in ticket.users
    )
    comment = (
        f"*Onboarding completed.*\n\n"
        f"*Users onboarded:*\n{user_lines}\n\n"
        f"- Environment: {env_name}\n"
        f"- Monitoring user: `{monitor_email_addr}`"
    )
    jira.add_comment(ticket.key, comment)
    jira.mark_done(ticket.key)
    state.mark_completed(ticket.key)
    console.print()
    console.print(Rule(
        f"[bold green] ✓  {ticket.key} — {env_name} completed [/]",
        style="green",
    ))


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Quilr Customer Onboarding Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command")

    # ── config subcommand ──────────────────────────────────────────────
    config_p = sub.add_parser("config", help="Manage configuration")
    config_sub = config_p.add_subparsers(dest="config_action")

    show_p = config_sub.add_parser("show", help="List config values")
    show_p.add_argument("--env", metavar="ENV_NAME", help="Filter by environment")

    set_p = config_sub.add_parser("set", help="Upsert a config value")
    set_p.add_argument("key", help="Config key (e.g. JIRA_URL)")
    set_p.add_argument("value", help="Config value")
    set_p.add_argument("--env", metavar="ENV_NAME", help="Set in env config instead of global")

    init_p = config_sub.add_parser(
        "init", help="Ingest config from .env / .env_* files"
    )
    init_p.add_argument(
        "--force", action="store_true",
        help="Overwrite existing values (default: skip existing keys)",
    )

    # ── onboard subcommand (default) ───────────────────────────────────
    parser.add_argument(
        "ticket",
        nargs="?",
        help="Jira ticket key (e.g. OPS-123). Omit to be prompted.",
    )

    args = parser.parse_args()

    # ── Dispatch config ────────────────────────────────────────────────
    if args.command == "config":
        from .config_cli import handle_config
        if not args.config_action:
            config_p.print_help()
            sys.exit(0)
        handle_config(args)
        return

    # ── Onboarding flow ────────────────────────────────────────────────
    ticket_key: Optional[str] = args.ticket
    if not ticket_key:
        raw = console.input(
            "[bold]Enter Jira ticket ID[/] [dim](or press Enter for all open tickets)[/]: "
        ).strip()
        ticket_key = raw or None

    cfg = Config()
    registry = EnvRegistry()
    jira = JiraClient(cfg)
    state = StateManager()

    try:
        if ticket_key:
            ticket = jira.fetch_ticket(ticket_key)
            if not ticket:
                logger.error(
                    "Could not parse [bold]%s[/] — check description and field config.",
                    ticket_key,
                )
                sys.exit(1)
            tickets = [ticket]
        else:
            tickets = jira.fetch_pending_tickets()
            if not tickets:
                logger.info("No pending Customer Onboard tickets found. Exiting.")
                return

        console.print()
        console.print(Rule(
            f"[bold] Processing {len(tickets)} ticket(s) [/]",
            style="blue",
        ))

        failed: list[str] = []
        for ticket in tickets:
            if state.is_completed(ticket.key, [ticket.environment]):
                console.print()
                console.print(Rule(
                    f"[dim] ✓  {ticket.key} — already fully onboarded, skipping [/]",
                    style="dim green",
                ))
                continue

            try:
                process_ticket(ticket, jira, registry, cfg, state)
            except Exception:  # noqa: BLE001
                failed.append(ticket.key)

        console.print()
        if failed:
            console.print(Rule(
                f"[bold red] ✗  {len(failed)} ticket(s) failed: {', '.join(failed)} [/]",
                style="red",
            ))
            sys.exit(1)
        else:
            console.print(Rule("[bold green] ✓  All done [/]", style="green"))
    finally:
        registry.close_all()


if __name__ == "__main__":
    main()
