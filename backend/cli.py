import asyncio
import subprocess
from pathlib import Path

import typer

from backend.app_context import build_context
from backend.logging_setup import setup_logging
from backend.services import rag as rag_service
from backend.services import rebuild as rebuild_service
from backend.services import vault_index
from backend.services.agents.pineal import find_contradictions
from backend.services.ingestion import ingest_file, ingest_note
from backend.services.vault_export import export_nodes

app = typer.Typer()

setup_logging()


@app.command()
def add(datei: str) -> None:
    ctx = build_context()
    result = ingest_file(Path(datei), ctx.event_log, vlm_client=ctx.vlm_client)
    if result.duplicate:
        typer.echo(f"Bereits bekannt seit {result.duplicate_since}")
        return
    summary = asyncio.run(ctx.dispatcher.process_pending())
    ctx.faiss_index.save()
    typer.echo(f"Verarbeitet: {summary.processed}, Fehlgeschlagen: {summary.failed}")


@app.command()
def note(text: str) -> None:
    ctx = build_context()
    result = ingest_note(text, ctx.event_log)
    if result.duplicate:
        typer.echo(f"Bereits bekannt seit {result.duplicate_since}")
        return
    summary = asyncio.run(ctx.dispatcher.process_pending())
    ctx.faiss_index.save()
    typer.echo(f"Verarbeitet: {summary.processed}, Fehlgeschlagen: {summary.failed}")


@app.command()
def ask(frage: str) -> None:
    ctx = build_context()
    result = asyncio.run(rag_service.ask(
        frage, ctx.temporal_agent, ctx.faiss_index, ctx.graph, ctx.prefrontal_agent
    ))
    typer.echo(result.answer)
    typer.echo("Quellen: " + ", ".join(result.sources))


@app.command()
def search(begriff: str) -> None:
    ctx = build_context()
    hits = asyncio.run(rag_service.search(begriff, ctx.temporal_agent, ctx.faiss_index, ctx.graph, limit=10))
    for hit in hits:
        typer.echo(f"{hit.node.title} (score={hit.score:.3f})")


@app.command()
def show(node_id: str) -> None:
    ctx = build_context()
    node = ctx.graph.get_node(node_id)
    if node is None:
        typer.echo("Node nicht gefunden")
        raise typer.Exit(code=1)
    typer.echo(f"{node.title} ({node.type})")
    for neighbor in ctx.graph.get_neighbors(node_id):
        typer.echo(f"  - {neighbor.title} ({neighbor.type})")


@app.command()
def stats() -> None:
    ctx = build_context()
    event_counts = ctx.event_log.counts()
    graph_counts = ctx.graph.counts()
    typer.echo(f"Events: {event_counts}")
    typer.echo(f"Graph: {graph_counts}")


@app.command()
def retry() -> None:
    ctx = build_context()
    summary = asyncio.run(ctx.dispatcher.process_events(ctx.event_log.failed()))
    ctx.faiss_index.save()
    typer.echo(f"Erneut verarbeitet: {summary.processed}, weiterhin fehlgeschlagen: {summary.failed}")


@app.command(name="index")
def index_vault(
    full: bool = typer.Option(False, "--full", help="Alle Dateien neu indexieren, auch unveränderte"),
) -> None:
    ctx = build_context()
    if ctx.config.vault_dir is None:
        typer.echo("CBKS_VAULT_DIR ist nicht gesetzt")
        raise typer.Exit(code=1)
    summary = asyncio.run(vault_index.rescan(ctx.config.vault_dir, ctx, full=full))
    typer.echo(
        f"Verarbeitet: {summary.processed}, Übersprungen: {summary.skipped}, "
        f"Fehlgeschlagen: {summary.failed}, Gelöscht: {summary.deleted}"
    )


@app.command()
def rebuild() -> None:
    ctx = build_context()
    summary = asyncio.run(rebuild_service.rebuild(ctx.event_log, ctx.graph, ctx.faiss_index, ctx.dispatcher))
    ctx.faiss_index.save()
    typer.echo(f"Rebuild abgeschlossen: {summary.processed} verarbeitet, {summary.failed} fehlgeschlagen")


@app.command()
def dedupe() -> None:
    ctx = build_context()
    summary = asyncio.run(ctx.entity_resolver.dedupe_all())
    typer.echo(f"Geprüft: {summary.checked}, Zusammengeführt: {summary.merged}")


@app.command()
def contradictions() -> None:
    ctx = build_context()
    summary = asyncio.run(find_contradictions(ctx.graph, ctx.llm_client))
    typer.echo(f"Geprüft: {summary.checked}, Widersprüche: {summary.found}")


@app.command()
def delete(node_id: str) -> None:
    ctx = build_context()
    node = ctx.graph.get_node(node_id)
    if node is None:
        typer.echo("Node nicht gefunden")
        raise typer.Exit(code=1)
    faiss_id = ctx.graph.delete_node(node_id)
    if faiss_id is not None:
        ctx.faiss_index.remove(faiss_id)
        ctx.faiss_index.save()
        ctx.event_log.delete(faiss_id)
        typer.echo(f"Node gelöscht: {node_id} (Event {faiss_id} entfernt)")
    else:
        typer.echo(f"Node gelöscht: {node_id}")


@app.command()
def export(zielordner: str) -> None:
    ctx = build_context()
    anzahl = export_nodes(ctx.graph.get_all_nodes(), Path(zielordner))
    typer.echo(f"{anzahl} Notizen exportiert nach {zielordner}")


@app.command()
def backup() -> None:
    ctx = build_context()
    subprocess.run([str(ctx.config.backup_script_path)], check=True)
    typer.echo("Backup abgeschlossen")


if __name__ == "__main__":
    app()
