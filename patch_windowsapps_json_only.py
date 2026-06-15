#!/usr/bin/env python3
"""Patch only JSON i18n resources in the official WindowsApps package.

Accepts --app-dir to specify the Claude app directory dynamically.
If not provided, auto-detects from C:\\Program Files\\WindowsApps.

Steps:
1. Backup original files
2. Copy zh-CN JSON resources into the official package
3. Patch the language whitelist in index-*.js to recognize zh-CN
4. Set locale=zh-CN in user config
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
from pathlib import Path


ROOT = Path(__file__).resolve().parent
RESOURCES = ROOT / "resources"
BACKUP_ROOT = Path(os.environ["LOCALAPPDATA"]) / "Claude-zh-CN-official-backup" / "json-only"
CONFIG_PATH = Path(os.environ["APPDATA"]) / "Claude-3p" / "config.json"
_CONFIG_ALT = Path(os.environ["APPDATA"]) / "Claude" / "config.json"
if not CONFIG_PATH.exists() and _CONFIG_ALT.exists():
    CONFIG_PATH = _CONFIG_ALT


def find_claude_package() -> Path | None:
    """Auto-detect Claude package — supports both Squirrel and WindowsApps installs."""
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


def backup_file(path: Path, app_resources: Path) -> None:
    if not path.exists():
        return
    rel = path.relative_to(app_resources)
    dst = BACKUP_ROOT / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not dst.exists():
        copy2_best_effort(path, dst, context="backup file")


def copy2_best_effort(src: Path, dst: Path, *, context: str, max_retries: int = 3) -> bool:
    """Copy a file with multiple retries for Windows permission issues."""
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
        f"Warning: cannot copy {context} from {src} to {dst}: {last_error}\n"
        f"  The WindowsApps directory is protected by Windows.\n"
        f"  Possible causes:\n"
        f"  1. Claude Desktop is currently running\n"
        f"  2. The file is locked by another process\n"
        f"  3. File permissions are not set correctly\n"
        f"  4. You need to run the script as Administrator\n"
    )

    # Check if Claude is running
    try:
        import psutil
        if any(p.name().lower() == "claude" for p in psutil.process_iter()):
            error_msg += f"  Current action: Please close Claude Desktop first, then try again.\n"
    except ImportError:
        pass

    print(error_msg)
    return False


def write_text_best_effort(path: Path, text: str, *, context: str) -> bool:
    """Write text and degrade gracefully on Windows permission issues."""
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


def patch_whitelist(app_resources: Path) -> list[str]:
    """Add zh-CN to the language whitelist in ALL matching bundles."""
    assets_dir = app_resources / "ion-dist" / "assets" / "v1"
    candidates = sorted(assets_dir.glob("index-*.js"))
    if not candidates:
        print("Warning: no index-*.js found; skipping whitelist patch")
        return []

    pattern = re.compile(r'(\["en-US"(?:,"[a-zA-Z]{2,3}(?:-[a-zA-Z0-9]{2,4})*")+)\]')
    patched = []
    for path in candidates:
        text = path.read_text(encoding="utf-8")

        # Backup before modifying
        backup_file(path, app_resources)

        if '"zh-CN"' in text:
            patched.append(path.name + " (already)")
            continue

        m = pattern.search(text)
        if m:
            original_array = m.group(0)
            patched_array = original_array[:-1] + ',"zh-CN"]'
            text = text.replace(original_array, patched_array, 1)
            if write_text_best_effort(path, text, context="whitelist patch"):
                patched.append(path.name)

    if not patched:
        print("Warning: whitelist pattern not found in any index bundle")
    return patched


def set_locale() -> bool:
    """Set locale=zh-CN in user config."""
    if not CONFIG_PATH.exists():
        print(f"Warning: config not found at {CONFIG_PATH}; skipping locale")
        return False

    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"Warning: cannot parse config: {e}; skipping locale")
        return False

    if data.get("locale") == "zh-CN":
        return True

    data["locale"] = "zh-CN"
    return write_text_best_effort(
        CONFIG_PATH,
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        context="locale config",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Patch Claude Desktop with zh-CN resources")
    parser.add_argument("--app-dir", type=str, default=None,
                        help="Path to Claude app directory (auto-detected if omitted)")
    args = parser.parse_args()

    if args.app_dir:
        app_dir = Path(args.app_dir)
        # If user pointed at the AnthropicClaude root, auto-find latest app-*/ subdirectory
        if not (app_dir / "resources").exists():
            candidates = sorted(app_dir.glob("app-*/resources/en-US.json"), reverse=True)
            if candidates:
                app_dir = candidates[0].parent.parent
                print(f"Auto-detected app version: {app_dir.name}")
            else:
                raise SystemExit(f"No app-*/resources/en-US.json found under {app_dir}")
    else:
        app_dir = find_claude_package()

    if not app_dir or not app_dir.exists():
        raise SystemExit("Claude app directory not found. Use --app-dir to specify manually.")

    app_resources = app_dir / "resources"
    if not app_resources.exists():
        raise SystemExit(f"App resources not found: {app_resources}")

    files = [
        (RESOURCES / "desktop-zh-CN.json", app_resources / "zh-CN.json"),
        (RESOURCES / "frontend-zh-CN.json", app_resources / "ion-dist" / "i18n" / "zh-CN.json"),
        (RESOURCES / "statsig-zh-CN.json", app_resources / "ion-dist" / "i18n" / "statsig" / "zh-CN.json"),
    ]

    BACKUP_ROOT.mkdir(parents=True, exist_ok=True)

    # Step 1: Copy JSON resources
    copied = 0
    for src, dst in files:
        if not src.exists():
            raise SystemExit(f"Missing source resource: {src}")
        backup_file(dst, app_resources)
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not copy2_best_effort(src, dst, context="json resource"):
            raise SystemExit(f"Failed to copy json resource: {src} -> {dst}")
        copied += 1

    # Step 2: Patch whitelist
    wl_files = patch_whitelist(app_resources)

    # Step 3: Set locale
    locale_set = set_locale()

    print("Done")
    print(f"App dir: {app_dir}")
    print(f"Copied json resources: {copied}")
    print(f"Whitelist patched: {', '.join(wl_files) if wl_files else 'skipped'}")
    print(f"Locale set: {locale_set}")
    print(f"Backup root: {BACKUP_ROOT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
