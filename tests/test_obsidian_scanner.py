from pathlib import Path

import pytest

from app.obsidian.scanner import ObsidianVaultScanner, VaultConfigurationError


def make_scanner(root: Path, **kwargs) -> ObsidianVaultScanner:
    return ObsidianVaultScanner(
        root,
        include_patterns=("*.md", "**/*.md"),
        exclude_dirs=(".obsidian", "Archive", "Attachments"),
        exclude_paths=("Learning/_drafts",),
        max_file_bytes=kwargs.pop("max_file_bytes", 1024),
        **kwargs,
    )


def test_scanner_finds_root_markdown(tmp_path):
    (tmp_path / "Note.md").write_text("hello", encoding="utf-8")
    report = make_scanner(tmp_path).scan()
    assert report.counts["new"] == 1
    assert report.items[0].relative_path == "Note.md"


def test_scanner_finds_nested_markdown_with_forward_slashes(tmp_path):
    path = tmp_path / "Folder" / "Note.md"
    path.parent.mkdir()
    path.write_text("hello", encoding="utf-8")
    item = next(item for item in make_scanner(tmp_path).scan().items if item.kind == "new")
    assert item.relative_path == "Folder/Note.md"
    assert "\\" not in item.relative_path


def test_scanner_ignores_non_markdown(tmp_path):
    (tmp_path / "image.png").write_bytes(b"png")
    assert make_scanner(tmp_path).scan().items == []


@pytest.mark.parametrize("directory", [".obsidian", "Archive", "Attachments"])
def test_scanner_excludes_configured_directories(tmp_path, directory):
    path = tmp_path / directory / "Note.md"
    path.parent.mkdir()
    path.write_text("ignored", encoding="utf-8")
    report = make_scanner(tmp_path).scan()
    assert report.counts["new"] == 0
    assert report.counts["skipped"] == 1


def test_scanner_excludes_configured_relative_prefix(tmp_path):
    path = tmp_path / "Learning" / "_drafts" / "Draft.md"
    path.parent.mkdir(parents=True)
    path.write_text("ignored", encoding="utf-8")
    report = make_scanner(tmp_path).scan()
    assert report.counts["new"] == 0
    assert report.counts["skipped"] == 1


def test_scanner_skips_file_over_size_limit(tmp_path):
    (tmp_path / "Large.md").write_text("x" * 20, encoding="utf-8")
    report = make_scanner(tmp_path, max_file_bytes=10).scan()
    assert report.items[0].reason == "file_too_large"


def test_scanner_reports_invalid_utf8_without_stopping_other_files(tmp_path):
    (tmp_path / "Bad.md").write_bytes(b"\xff")
    (tmp_path / "Good.md").write_text("good", encoding="utf-8")
    report = make_scanner(tmp_path).scan()
    assert report.counts["error"] == 1
    assert report.counts["new"] == 1


def test_scanner_accepts_utf8_bom(tmp_path):
    (tmp_path / "Bom.md").write_text("hello", encoding="utf-8-sig")
    assert make_scanner(tmp_path).scan().counts["new"] == 1


def test_scanner_classifies_unchanged_by_indexed_hash(tmp_path):
    (tmp_path / "Note.md").write_text("hello", encoding="utf-8")
    first = make_scanner(tmp_path).scan().items[0]
    second = make_scanner(tmp_path).scan({"Note.md": first.observed_hash})
    assert second.counts["unchanged"] == 1


def test_scanner_classifies_changed_by_indexed_hash(tmp_path):
    (tmp_path / "Note.md").write_text("hello", encoding="utf-8")
    report = make_scanner(tmp_path).scan({"Note.md": "old"})
    assert report.counts["changed"] == 1


def test_scanner_classifies_missing_database_note(tmp_path):
    report = make_scanner(tmp_path).scan({"Gone.md": {"relative_path": "Gone.md", "indexed_hash": "old"}})
    assert report.counts["missing"] == 1
    assert report.items[0].relative_path == "Gone.md"


def test_scanner_does_not_expose_absolute_path_in_public_item(tmp_path):
    (tmp_path / "Note.md").write_text("hello", encoding="utf-8")
    public = make_scanner(tmp_path).scan().items[0].public_dict()
    assert "absolute_path" not in public
    assert str(tmp_path) not in str(public)


def test_scanner_rejects_missing_vault(tmp_path):
    with pytest.raises(VaultConfigurationError):
        make_scanner(tmp_path / "missing")


def test_scanner_rejects_relative_vault_path():
    with pytest.raises(VaultConfigurationError):
        ObsidianVaultScanner(Path("relative"))


@pytest.mark.parametrize("unsafe", ["../outside.md", "C:/outside.md", "/outside.md"])
def test_resolve_note_path_rejects_unsafe_paths(tmp_path, unsafe):
    with pytest.raises(VaultConfigurationError):
        make_scanner(tmp_path).resolve_note_path(unsafe)


def test_scanner_skips_mocked_symlink(tmp_path, monkeypatch):
    path = tmp_path / "Linked.md"
    path.write_text("hello", encoding="utf-8")
    scanner = make_scanner(tmp_path)
    original = scanner._is_link_or_reparse
    monkeypatch.setattr(scanner, "_is_link_or_reparse", lambda candidate: candidate == path or original(candidate))
    report = scanner.scan()
    assert report.items[0].reason == "symlink_or_path_escape"
