import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from backend.services.hashing import content_hash


class VaultPathError(Exception):
    pass


class VaultConflictError(Exception):
    pass


@dataclass
class TreeEntry:
    name: str
    path: str
    is_dir: bool
    children: Optional[list["TreeEntry"]] = None


def _resolve(root: Path, relative: str) -> Path:
    root_resolved = root.resolve()
    candidate = (root_resolved / relative).resolve()
    if candidate != root_resolved and root_resolved not in candidate.parents:
        raise VaultPathError(f"Pfad verlässt den Vault: {relative}")
    return candidate


def list_tree(root: Path) -> list[TreeEntry]:
    def _walk(dir_path: Path) -> list[TreeEntry]:
        entries = []
        for item in sorted(dir_path.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
            if item.name.startswith("."):
                continue
            rel = item.relative_to(root).as_posix()
            if item.is_dir():
                entries.append(TreeEntry(name=item.name, path=rel, is_dir=True, children=_walk(item)))
            else:
                entries.append(TreeEntry(name=item.name, path=rel, is_dir=False))
        return entries

    if not root.is_dir():
        return []
    return _walk(root)


def read_file(root: Path, relative: str) -> tuple[str, str]:
    path = _resolve(root, relative)
    if not path.is_file():
        raise FileNotFoundError(relative)
    content = path.read_text(encoding="utf-8")
    return content, content_hash(content)


def write_file(root: Path, relative: str, content: str, expected_hash: Optional[str]) -> str:
    path = _resolve(root, relative)
    if path.is_file() and expected_hash is not None:
        current = path.read_text(encoding="utf-8")
        if content_hash(current) != expected_hash:
            raise VaultConflictError(f"Datei wurde extern geändert: {relative}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return content_hash(content)


def rename(root: Path, source: str, target: str) -> None:
    src = _resolve(root, source)
    dst = _resolve(root, target)
    if not src.is_file():
        raise FileNotFoundError(source)
    if dst.exists():
        raise FileExistsError(target)
    dst.parent.mkdir(parents=True, exist_ok=True)
    src.rename(dst)


def delete(root: Path, relative: str) -> None:
    path = _resolve(root, relative)
    if not path.is_file():
        raise FileNotFoundError(relative)
    path.unlink()


def save_attachment(root: Path, filename: str, content: bytes) -> str:
    safe_name = Path(filename).name
    if safe_name in ("", ".", ".."):
        raise VaultPathError(f"Ungültiger Dateiname für Anhang: {filename}")
    dest_dir = root / "attachments"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / safe_name
    if dest.exists():
        digest = hashlib.sha256(content).hexdigest()[:8]
        dest = dest_dir / f"{dest.stem}-{digest}{dest.suffix}"
    dest.write_bytes(content)
    return dest.relative_to(root).as_posix()
