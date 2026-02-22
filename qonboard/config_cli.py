"""
Config subcommand handler for `qonboard config ...`.

Usage:
    qonboard config show [--env ENV_NAME]          list config values
    qonboard config set KEY VALUE [--env ENV_NAME]  upsert a value
    qonboard config init [--force]                  (re-)ingest from .env files
"""

from __future__ import annotations

import argparse

from rich.table import Table

from .config_store import ConfigStore, ENV_FILE_MAP
from .logger_setup import console


def handle_config(args: argparse.Namespace) -> None:
    store = ConfigStore.instance()
    sub = args.config_action

    if sub == "show":
        _show(store, args)
    elif sub == "set":
        _set(store, args)
    elif sub == "init":
        _init(store, args)
    else:
        console.print("[red]Unknown config action. Use: show | set | init[/]")


# ── show ──────────────────────────────────────────────────────────────


def _show(store: ConfigStore, args: argparse.Namespace) -> None:
    env_name: str | None = getattr(args, "env", None)

    if env_name:
        rows = store.list_env(env_name)
        if not rows:
            console.print(
                f"[yellow]No config found for environment '{env_name}'.[/]\n"
                "[dim]Run [bold]qonboard config init[/] to ingest from .env files, "
                "or [bold]qonboard config set KEY VALUE --env " + env_name + "[/] to add keys.[/]"
            )
            return
        t = Table(
            title=f"Config — {env_name}",
            show_header=True,
            header_style="bold cyan",
        )
        t.add_column("Key", style="cyan")
        t.add_column("Value")
        t.add_column("Updated", style="dim")
        for _, k, v, ts in rows:
            t.add_row(k, _mask(k, v), ts)
        console.print(t)
        return

    # ── Global ────────────────────────────────────────────────────────
    global_rows = store.list_global()
    if global_rows:
        t = Table(
            title="Global Config",
            show_header=True,
            header_style="bold cyan",
        )
        t.add_column("Key", style="cyan")
        t.add_column("Value")
        t.add_column("Updated", style="dim")
        for k, v, ts in global_rows:
            t.add_row(k, _mask(k, v), ts)
        console.print(t)
    else:
        console.print("[yellow]Global config is empty.[/]")

    # ── Env summary ───────────────────────────────────────────────────
    all_env = store.list_env()
    if all_env:
        env_counts: dict[str, int] = {}
        for env_n, _, _, _ in all_env:
            env_counts[env_n] = env_counts.get(env_n, 0) + 1

        t2 = Table(
            title="Env Config (summary)",
            show_header=True,
            header_style="bold cyan",
        )
        t2.add_column("Environment", style="green")
        t2.add_column("Keys", justify="right")
        for env_n in sorted(env_counts):
            t2.add_row(env_n, str(env_counts[env_n]))
        console.print(t2)
        console.print(
            "[dim]Use [bold]qonboard config show --env <NAME>[/] to see full details[/]"
        )
    else:
        console.print(
            "[yellow]No env config found.[/]\n"
            "[dim]Run [bold]qonboard config init[/] to ingest from .env_* files.[/]"
        )


# ── set ───────────────────────────────────────────────────────────────


def _set(store: ConfigStore, args: argparse.Namespace) -> None:
    key: str = args.key
    value: str = args.value
    env_name: str | None = getattr(args, "env", None)

    if env_name:
        if env_name not in ENV_FILE_MAP:
            console.print(
                f"[red]Unknown environment '{env_name}'.[/] "
                f"Valid values: {list(ENV_FILE_MAP.keys())}"
            )
            return
        store.set_env(env_name, key, value)
        console.print(f"[green]OK[/] Set [cyan]{env_name}[/] / [bold]{key}[/]")
    else:
        store.set_global(key, value)
        console.print(f"[green]OK[/] Set global [bold]{key}[/]")


# ── init ──────────────────────────────────────────────────────────────


def _init(store: ConfigStore, args: argparse.Namespace) -> None:
    force: bool = getattr(args, "force", False)
    if force:
        console.print("[cyan]Re-ingesting from .env files (overwriting existing values)...[/]")
    else:
        console.print("[cyan]Ingesting from .env files (skipping existing keys)...[/]")

    results = store.ingest_from_files(force=force)

    console.print(f"[green]OK[/] Global keys ingested: [bold]{results['global']}[/]")
    for env_name, count in sorted(results["env"].items()):
        console.print(f"[green]OK[/] [cyan]{env_name}[/]: [bold]{count}[/] key(s)")
    console.print("[dim]Done. Run [bold]qonboard config show[/] to verify.[/]")


# ── helpers ───────────────────────────────────────────────────────────


def _mask(key: str, value: str) -> str:
    """Partially mask sensitive values."""
    lower = key.lower()
    if any(s in lower for s in ("password", "token", "secret", "api_key")):
        if len(value) > 4:
            return value[:2] + "***" + value[-2:]
        return "***"
    return value
