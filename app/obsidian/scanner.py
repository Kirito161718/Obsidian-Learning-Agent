from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.obsidian.models import ScanItem, ScanReport, VaultFile


class VaultConfigurationError(ValueError):
    pass


class ObsidianVaultScanner:
    """Discover Markdown files without writing to the Vault."""

    def __init__(
        self,
        vault_path: Path | None,
        *,
        include_patterns: tuple[str, ...] = ("*.md", "**/*.md"),
        exclude_dirs: tuple[str, ...] = (),
        exclude_paths: tuple[str, ...] = (),
        max_file_bytes: int = 2_097_152,
    ) -> None:
        if vault_path is None:
            raise VaultConfigurationError("OBSIDIAN_VAULT_PATH is not configured")
        if not vault_path.is_absolute():
            raise VaultConfigurationError("OBSIDIAN_VAULT_PATH must be an absolute path")
        self.root = vault_path.resolve(strict=False)
        if not self.root.exists() or not self.root.is_dir():
            raise VaultConfigurationError("Configured Obsidian Vault does not exist or is not a directory")
        if self._is_link_or_reparse(self.root):
            raise VaultConfigurationError("Configured Obsidian Vault root must not be a symbolic link")
        self.include_patterns = include_patterns or ("*.md", "**/*.md")
        self.exclude_dirs = {value.casefold() for value in exclude_dirs}
        self.exclude_paths = tuple(value.replace("\\", "/").strip("/").casefold() for value in exclude_paths)
        self.max_file_bytes = max(1, max_file_bytes)

    def scan(self, existing: Mapping[str, Any] | None = None) -> ScanReport:
        existing = existing or {}
        existing_by_key = {self._key(path): value for path, value in existing.items()}
        seen: set[str] = set()
        items: list[ScanItem] = []

        def on_walk_error(exc: OSError) -> None:
            name = Path(exc.filename or "unknown").name
            items.append(ScanItem(kind="error", relative_path=name, reason=type(exc).__name__))

        for directory, dir_names, file_names in os.walk(self.root, topdown=True, followlinks=False, onerror=on_walk_error):
            directory_path = Path(directory)
            safe_dirs: list[str] = []
            for name in sorted(dir_names, key=str.casefold):
                child = directory_path / name
                relative = self._relative(child)
                if self._excluded(relative, is_dir=True) or self._is_hidden(child) or self._is_link_or_reparse(child):
                    items.append(ScanItem(kind="skipped", relative_path=relative, reason="excluded_or_unsafe_directory"))
                else:
                    safe_dirs.append(name)
            dir_names[:] = safe_dirs

            for name in sorted(file_names, key=str.casefold):
                path = directory_path / name
                relative = self._relative(path)
                key = self._key(relative)
                if path.suffix.casefold() != ".md" or not self._included(relative):
                    continue
                seen.add(key)
                if self._excluded(relative, is_dir=False) or self._is_hidden(path):
                    items.append(ScanItem(kind="skipped", relative_path=relative, reason="excluded_or_hidden"))
                    continue
                if self._is_link_or_reparse(path) or not self._within_root(path):
                    items.append(ScanItem(kind="skipped", relative_path=relative, reason="symlink_or_path_escape"))
                    continue
                try:
                    stat = path.stat()
                    if stat.st_size > self.max_file_bytes:
                        items.append(ScanItem(kind="skipped", relative_path=relative, reason="file_too_large"))
                        continue
                    raw = path.read_bytes()
                    raw.decode("utf-8-sig")
                    observed_hash = hashlib.sha256(raw).hexdigest()
                    indexed_hash = self._indexed_hash(existing_by_key.get(key))
                    kind = "new" if key not in existing_by_key else ("unchanged" if indexed_hash == observed_hash else "changed")
                    vault_file = VaultFile(
                        relative_path=relative,
                        absolute_path=path,
                        file_name=name,
                        size_bytes=stat.st_size,
                        modified_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
                        observed_hash=observed_hash,
                    )
                    items.append(
                        ScanItem(
                            kind=kind,
                            relative_path=relative,
                            observed_hash=observed_hash,
                            indexed_hash=indexed_hash,
                            vault_file=vault_file,
                        )
                    )
                except (OSError, UnicodeError) as exc:
                    items.append(ScanItem(kind="error", relative_path=relative, reason=type(exc).__name__))

        for key, value in existing_by_key.items():
            if key in seen:
                continue
            relative = self._existing_path(value, fallback=key)
            items.append(
                ScanItem(
                    kind="missing",
                    relative_path=relative,
                    reason="file_not_found",
                    indexed_hash=self._indexed_hash(value),
                )
            )
        items.sort(key=lambda item: (item.relative_path.casefold(), item.kind))
        return ScanReport(items=items)

    def resolve_note_path(self, relative_path: str) -> Path:
        normalized = relative_path.replace("\\", "/").strip("/")
        parts = Path(normalized).parts
        if not normalized or Path(normalized).is_absolute() or ".." in parts:
            raise VaultConfigurationError("Note path must be a safe Vault-relative path")
        candidate = self.root.joinpath(*parts)
        if not self._within_root(candidate) or self._is_link_or_reparse(candidate):
            raise VaultConfigurationError("Note path escapes the configured Vault")
        return candidate

    def _relative(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    def _within_root(self, path: Path) -> bool:
        try:
            path.resolve(strict=False).relative_to(self.root)
            return True
        except (OSError, ValueError):
            return False

    def _included(self, relative: str) -> bool:
        path = Path(relative)
        return any(path.match(pattern) for pattern in self.include_patterns)

    def _excluded(self, relative: str, *, is_dir: bool) -> bool:
        normalized = relative.replace("\\", "/").strip("/")
        parts = normalized.split("/") if normalized else []
        directory_parts = parts if is_dir else parts[:-1]
        if any(part.casefold() in self.exclude_dirs or part.startswith(".") for part in directory_parts):
            return True
        key = normalized.casefold()
        return any(key == prefix or key.startswith(f"{prefix}/") for prefix in self.exclude_paths)

    @staticmethod
    def _is_hidden(path: Path) -> bool:
        if path.name.startswith("."):
            return True
        try:
            attributes = getattr(path.stat(follow_symlinks=False), "st_file_attributes", 0)
            hidden = getattr(stat, "FILE_ATTRIBUTE_HIDDEN", 0x2)
            system = getattr(stat, "FILE_ATTRIBUTE_SYSTEM", 0x4)
            return bool(attributes & (hidden | system))
        except OSError:
            return True

    @staticmethod
    def _is_link_or_reparse(path: Path) -> bool:
        try:
            if path.is_symlink():
                return True
            attributes = getattr(path.stat(follow_symlinks=False), "st_file_attributes", 0)
            return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
        except OSError:
            return True

    @staticmethod
    def _key(relative_path: str) -> str:
        return relative_path.replace("\\", "/").strip("/").casefold()

    @staticmethod
    def _indexed_hash(value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            return value or None
        if isinstance(value, Mapping):
            return value.get("indexed_hash")
        return getattr(value, "indexed_hash", None)

    @staticmethod
    def _existing_path(value: Any, *, fallback: str) -> str:
        if isinstance(value, Mapping):
            return str(value.get("relative_path") or fallback).replace("\\", "/")
        return str(getattr(value, "relative_path", fallback)).replace("\\", "/")
