"""
Quilr Customer Onboarding Agent
================================
Usage:
    python agent.py            # prompts for ticket ID, or processes all open tickets
    python agent.py OPS-123    # processes a specific ticket directly

Progress is saved to .onboard_state.json after each step.  If the agent is
restarted mid-ticket it will skip already-completed steps and resume from
where it left off.
"""

from __future__ import annotations

import argparse
import json
import logging
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

from logger_setup import setup_logging, console
from config import Config
from clients.jira_client import JiraClient, OnboardTicket
from clients.onboard_api import call_onboard_api_for_user, resolve_domain
from clients.env_registry import EnvRegistry
from state import StateManager

setup_logging()
logger = logging.getLogger("agent")


# ── Consent ────────────────────────────────────────────────────────────────────

class UserSkipped(Exception):
    """Raised when the operator declines to proceed with a step."""


def confirm_step(step_num: int, total: int, title: str, content) -> None:
    """Render a coloured panel with the step preview, then prompt Y/N.

    Raises UserSkipped if the operator enters N.
    """
    panel = Panel(
        content,
        title=f"[bold cyan]STEP {step_num}/{total}[/] — [bold white]{title}[/]",
        border_style="cyan",
        padding=(1, 2),
    )
    console.print()
    console.print(panel)
    if not Confirm.ask("  [bold]Proceed?[/bold]"):
        raise UserSkipped(f"Step {step_num}/{total} — [bold yellow]{title}[/] skipped by operator")


def skip_step(step_num: int, total: int, title: str) -> None:
    """Show a dimmed already-done indicator for a step completed in a previous run."""
    console.print()
    console.print(Rule(
        f"[dim] ✓  Step {step_num}/{total} — {title} — already completed [/]",
        style="dim green",
    ))


# ── Helpers ────────────────────────────────────────────────────────────────────

def extract_email_domain(email: str) -> str:
    if "@" not in email:
        raise ValueError(f"Invalid email address: '{email}'")
    return email.split("@", 1)[1].lower().strip()


