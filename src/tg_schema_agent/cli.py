from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from tg_schema_agent import io_utils, patterns, scorer, validator
from tg_schema_agent.designer import design_schema, design_schema_with_ai
from tg_schema_agent.emitters import gsql as gsql_emitter
from tg_schema_agent.emitters import markdown as md_emitter
from tg_schema_agent.enums import UseCase
from tg_schema_agent.profiler import profile_directory

app = typer.Typer(
    name="tg-schema",
    help="TigerGraph Schema Creation Agent — deterministic core.",
    add_completion=False,
    no_args_is_help=True,
)
console = Console()


def _parse_use_case(value: str) -> UseCase:
    try:
        return UseCase(value.upper())
    except ValueError as exc:
        valid = ", ".join(u.value for u in UseCase)
        raise typer.BadParameter(f"Unknown use case '{value}'. Choose one of: {valid}") from exc


@app.command()
def profile(
    directory: Annotated[Path, typer.Argument(help="Directory containing CSV input files")],
    out: Annotated[Path, typer.Option("--out", "-o", help="Where to write profile.json")] = Path("profile.json"),
) -> None:
    """Profile every CSV in the given directory."""
    profiles = profile_directory(directory)
    io_utils.dump_json([p.model_dump(mode="json") for p in profiles], out)
    table = Table(title=f"Profiles ({len(profiles)} tables)")
    table.add_column("Table")
    table.add_column("Rows")
    table.add_column("Delim")
    table.add_column("PK")
    table.add_column("Event?")
    table.add_column("Wide?")
    for p in profiles:
        table.add_row(
            p.name,
            f"{p.row_count}",
            repr(p.detected_delimiter),
            ",".join(p.primary_key or []) or "—",
            "yes" if p.has_event_signature else "no",
            "yes" if p.is_wide_denormalized else "no",
        )
    console.print(table)
    console.print(f"[green]Wrote[/] {out}")


@app.command()
def design(
    directory: Annotated[Path, typer.Argument(help="Directory containing CSV input files")],
    use_case: Annotated[str, typer.Option("--use-case", "-u", help="fraud / entity_resolution / customer_360 / recommendation")] = "fraud",
    out: Annotated[Path, typer.Option("--out", "-o", help="Where to write schema.json")] = Path("schema.json"),
    patterns_dir: Annotated[Path | None, typer.Option("--patterns-dir")] = None,
    ai: Annotated[bool, typer.Option("--ai", help="Use Gemini to design the schema (needs GEMINI_API_KEY)")] = False,
    prompt: Annotated[str | None, typer.Option("--prompt", "-p", help="Free-text intent passed to the LLM")] = None,
) -> None:
    """Design a schema for the given input directory."""
    uc = _parse_use_case(use_case)
    profiles = profile_directory(directory)
    if ai or prompt:
        csv_paths = sorted(directory.glob("*.csv"))
        schema, info = design_schema_with_ai(
            profiles=profiles,
            use_case=uc,
            user_prompt=prompt,
            csv_paths=csv_paths,
            patterns_dir=patterns_dir,
        )
        console.print(f"[bold]Design mode:[/] {info.get('mode')}  {info.get('reason', '')}")
    else:
        schema = design_schema(profiles, uc, patterns_dir=patterns_dir)
    io_utils.dump_schema(schema, out)
    console.print(f"[bold]{schema.name}[/] — {len(schema.vertices)} vertices, {len(schema.edges)} edges")
    console.print(f"[green]Wrote[/] {out}")


@app.command()
def validate(  # noqa: A001
    schema_path: Annotated[Path, typer.Argument(help="Path to schema.json")],
    out: Annotated[Path, typer.Option("--out", "-o", help="Where to write validation.json")] = Path("validation.json"),
) -> None:
    """Validate a schema and report answerable target questions."""
    schema = io_utils.load_schema(schema_path)
    result = validator.validate(schema)
    io_utils.dump_json(result.model_dump(mode="json"), out)
    table = Table(title="Validation checks")
    table.add_column("Check")
    table.add_column("Result")
    table.add_column("Detail", overflow="fold")
    for c in result.checks:
        table.add_row(c.id, "PASS" if c.passed else "FAIL", c.detail or "")
    console.print(table)
    console.print(f"Answerable: {result.answerable_questions}")
    console.print(f"Unanswerable: {result.unanswerable_questions}")
    console.print(f"[green]Wrote[/] {out}")


