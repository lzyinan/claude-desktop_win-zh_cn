from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path


# Dry-run flag (M5). Read at call time via module attribute so toggling
# best_effort_io.DRY_RUN at runtime is honored (do NOT `from ... import DRY_RUN`,
# that snapshots the value). Default off — no behavior change for existing flows.
DRY_RUN: bool = False


def is_dry_run() -> bool:
    """Read the live DRY_RUN flag at call time (avoids import-snapshot staleness)."""
    return bool(DRY_RUN)


def copy2_best_effort(src: Path, dst: Path, *, context: str, max_retries: int = 3) -> bool:
    """Copy a file with multiple retries for Windows permission issues.

    In dry-run mode this does not touch the filesystem but reports the would-be action.
    """
    if is_dry_run():
        print(f"[dry-run] would copy {context}: {src} -> {dst}")
        return True

    import time

    last_error = None
    for attempt in range(max_retries):
        try:
            shutil.copy2(src, dst)
            return True
        except PermissionError as e:
            last_error = e
            if attempt < max_retries - 1:
                # Clear readonly bit before retry
                try:
                    if dst.exists():
                        dst.chmod(dst.stat().st_mode | stat.S_IWRITE)
                except OSError:
                    pass
                # Wait a bit before retry
                time.sleep(0.5 * (attempt + 1))
            continue
        except OSError as e:
            last_error = e
            break

    # All retries failed - provide helpful error message
    error_msg = (
        f"Warning: cannot copy {context} from {src} to {dst}: {last_error}; skipping"
    )

    # Check if Claude is running
    try:
        import psutil
        if any(p.name().lower() == "claude" for p in psutil.process_iter()):
            error_msg += "  Note: Claude Desktop is running. Please close it first."
    except ImportError:
        pass

    print(error_msg)
    return False


def write_text_best_effort(path: Path, text: str, *, context: str) -> bool:
    """Write text and degrade gracefully on Windows permission issues.

    In dry-run mode this does not touch the filesystem but reports the would-be action.
    """
    if is_dry_run():
        print(f"[dry-run] would write {context}: {path}")
        return True

    try:
        path.write_text(text, encoding="utf-8")
        return True
    except PermissionError:
        try:
            path.chmod(path.stat().st_mode | stat.S_IWRITE)
        except OSError:
            pass
        try:
            path.write_text(text, encoding="utf-8")
            return True
        except OSError as e:
            print(f"Warning: cannot write {context} at {path}: {e}; skipping")
            return False
    except OSError as e:
        print(f"Warning: cannot write {context} at {path}: {e}; skipping")
        return False


def find_claude_package() -> Path | None:
    """Auto-detect the installed Claude app directory.

    Supports both Squirrel (AnthropicClaude in LOCALAPPDATA) and Windows Store /
    MSIX (WindowsApps) installs. Returns the directory containing ``resources/``,
    or None if nothing is found.
    """
    # 1. Squirrel installer (AnthropicClaude in LocalAppData)
    squirrel_base = Path(os.environ.get("LOCALAPPDATA", "")) / "AnthropicClaude"
    if squirrel_base.exists():
        # Newer Squirrel versions: resources directly under app-*/
        candidates = sorted(squirrel_base.glob("app-*/resources/en-US.json"), reverse=True)
        if candidates:
            return candidates[0].parent.parent  # .../app-X.Y.Z
        # Older Squirrel versions: extra app/ subdirectory
        candidates = sorted(squirrel_base.glob("app-*/app/resources/en-US.json"), reverse=True)
        if candidates:
            return candidates[0].parent.parent  # .../app
    # 2. Windows Store / MSIX (WindowsApps)
    base = Path(r"C:\Program Files\WindowsApps")
    if base.exists():
        candidates = sorted(base.glob("Claude_*_x64__*/app/resources/en-US.json"), reverse=True)
        if candidates:
            return candidates[0].parent.parent  # .../app
    return None


def resolve_config_path() -> Path:
    """Resolve the user config path with Claude-3p -> Claude fallback.

    Mirrors the per-script logic previously duplicated across all three mainline
    scripts. Returns the preferred existing path, or the Claude-3p default if
    neither exists (callers should check .exists() before relying on it).
    """
    preferred = Path(os.environ["APPDATA"]) / "Claude-3p" / "config.json"
    alt = Path(os.environ["APPDATA"]) / "Claude" / "config.json"
    if not preferred.exists() and alt.exists():
        return alt
    return preferred


def backup_file(path: Path, base_dir: Path, *, backup_root: Path) -> None:
    """Back up ``path`` under ``backup_root`` preserving its path relative to ``base_dir``.

    ``base_dir`` carries the per-script difference (json-only uses app/resources,
    chunks uses the assets/v1 dir) so the same function serves both without forcing
    a single relative-root semantics. No-op in dry-run mode (still prints intent).
    """
    if not path.exists():
        return
    if is_dry_run():
        print(f"[dry-run] would back up {path.name} -> {backup_root}")
        return
    rel = path.relative_to(base_dir)
    dst = backup_root / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not dst.exists():
        copy2_best_effort(path, dst, context="backup file")
