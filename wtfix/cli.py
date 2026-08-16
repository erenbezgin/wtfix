"""
wtfix/cli.py -- Core CLI logic for the wtfix tool.

Two operating modes:
  1. Subprocess mode : wtfix python broken_app.py
  2. Pipe/stdin mode : cat error.log | wtfix
"""

from __future__ import annotations

import io
import os
import select
import subprocess
import sys
import warnings
from typing import List, Optional

# Suppress noisy urllib3/chardet version mismatch warning from third-party packages.
warnings.filterwarnings("ignore", category=Warning, message=".*urllib3.*")

import typer
from google import genai
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.spinner import Spinner
from rich.live import Live
from rich.text import Text

# ── Force UTF-8 I/O on Windows (avoids cp1254 UnicodeEncodeError with emojis) ─
# reconfigure() is a no-op on Python < 3.7 and on non-Windows systems.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ── Constants ──────────────────────────────────────────────────────────────────

APP_NAME = "wtfix"
MODEL_ID = "gemini-2.5-flash"
ENV_KEY = "GEMINI_API_KEY"

PROMPT_TEMPLATE = """\
You are an expert software engineer and debugger. A command-line tool produced the following error output:

```
{error_output}
```

Analyze the error above and respond ONLY in the following Markdown format — no preamble, no extra text:

## 1. 🔍 Hatanın Özü (Ne patladı?)
<One concise sentence describing exactly what crashed or failed.>

## 2. 💡 Neden Kaynaklandı? (Satır, dosya ve mantık hatası)
<Explain the root cause: which file, which line, and what logical/syntax mistake caused it.>

## 3. 🛠️ Çözüm Adımı (Doğrudan kopyalanıp çalıştırılabilecek komut veya düzeltilmiş kod parçası)
<Provide a ready-to-run command or corrected code snippet the user can copy and apply immediately.>
"""

# ── Shared Rich console ────────────────────────────────────────────────────────

console = Console(stderr=True)

# ── Typer application ──────────────────────────────────────────────────────────

app = typer.Typer(
    name=APP_NAME,
    help=(
        "[bold cyan]wtfix[/bold cyan] -- AI-powered terminal error analyzer.\n\n"
        "Run any command through wtfix and get instant, actionable fixes powered by Gemini."
    ),
    add_completion=False,
    rich_markup_mode="rich",
    invoke_without_command=True,
)


# ── Helper utilities ───────────────────────────────────────────────────────────


def _get_api_key() -> Optional[str]:
    """Return the Gemini API key from the environment, or None if absent."""
    return os.environ.get(ENV_KEY)


def _show_missing_key_warning() -> None:
    """Print a rich, user-friendly panel when the API key is not configured."""
    console.print(
        Panel.fit(
            Text.assemble(
                ("GEMINI_API_KEY", "bold red"),
                " environment variable is not set.\n\n",
                "Get a free API key at ",
                ("https://aistudio.google.com/app/apikey", "bold underline cyan"),
                "\n\nThen export it in your shell:\n\n",
                ("  # Linux / macOS\n", "dim"),
                ('  export GEMINI_API_KEY="your-key-here"\n\n', "bold green"),
                ("  # Windows PowerShell\n", "dim"),
                ('  $env:GEMINI_API_KEY = "your-key-here"\n\n', "bold green"),
                ("  # Windows CMD\n", "dim"),
                ('  set GEMINI_API_KEY=your-key-here', "bold green"),
            ),
            title="[bold yellow]⚠  API Key Missing[/bold yellow]",
            border_style="yellow",
            padding=(1, 2),
        )
    )


def _build_client() -> Optional[genai.Client]:
    """
    Instantiate and return the Gemini client.
    Returns None (after printing a warning) when the key is absent.
    """
    api_key = _get_api_key()
    if not api_key:
        _show_missing_key_warning()
        return None
    return genai.Client(api_key=api_key)


def _analyse_with_ai(client: genai.Client, error_output: str) -> str:
    """
    Send *error_output* to Gemini and return the raw markdown response text.
    Raises on network / API errors so the caller can handle gracefully.
    """
    prompt = PROMPT_TEMPLATE.format(error_output=error_output.strip())
    response = client.models.generate_content(
        model=MODEL_ID,
        contents=prompt,
    )
    return response.text


def _print_analysis(raw_markdown: str) -> None:
    """Render the AI markdown analysis inside a styled Rich panel."""
    console.print()
    console.print(
        Panel(
            Markdown(raw_markdown),
            title="[bold magenta]🤖 wtfix — AI Analysis[/bold magenta]",
            subtitle="[dim]Powered by Gemini 2.5 Flash[/dim]",
            border_style="magenta",
            padding=(1, 2),
        )
    )
    console.print()


