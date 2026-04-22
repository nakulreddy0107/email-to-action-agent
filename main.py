"""CLI entry point.

Usage:
  python main.py --demo                  # run all sample emails
  python main.py --email path/to.json    # run a single email from a JSON file
  python main.py --serve                 # start the FastAPI server
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from core.orchestrator import Orchestrator

console = Console()


def _print_result(result) -> None:
    console.rule(f"[bold cyan]{result.subject}[/bold cyan]", align="left")

    # Intents table
    intent_tbl = Table(title="Detected intents", box=box.SIMPLE, show_header=True, header_style="dim")
    intent_tbl.add_column("Type", style="cyan")
    intent_tbl.add_column("Confidence", justify="right")
    intent_tbl.add_column("Summary")
    for i in result.intents:
        conf_style = "green" if i.confidence >= 0.8 else "yellow" if i.confidence >= 0.5 else "red"
        intent_tbl.add_row(i.intent_type.value, f"[{conf_style}]{i.confidence:.2f}[/{conf_style}]", i.summary)
    console.print(intent_tbl)

    # Actions + results table
    act_tbl = Table(title="Actions", box=box.SIMPLE, show_header=True, header_style="dim")
    act_tbl.add_column("Tool", style="magenta")
    act_tbl.add_column("Status")
    act_tbl.add_column("External ID")
    act_tbl.add_column("Message")
    for action, res in zip(result.actions, result.results):
        status_style = {
            "executed": "green",
            "dry_run": "cyan",
            "pending": "yellow",
            "failed": "red",
            "skipped_policy": "red",
            "skipped_low_confidence": "yellow",
        }.get(res.status.value, "white")
        act_tbl.add_row(
            action.tool,
            f"[{status_style}]{res.status.value}[/{status_style}]",
            res.external_id or "-",
            res.message[:60],
        )
    console.print(act_tbl)

    # Audit trail
    console.print(Panel("\n".join(result.audit_trail), title="Audit trail", border_style="dim"))
    console.print()


def run_demo() -> None:
    samples_path = Path(__file__).parent / "data" / "sample_emails.json"
    with samples_path.open() as f:
        samples = json.load(f)

    console.print(f"[bold]Running {len(samples)} sample emails through the pipeline…[/bold]\n")
    orch = Orchestrator()
    for email_data in samples:
        try:
            result = orch.run(email_data)
            _print_result(result)
        except Exception as e:
            console.print(f"[red]FAILED on email {email_data.get('subject')!r}: {e}[/red]")


def run_single(path: str) -> None:
    with Path(path).open() as f:
        data = json.load(f)
    orch = Orchestrator()
    result = orch.run(data)
    _print_result(result)


def run_server() -> None:
    import uvicorn
    uvicorn.run("api.main:app", host="0.0.0.0", port=8000, reload=False)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    # Quiet down noisy libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)

    parser = argparse.ArgumentParser(description="Email-to-Action Agent")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--demo", action="store_true", help="Run sample emails")
    group.add_argument("--email", type=str, help="Path to a JSON email file")
    group.add_argument("--serve", action="store_true", help="Start FastAPI server")
    args = parser.parse_args()

    if args.demo:
        run_demo()
    elif args.email:
        run_single(args.email)
    elif args.serve:
        run_server()


if __name__ == "__main__":
    sys.exit(main() or 0)
