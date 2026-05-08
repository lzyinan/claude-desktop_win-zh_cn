# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Windows 版 Claude Desktop 中文界面补丁项目。通过导出官方安装包的副本并注入 zh-CN 翻译资源、修补 JS bundle 中的硬编码英文标签来实现汉化。

## Commands

### Run Tests

```bash
# 主回归测试（无需管理员权限）
python tools/test_patch_behaviors.py

# 校验 JSON 资源合法性
python tools/validate_resources.py

# 检测疑似未翻译条目
python tools/check_i18n_coverage.py
```

### Install / Restore (需管理员 PowerShell)

```powershell
# 交互式菜单（推荐入口）
powershell -NoProfile -ExecutionPolicy Bypass -File .\claude-zh-cn.ps1

# 非交互安装（可指定 app 目录）
powershell -ExecutionPolicy Bypass -File .\install-windowsapps-json-only.ps1 -AppDir "D:\Claude\app"

# 非交互恢复
powershell -ExecutionPolicy Bypass -File .\restore-windowsapps-zh-cn.ps1 -AppDir "D:\Claude\app"
```

## Architecture: Two-Stage Patch Pipeline

Installation runs two Python scripts sequentially:

**Stage 1 — `patch_windowsapps_json_only.py`**: JSON resource layer
- Copies `resources/*.json` into the Claude app's resource directory
- Patches the language whitelist in `index-*.js` (regex match on locale arrays)
- Sets `locale=zh-CN` in `%APPDATA%\Claude-3p\config.json`

**Stage 2 — `patch_chunks_zh_cn.py`**: JS bundle string replacement
- Replaces hardcoded English UI labels in chunk files via exact string matching (`PATCHES` dict keyed by filename glob)
- Injects a font customization runtime (IIFE) into the entry bundle with `__CLAUDE_ZH_CN_FONT_PATCH_BEGIN/END__` markers
- Mirrors default font config to `%APPDATA%\Claude-3p\config.json` under `claudeZhCnFont`

Both stages back up originals to `%LOCALAPPDATA%\Claude-zh-CN-official-backup\{json-only,chunks}` before modifying.

## Key Design Patterns

**Best-effort I/O**: `copy2_best_effort()` / `write_text_best_effort()` — catch `PermissionError`, clear readonly bit, retry once. This pattern is duplicated in each script (not shared via import from `best_effort_io.py` in the mainline scripts).

**Chunk PATCHES dict** (`patch_chunks_zh_cn.py`): Maps filename glob patterns to `[(old, new)]` string replacement pairs. Entries where `old == new` are idempotent no-ops (already translated). When updating for a new Claude version, chunk filenames change and PATCHES keys must be updated to match.

**Restore script** (`restore_claude_zh_cn_windowsapps.py`): Imports `PATCHES` from `patch_chunks_zh_cn` to reverse translations. Also has a hardcoded `cleanup_known_chunk_residue_tokens` fallback for labels that survive stale backups.

## Resource Files

- `resources/desktop-zh-CN.json` — Desktop shell (menus, dialogs, tray, Cowork). Written to `app/resources/zh-CN.json`.
- `resources/frontend-zh-CN.json` — Web UI (chat, settings, billing, projects). Written to `app/resources/ion-dist/i18n/zh-CN.json`.
- `resources/statsig-zh-CN.json` — Statsig feature descriptions. Written to `app/resources/ion-dist/i18n/statsig/zh-CN.json`.

When Claude updates, compare new `en-US.json` keys against these files to find missing translations.

## Important Paths

- Claude app auto-detection: `C:\Program Files\WindowsApps\Claude_*_x64__*/app` (glob pattern matches newest)
- Install output: `C:\Users\<user>\AppData\Local\Claude-zh-CN`
- Backup root: `C:\Users\<user>\AppData\Local\Claude-zh-CN-official-backup`
- User config: `C:\Users\<user>\AppData\Roaming\Claude-3p\config.json`
- Font runtime uses `localStorage` key `claudeZhCnFont` in the browser, mirrored to the config file

## Test Architecture

`tools/test_patch_behaviors.py` uses `importlib.util` to dynamically load patch modules per test, avoiding cross-test state leakage. Tests create temp directories with fake Claude app structures, mock `LOCALAPPDATA`/`APPDATA` env vars, and verify patch/restore behavior including PermissionError retry logic. No admin access required.
