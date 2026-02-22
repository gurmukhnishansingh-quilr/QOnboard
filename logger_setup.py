"""
Rich logging setup — coloured, readable output for the onboarding agent.
Import `console` here so the same instance is shared across the app.
"""

from __future__ import annotations

import logging

from rich.console import Console
from rich.logging import RichHandler

console = Console(highlight=True)


def setup_logging(debug: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="%(message)s",
        datefmt="[%H:%M:%S]",
        handlers=[
            RichHandler(
                console=console,
                rich_tracebacks=True,
                tracebacks_show_locals=False,
                show_path=False,
                markup=True,
                keywords=[
                    "Jira", "PostgreSQL", "Neo4j",
                    "Tenant", "Customer", "Onboard",
                    "CREATED", "MATCHED",
                ],
            )
        ],
        force=True,
    )