@app.command()
def score(
    schema_path: Annotated[Path, typer.Argument(help="Path to schema.json")],
    use_case: Annotated[str, typer.Option("--use-case", "-u")] = "fraud",
    out: Annotated[Path, typer.Option("--out", "-o", help="Where to write score.json")] = Path("score.json"),
    patterns_dir: Annotated[Path | None, typer.Option("--patterns-dir")] = None,
) -> None:
    """Score a schema (0–100)."""
    uc = _parse_use_case(use_case)
    schema = io_utils.load_schema(schema_path)
    validation = validator.validate(schema)
    pattern = patterns.load_patterns(patterns_dir)[uc]
    s = scorer.score_schema(schema, validation, pattern)
    io_utils.dump_json(s.model_dump(mode="json"), out)
    console.print(f"[bold]Score: {s.total}/100[/]")
    for k, v in s.breakdown.items():
        console.print(f"  {k:24s} {v}")
    console.print(f"[green]Wrote[/] {out}")


@app.command()
def emit(
    schema_path: Annotated[Path, typer.Argument(help="Path to schema.json")],
    directory: Annotated[Path, typer.Option("--directory", "-d", help="Source CSV directory (for loading job)")] = Path("."),
    fmt: Annotated[str, typer.Option("--format", "-f", help="gsql | markdown")] = "gsql",
    out: Annotated[Path, typer.Option("--out", "-o")] = Path("schema.gsql"),
    use_case: Annotated[str, typer.Option("--use-case", "-u")] = "fraud",
) -> None:
    """Emit a schema to GSQL or Markdown."""
    schema = io_utils.load_schema(schema_path)
    if fmt == "gsql":
        profiles = profile_directory(directory) if directory.exists() else []
        text = gsql_emitter.emit(schema, profiles)
    elif fmt == "markdown":
        validation = validator.validate(schema)
        uc = _parse_use_case(use_case)
        pattern = patterns.load_patterns()[uc]
        s = scorer.score_schema(schema, validation, pattern)
        text = md_emitter.emit_markdown(schema, validation, s)
    else:
        raise typer.BadParameter(f"Unknown format '{fmt}'. Use 'gsql' or 'markdown'.")
    out.write_text(text, encoding="utf-8")
    console.print(f"[green]Wrote[/] {out}")


@app.command()
def run(
    directory: Annotated[Path, typer.Argument(help="Directory containing CSV input files")],
    use_case: Annotated[str, typer.Option("--use-case", "-u")] = "fraud",
    out: Annotated[Path, typer.Option("--out", "-o", help="Output directory")] = Path("build"),
    patterns_dir: Annotated[Path | None, typer.Option("--patterns-dir")] = None,
    ai: Annotated[bool, typer.Option("--ai", help="Use Gemini for schema design (needs GEMINI_API_KEY)")] = False,
    prompt: Annotated[str | None, typer.Option("--prompt", "-p", help="Free-text intent passed to the LLM")] = None,
) -> None:
    """Full pipeline: profile -> design -> validate -> score -> emit (gsql + markdown)."""
    uc = _parse_use_case(use_case)
    out.mkdir(parents=True, exist_ok=True)

    console.rule("[bold cyan]Step 1: profiling")
    profiles = profile_directory(directory)
    io_utils.dump_json([p.model_dump(mode="json") for p in profiles], out / "profile.json")
    for p in profiles:
        console.print(f"  {p.name}: {p.row_count} rows, delim={p.detected_delimiter!r}, event={p.has_event_signature}, wide={p.is_wide_denormalized}")

    console.rule("[bold cyan]Step 2: designing schema")
    if ai or prompt:
        csv_paths = sorted(directory.glob("*.csv"))
        schema, info = design_schema_with_ai(
            profiles=profiles,
            use_case=uc,
            user_prompt=prompt,
            csv_paths=csv_paths,
            patterns_dir=patterns_dir,
        )
        io_utils.dump_json(info, out / "design_info.json")
        mode = info.get("mode", "deterministic")
        color = "magenta" if mode == "ai" else "yellow"
        console.print(f"  [bold {color}]design mode: {mode}[/]  {info.get('reason', '')}")
    else:
        schema = design_schema(profiles, uc, patterns_dir=patterns_dir)
    io_utils.dump_schema(schema, out / "schema.json")
    console.print(f"  {schema.name}: {len(schema.vertices)} vertices, {len(schema.edges)} edges")

    console.rule("[bold cyan]Step 3: validating")
    validation = validator.validate(schema)
    io_utils.dump_json(validation.model_dump(mode="json"), out / "validation.json")
    console.print(f"  Answerable: {len(validation.answerable_questions)}/{len(schema.target_questions)}")
    if validation.unanswerable_questions:
        console.print(f"  [yellow]Unanswerable:[/] {validation.unanswerable_questions}")

    console.rule("[bold cyan]Step 4: scoring")
    pattern = patterns.load_patterns(patterns_dir)[uc]
    s = scorer.score_schema(schema, validation, pattern)
    io_utils.dump_json(s.model_dump(mode="json"), out / "score.json")
    console.print(f"  [bold green]Score: {s.total}/100[/]")

    console.rule("[bold cyan]Step 5: emitting GSQL + Markdown")
    gsql_text = gsql_emitter.emit(schema, profiles)
    (out / "schema.gsql").write_text(gsql_text, encoding="utf-8")
    md_text = md_emitter.emit_markdown(schema, validation, s)
    (out / "schema.md").write_text(md_text, encoding="utf-8")

    console.rule("[bold green]Done")
    console.print(f"[green]All artifacts written to:[/] {out.resolve()}")
    for name in ("profile.json", "schema.json", "validation.json", "score.json", "schema.gsql", "schema.md"):
        console.print(f"  - {out / name}")