def _print_success() -> None:
    """Inform the user that the command exited cleanly."""
    console.print(
        Panel.fit(
            "[bold green]✓ Command completed successfully — no errors detected.[/bold green]",
            border_style="green",
        )
    )


def _run_analysis(error_output: str, source_label: str = "stderr") -> None:
    """
    Orchestrate the full analysis flow:
      1. Build the Gemini client.
      2. Spin while contacting the API.
      3. Print the formatted result.
    """
    client = _build_client()
    if client is None:
        raise typer.Exit(code=1)

    console.print(
        f"\n[bold yellow]⚡ Analysing {source_label} with Gemini {MODEL_ID}…[/bold yellow]\n"
    )

    spinner_text = Text("Thinking", style="bold cyan")
    with Live(
        Spinner("dots", text=spinner_text, style="cyan"),
        console=console,
        refresh_per_second=15,
        transient=True,
    ):
        try:
            analysis = _analyse_with_ai(client, error_output)
        except Exception as exc:  # noqa: BLE001
            console.print(
                Panel.fit(
                    f"[bold red]Gemini API error:[/bold red] {exc}",
                    border_style="red",
                    title="[red]API Error[/red]",
                )
            )
            raise typer.Exit(code=1) from exc

    _print_analysis(analysis)


# ── Stdin (pipe) detection helper ──────────────────────────────────────────────


def _stdin_has_data() -> bool:
    """
    Return True when stdin is a pipe/file with data available.
    Works on both POSIX and Windows (falls back to a simple isatty check).
    """
    if not sys.stdin.isatty():
        # On POSIX we can use select for a non-blocking peek.
        if hasattr(select, "select"):
            try:
                ready, _, _ = select.select([sys.stdin], [], [], 0)
                return bool(ready)
            except (ValueError, OSError):
                pass
        # On Windows (no select for files) just trust isatty.
        return True
    return False


# ── Main command ───────────────────────────────────────────────────────────────


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    command: Optional[List[str]] = typer.Argument(
        default=None,
        help=(
            "Command and arguments to run "
            "(e.g. [bold]wtfix python app.py[/bold]). "
            "Omit to read from stdin."
        ),
        show_default=False,
    ),
    version: bool = typer.Option(
        False,
        "--version",
        "-v",
        help="Show the wtfix version and exit.",
        is_eager=True,
    ),
) -> None:
    """
    [bold cyan]wtfix[/bold cyan] -- What The Fix?

    Wrap any failing command or pipe error output to get instant AI-powered solutions.

    \b
    Examples:
      wtfix python broken_app.py
      wtfix npm run build
      wtfix go build ./...
      cat error.log | wtfix
      python app.py 2>&1 | wtfix
    """
    if version:
        from wtfix import __version__

        console.print(f"[bold cyan]wtfix[/bold cyan] v{__version__}")
        raise typer.Exit()

    # ── Mode A: Subprocess ─────────────────────────────────────────────────────
    if command:
        console.print(
            Panel.fit(
                f"[bold]$ {' '.join(command)}[/bold]",
                title="[cyan]Running command[/cyan]",
                border_style="cyan",
            )
        )

        result = subprocess.run(  # noqa: S603
            command,
            capture_output=True,
            text=True,
        )

        # Always echo stdout so the user sees normal output.
        if result.stdout:
            sys.stdout.write(result.stdout)
            sys.stdout.flush()

        if result.returncode == 0:
            _print_success()
            return

        # Command failed — echo stderr then analyse.
        if result.stderr:
            console.print(
                Panel(
                    Text(result.stderr.strip(), style="bold red"),
                    title="[red]stderr output[/red]",
                    border_style="red",
                    padding=(0, 1),
                )
            )
            _run_analysis(result.stderr, source_label="stderr")
        else:
            console.print(
                "[yellow]Command exited with a non-zero code but produced no stderr.[/yellow]"
            )

        raise typer.Exit(code=result.returncode)

    # ── Mode B: Stdin / pipe ───────────────────────────────────────────────────
    if _stdin_has_data():
        error_output = sys.stdin.read()
        if error_output.strip():
            console.print(
                Panel(
                    Text(error_output.strip(), style="bold red"),
                    title="[red]Piped input[/red]",
                    border_style="red",
                    padding=(0, 1),
                )
            )
            _run_analysis(error_output, source_label="piped input")
        else:
            console.print("[yellow]Received empty input — nothing to analyse.[/yellow]")
        return

    # ── No input at all: show help ─────────────────────────────────────────────
    console.print(ctx.get_help())


# ── Entry point ────────────────────────────────────────────────────────────────


def run() -> None:
    """Setuptools entry-point wrapper."""
    app()


if __name__ == "__main__":
    run()
