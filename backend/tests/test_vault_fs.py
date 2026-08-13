import pytest

from backend.services.vault_fs import (
    VaultConflictError, VaultPathError, delete, list_tree, read_file, rename,
    save_attachment, write_file,
)


def test_write_and_read_file_roundtrip(tmp_path):
    write_file(tmp_path, "notiz.md", "# Hallo", expected_hash=None)

    content, digest = read_file(tmp_path, "notiz.md")

    assert content == "# Hallo"
    assert len(digest) == 64


def test_write_file_creates_parent_dirs(tmp_path):
    write_file(tmp_path, "ordner/unterordner/notiz.md", "Inhalt", expected_hash=None)

    assert (tmp_path / "ordner" / "unterordner" / "notiz.md").is_file()


def test_write_file_conflict_when_hash_mismatches(tmp_path):
    write_file(tmp_path, "notiz.md", "Original", expected_hash=None)

    with pytest.raises(VaultConflictError):
        write_file(tmp_path, "notiz.md", "Überschrieben", expected_hash="falscher-hash")


def test_write_file_succeeds_with_correct_expected_hash(tmp_path):
    write_file(tmp_path, "notiz.md", "Original", expected_hash=None)
    _, current_hash = read_file(tmp_path, "notiz.md")

    write_file(tmp_path, "notiz.md", "Geändert", expected_hash=current_hash)

    content, _ = read_file(tmp_path, "notiz.md")
    assert content == "Geändert"


def test_read_file_missing_raises_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_file(tmp_path, "gibtsnicht.md")


def test_read_file_rejects_path_traversal(tmp_path):
    with pytest.raises(VaultPathError):
        read_file(tmp_path, "../../etc/passwd")


def test_write_file_rejects_path_traversal(tmp_path):
    with pytest.raises(VaultPathError):
        write_file(tmp_path, "../escaped.md", "böse", expected_hash=None)


def test_rename_moves_file(tmp_path):
    write_file(tmp_path, "alt.md", "Inhalt", expected_hash=None)

    rename(tmp_path, "alt.md", "neu.md")

    assert not (tmp_path / "alt.md").exists()
    assert (tmp_path / "neu.md").is_file()


def test_rename_missing_source_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        rename(tmp_path, "gibtsnicht.md", "ziel.md")


def test_rename_existing_target_raises(tmp_path):
    write_file(tmp_path, "a.md", "A", expected_hash=None)
    write_file(tmp_path, "b.md", "B", expected_hash=None)

    with pytest.raises(FileExistsError):
        rename(tmp_path, "a.md", "b.md")


def test_delete_removes_file(tmp_path):
    write_file(tmp_path, "weg.md", "Inhalt", expected_hash=None)

    delete(tmp_path, "weg.md")

    assert not (tmp_path / "weg.md").exists()


def test_save_attachment_writes_under_attachments_dir(tmp_path):
    rel_path = save_attachment(tmp_path, "bild.png", b"\x89PNG")

    assert rel_path == "attachments/bild.png"
    assert (tmp_path / "attachments" / "bild.png").read_bytes() == b"\x89PNG"


def test_save_attachment_avoids_overwriting_existing_file(tmp_path):
    save_attachment(tmp_path, "bild.png", b"erste-version")

    second_path = save_attachment(tmp_path, "bild.png", b"zweite-version")

    assert second_path != "attachments/bild.png"
    assert (tmp_path / second_path).read_bytes() == b"zweite-version"
    assert (tmp_path / "attachments" / "bild.png").read_bytes() == b"erste-version"


def test_write_file_conflict_message_is_german(tmp_path):
    write_file(tmp_path, "notiz.md", "Original", expected_hash=None)

    with pytest.raises(VaultConflictError, match="extern geändert"):
        write_file(tmp_path, "notiz.md", "Neu", expected_hash="falscher-hash")


def test_save_attachment_rejects_dotdot_filename(tmp_path):
    with pytest.raises(VaultPathError):
        save_attachment(tmp_path, "..", b"inhalt")


def test_save_attachment_rejects_empty_filename(tmp_path):
    with pytest.raises(VaultPathError):
        save_attachment(tmp_path, "", b"inhalt")


def test_rename_source_rejects_path_traversal(tmp_path):
    with pytest.raises(VaultPathError):
        rename(tmp_path, "../escaped.md", "ziel.md")


def test_rename_target_rejects_path_traversal(tmp_path):
    write_file(tmp_path, "quelle.md", "Inhalt", expected_hash=None)

    with pytest.raises(VaultPathError):
        rename(tmp_path, "quelle.md", "../escaped.md")


def test_delete_rejects_path_traversal(tmp_path):
    with pytest.raises(VaultPathError):
        delete(tmp_path, "../escaped.md")


def test_list_tree_returns_nested_structure(tmp_path):
    (tmp_path / "ordner").mkdir()
    (tmp_path / "ordner" / "tief.md").write_text("x")
    (tmp_path / "oben.md").write_text("x")

    tree = list_tree(tmp_path)

    names = {entry.name for entry in tree}
    assert names == {"ordner", "oben.md"}
    folder = next(e for e in tree if e.name == "ordner")
    assert folder.is_dir is True
    assert folder.children is not None
    assert folder.children[0].name == "tief.md"


def test_list_tree_skips_hidden_entries(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".hidden.md").write_text("x")
    (tmp_path / "sichtbar.md").write_text("x")

    tree = list_tree(tmp_path)

    names = {entry.name for entry in tree}
    assert names == {"sichtbar.md"}
