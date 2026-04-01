from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from time import time

DEFAULT_LOG_RETENTION_DAYS = 3.0
DEFAULT_LOG_SUFFIXES = (".csv", ".json")
PROJECT_LOG_ROOT = Path("logs")


@dataclass(frozen=True)
class LogCleanupResult:
    scanned_count: int = 0
    deleted_count: int = 0
    protected_count: int = 0
    recent_count: int = 0
    error_count: int = 0


def _as_path(path_value: str | Path) -> Path:
    return path_value if isinstance(path_value, Path) else Path(path_value)


def _normalize_suffixes(suffixes: Iterable[str]) -> set[str]:
    normalized = set()
    for suffix in suffixes:
        cleaned = suffix.lower()
        if not cleaned.startswith("."):
            cleaned = f".{cleaned}"
        normalized.add(cleaned)
    return normalized


def _normalize_path(path_value: str | Path) -> Path:
    return _as_path(path_value).expanduser().resolve(strict=False)


def _iter_candidate_files(root: Path):
    if not root.exists():
        return
    if root.is_file():
        yield root
        return
    for candidate in root.rglob("*"):
        if candidate.is_file():
            yield candidate


def cleanup_old_logs(
    root_paths: Iterable[str | Path],
    protect_paths: Iterable[str | Path] = (),
    older_than_days: float = DEFAULT_LOG_RETENTION_DAYS,
    suffixes: Iterable[str] = DEFAULT_LOG_SUFFIXES,
) -> LogCleanupResult:
    cutoff_ts = time() - (max(older_than_days, 0.0) * 86400.0)
    allowed_suffixes = _normalize_suffixes(suffixes)
    protected = {_normalize_path(path_value) for path_value in protect_paths}

    scanned_count = 0
    deleted_count = 0
    protected_count = 0
    recent_count = 0
    error_count = 0
    seen_roots: set[Path] = set()
    seen_files: set[Path] = set()

    for root_value in root_paths:
        root = _normalize_path(root_value)
        if root in seen_roots:
            continue
        seen_roots.add(root)

        for candidate in _iter_candidate_files(root):
            try:
                resolved_candidate = candidate.resolve(strict=False)
            except OSError:
                error_count += 1
                continue

            if resolved_candidate in seen_files:
                continue
            seen_files.add(resolved_candidate)

            if candidate.suffix.lower() not in allowed_suffixes:
                continue

            scanned_count += 1
            if resolved_candidate in protected:
                protected_count += 1
                continue

            try:
                modified_ts = candidate.stat().st_mtime
            except OSError:
                error_count += 1
                continue

            if modified_ts > cutoff_ts:
                recent_count += 1
                continue

            try:
                candidate.unlink()
                deleted_count += 1
            except OSError:
                error_count += 1

    return LogCleanupResult(
        scanned_count=scanned_count,
        deleted_count=deleted_count,
        protected_count=protected_count,
        recent_count=recent_count,
        error_count=error_count,
    )


def cleanup_logs_for_run(
    current_paths: Iterable[str | Path],
    extra_roots: Iterable[str | Path] = (),
    older_than_days: float = DEFAULT_LOG_RETENTION_DAYS,
) -> LogCleanupResult:
    current_path_list = [_as_path(path_value) for path_value in current_paths]
    roots = [PROJECT_LOG_ROOT, *extra_roots, *(path.parent for path in current_path_list)]
    return cleanup_old_logs(
        root_paths=roots,
        protect_paths=current_path_list,
        older_than_days=older_than_days,
    )


def format_cleanup_result(result: LogCleanupResult) -> str:
    return (
        f"scanned {result.scanned_count} | deleted {result.deleted_count} | "
        f"protected {result.protected_count} | recent {result.recent_count} | errors {result.error_count}"
    )