# ── Core processing ────────────────────────────────────────────────────────────

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

    steps_done = state.get_steps_done(ticket.key)
    resume_note = (
        f"  [dim]Resuming — steps {steps_done} already done[/]"
        if steps_done else ""
    )

    info = Table.grid(padding=(0, 2))
    info.add_column(style="dim")
    info.add_column()
    info.add_row("Ticket", f"[bold blue]{ticket.key}[/] — {ticket.summary}")
    info.add_row("Env",    f"[bold green]{ticket.environment}[/]")
    info.add_row("Users",  user_table)
    if resume_note:
        info.add_row("", Text.from_markup(resume_note))
    console.print()
    console.print(Panel(info, border_style="blue", title="[bold blue] Customer Onboarding [/]"))

    jira.mark_in_progress(ticket.key)

    # Use the first user's email domain for all tenant-level operations
    # (all users on one ticket share the same tenant/domain)
    email_domain = extract_email_domain(ticket.users[0].email)

    try:
        # ── Env clients needed from step 1 onwards ─────────────────────
        env_clients = registry.get(ticket.environment)

        # ── STEP 1 — Onboard API (one call per new user) ───────────────
        domain = resolve_domain(ticket.environment)
        url = f"https://{domain}/bff/auth/auth/onboard"

        if state.is_step_done(ticket.key, 1):
            skip_step(1, 4, f"Onboard API  ({len(ticket.users)} user(s))")
        else:
            # Check which users are already in the DB
            new_users = []
            existing_users = []
            for u in ticket.users:
                if env_clients.pg.user_exists(u.email):
                    existing_users.append(u)
                else:
                    new_users.append(u)

            payload_preview = Table(box=None, padding=(0, 2), show_header=False)
            payload_preview.add_column(style="dim", no_wrap=True)
            payload_preview.add_column()
            idx = 1
            for u in new_users:
                payload_preview.add_row(
                    f"[{idx}]",
                    f"[cyan]{u.email}[/]  {u.firstname} {u.lastname}",
                )
                idx += 1
            for u in existing_users:
                payload_preview.add_row(
                    "[dim][skip][/dim]",
                    f"[dim]{u.email}  {u.firstname} {u.lastname}  (already in user table)[/dim]",
                )

            if not new_users:
                logger.info(
                    "[yellow]⚡[/] All %d user(s) already exist in the user table — skipping Onboard API",
                    len(existing_users),
                )
            else:
                title = f"Onboard API  ({len(new_users)} new"
                if existing_users:
                    title += f", {len(existing_users)} skipped"
                title += " user(s))"
                confirm_step(
                    1, 4, title,
                    Group(
                        Text(f"  POST  {url}", style="bold green"),
                        Text(""),
                        payload_preview,
                    ),
                )
                for u in new_users:
                    resp = call_onboard_api_for_user(u, domain, cfg)
                    logger.info("[green]✓[/] Onboard API — [cyan]%s[/]: %s", u.email, resp)

            state.mark_step_done(ticket.key, 1)

        # ── STEP 2 — PostgreSQL: fetch tenant (once per ticket) ────────
        if state.is_step_done(ticket.key, 2):
            tenant = state.get_tenant(ticket.key)
            if tenant is None:
                # Fallback: state file missing tenant data — re-fetch
                tenant = env_clients.pg.get_tenant(email_domain)
            skip_step(2, 4, "PostgreSQL — Fetch Tenant")
        else:
            confirm_step(
                2, 4, "PostgreSQL — Fetch Tenant",
                Syntax(
                    f'SELECT "id", "subscriberId", "name"\n'
                    f"FROM   public.tenant\n"
                    f'WHERE  "name" = \'{email_domain}\';',
                    "sql", theme="monokai",
                ),
            )
            tenant = env_clients.pg.get_tenant(email_domain)
            state.mark_step_done(ticket.key, 2)
            state.save_tenant(ticket.key, tenant)

        logger.info("[green]✓[/] Tenant — id=[bold]%s[/]  subscriberid=[bold]%s[/]", tenant.id, tenant.subscriberid)

        # ── STEP 3 — PostgreSQL: apply updates (once per ticket) ───────
        if state.is_step_done(ticket.key, 3):
            skip_step(3, 4, "PostgreSQL — Apply Updates")
        else:
            confirm_step(
                3, 4, "PostgreSQL — Apply Updates",
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
            )
            env_clients.pg.apply_onboarding_updates(email_domain)
            state.mark_step_done(ticket.key, 3)

        logger.info("[green]✓[/] PostgreSQL updates applied for [bold]%s[/]", email_domain)

        # ── STEP 4 — Neo4j: MERGE tenant node (once per ticket) ────────
        if state.is_step_done(ticket.key, 4):
            skip_step(4, 4, "Neo4j — MERGE Tenant Node")
        else:
            confirm_step(
                4, 4, "Neo4j — MERGE Tenant Node",
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
            )
            env_clients.neo4j.merge_tenant(tenant)
            state.mark_step_done(ticket.key, 4)

        logger.info("[green]✓[/] Neo4j TENANT node merged for [bold]%s[/]", tenant.name)

        # ── Done ───────────────────────────────────────────────────────
        user_lines = "\n".join(
            f"  - {u.firstname} {u.lastname} `{u.email}`" for u in ticket.users
        )
        comment = (
            f"*Onboarding completed successfully.*\n\n"
            f"*Users onboarded:*\n{user_lines}\n\n"
            f"- Environment: {ticket.environment}\n"
            f"- Tenant ID: `{tenant.id}`\n"
            f"- Subscriber ID: `{tenant.subscriberid}`"
        )
        jira.add_comment(ticket.key, comment)
        jira.mark_done(ticket.key)
        state.mark_completed(ticket.key)
        console.print()
        console.print(Rule(f"[bold green] ✓  {ticket.key} — {len(ticket.users)} user(s) completed [/]", style="green"))

    except UserSkipped as exc:
        logger.warning("%s", exc)
        jira.add_comment(ticket.key, f"Onboarding paused — {exc}")
        console.print()
        console.print(Rule(f"[bold yellow] ⏸  {ticket.key} skipped by operator [/]", style="yellow"))

    except Exception as exc:
        error_msg = traceback.format_exc()
        logger.error("[red]✗[/] Failed to process [bold]%s[/]: %s", ticket.key, exc)
        jira.add_comment(
            ticket.key,
            f"*Onboarding failed — manual intervention required.*\n\n"
            f"{{code}}\n{error_msg}\n{{code}}",
        )
        console.print()
        console.print(Rule(f"[bold red] ✗  {ticket.key} failed [/]", style="red"))
        raise exc


# ── Entry point ────────────────────────────────────────────────────────────────

def main(ticket_key: Optional[str] = None) -> None:
    # Prompt interactively if no key supplied via CLI
    if not ticket_key:
        raw = console.input("[bold]Enter Jira ticket ID[/] [dim](or press Enter for all open tickets)[/]: ").strip()
        ticket_key = raw or None

    cfg = Config()
    registry = EnvRegistry()
    jira = JiraClient(cfg)
    state = StateManager()

    try:
        if ticket_key:
            ticket = jira.fetch_ticket(ticket_key)
            if not ticket:
                logger.error("Could not parse [bold]%s[/] — check description and field config.", ticket_key)
                sys.exit(1)
            tickets = [ticket]
        else:
            tickets = jira.fetch_pending_tickets()
            if not tickets:
                logger.info("No pending Customer Onboard tickets found. Exiting.")
                return

        console.print()
        console.print(Rule(f"[bold] Processing {len(tickets)} customer(s) [/]", style="blue"))

        failed: list[str] = []
        for ticket in tickets:
            if state.is_completed(ticket.key):
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
            console.print(Rule(f"[bold red] ✗  {len(failed)} ticket(s) failed: {', '.join(failed)} [/]", style="red"))
            sys.exit(1)
        else:
            console.print(Rule("[bold green] ✓  All done [/]", style="green"))
    finally:
        registry.close_all()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Quilr Customer Onboarding Agent")
    parser.add_argument(
        "ticket",
        nargs="?",
        help="Jira ticket key (e.g. OPS-123). If omitted, the agent will prompt for it.",
    )
    args = parser.parse_args()
    main(ticket_key=args.ticket)
