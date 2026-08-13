import re
from pathlib import Path

from backend.models.nodes import Node

_SLUG_RE = re.compile(r"[^a-z0-9äöüß]+")


def _slug(title: str) -> str:
    slug = _SLUG_RE.sub("-", title.lower()).strip("-")
    return slug[:60] or "notiz"


def export_nodes(nodes: list[Node], ziel: Path) -> int:
    """Schreibt alle Nodes mit content als Markdown-Dateien (Frontmatter + Body).

    Dateiname = Titel-Slug + Node-ID-Präfix, damit ein erneuter Export dieselbe
    Datei überschreibt statt Duplikate anzulegen. Die Frontmatter-Keys
    (title/created/updated) sind genau die, die parse_frontmatter beim
    Re-Import als Node-Felder übernimmt.
    """
    ziel.mkdir(parents=True, exist_ok=True)
    anzahl = 0
    for node in nodes:
        if not node.content:
            continue
        zeilen = [
            "---",
            f"id: {node.id}",
            f"title: {node.title}",
            f"type: {node.type}",
            f"created: {node.creation_time}",
        ]
        updated = node.metadata.get("updated")
        if updated:
            zeilen.append(f"updated: {updated}")
        zeilen.append("---")
        pfad = ziel / f"{_slug(node.title)}-{node.id[:8]}.md"
        pfad.write_text("\n".join(zeilen) + "\n\n" + node.content + "\n", encoding="utf-8")
        anzahl += 1
    return anzahl