@app.command()
def deploy(
    schema_path: Annotated[Path, typer.Argument(help="Path to schema.json")],
    csv: Annotated[Path, typer.Option("--csv", "-c", help="CSV file to load")],
    graph_name: Annotated[str | None, typer.Option("--graph-name", "-g", help="TigerGraph graph name (defaults to <use_case>_graph)")] = None,
    use_case: Annotated[str, typer.Option("--use-case", "-u")] = "fraud",
    directory: Annotated[Path, typer.Option("--directory", "-d", help="Source CSV directory (used to rebuild profiles for the loading job)")] = Path("."),
    env_file: Annotated[Path | None, typer.Option("--env-file", help="Path to .env with TG_HOST, TG_USERNAME, etc.")] = None,
    profile_name: Annotated[str | None, typer.Option("--profile", help="Named tigergraph-mcp connection profile (e.g. staging, prod)")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Print the MCP calls that would be made and exit")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Pass -vv to tigergraph-mcp")] = False,
    report_out: Annotated[Path, typer.Option("--report", help="Where to write the deploy report JSON")] = Path("deploy_report.json"),
) -> None:
    """Deploy a schema + load CSV into TigerGraph via the tigergraph-mcp MCP server.

    Reads connection details from environment / --env-file (TG_HOST, TG_USERNAME,
    TG_PASSWORD or TG_API_TOKEN, TG_GRAPHNAME). The MCP server is spawned as a stdio
    subprocess; the agent calls its tigergraph__* tools.
    """
    from tg_schema_agent.deploy import build_plan, deploy as run_deploy, render_dry_run, _load_env

    schema = io_utils.load_schema(schema_path)
    profiles = profile_directory(directory) if directory.exists() else []
    if not profiles:
        console.print(f"[red]No CSVs found in[/] {directory} — cannot build a loading job.")
        raise typer.Exit(code=2)

    plan = build_plan(schema, profiles, csv, graph_name=graph_name)

    if dry_run:
        env = _load_env(env_file)
        console.print(render_dry_run(plan, env))
        return

    console.rule(f"[bold cyan]Deploying {plan.graph_name} to TigerGraph via MCP")

    def _progress(msg: str) -> None:
        console.print(f"  • {msg}")

    report = asyncio.run(
        run_deploy(
            schema=schema,
            profiles=profiles,
            csv_path=csv,
            graph_name=graph_name,
            env_file=env_file,
            profile_name=profile_name,
            verbose=verbose,
            progress=_progress,
        )
    )
    io_utils.dump_json(report, report_out)
    console.rule("[bold green]Deploy report")
    if report.get("errors"):
        console.print(f"[red]Errors: {len(report['errors'])}[/]")
        for err in report["errors"][:5]:
            console.print(f"  - {err}")
    console.print("Vertex counts:")
    for vname, count in report.get("vertex_counts", {}).items():
        console.print(f"  {vname:14s} {count}")
    console.print(f"[green]Full report:[/] {report_out}")


if __name__ == "__main__":
    app()
