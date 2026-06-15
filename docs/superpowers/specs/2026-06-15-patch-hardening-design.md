# 补丁健壮性优化设计（P0 + P1）

- **日期**: 2026-06-15
- **范围**: 版本兼容性 / 可维护性 + 翻译覆盖率 / 质量 + 安装体验 / 可靠性
- **尺度**: 渐进式（增量改进，不破坏现有 patch 行为）
- **分支**: `optimize/patch-hardening`

## 背景与现状

Windows 版 Claude Desktop 中文补丁项目的翻译**内容覆盖已接近 100%**，主要债务在工程层：

1. **代码三重重复**：`find_claude_package` / `copy2_best_effort` / `write_text_best_effort` / `backup_file` / `CONFIG_PATH` 逻辑在 3 个主线脚本各写一遍。已存在的 `best_effort_io.py` 未被主线导入。
2. **chunk hash 硬编码**：`PATCHES["c71860c77-*.js"]` 等用 hash 前缀 glob，Claude 升级后内容变 → hash 全变 → glob 零匹配 → 补丁**静默失败**。
3. **补丁静默失败**：glob 零匹配或 `old` 未找到时无任何提示，用户无法判断汉化是否生效。
4. **缺 key 差异工具**：`check_i18n_coverage.py` 只做值自检，不对比官方 `en-US.json` 的 key。
5. **PATCHES 维护性差**：含大量 `old==new` 的 no-op 条目，混用 `\uXXXX` 转义与直接中文。
6. **无 dry-run / 版本指纹**：无法预览改动，无法追溯补丁针对的 Claude 版本。

## 设计决策（最保险取向）

- **M1 共享层**：扩展已有的 `best_effort_io.py`（测试已 `import` 它），不新建文件、不引入新导入路径。
- **M4 chunk 匹配**：**仅零匹配告警，不做自动全局降级搜索**——绝不在预期外的文件里自动替换，改为显眼警告提示维护者更新 hash。
- 每项改动**不改变任何已有 patch 行为**，纯增加可见性 / 复用 / 工具。
- 每步配回归测试，`python -m tools.test_patch_behaviors`（16 测试）守门，每步独立提交、可独立回退。

## 措施清单

### M1 — 抽取共享工具层（基础）

`best_effort_io.py` 升级为共享层，导出：

| 符号 | 说明 |
|------|------|
| `copy2_best_effort(src, dst, *, context, max_retries=3)` | 采用工作区已验证的 3 次重试 + psutil 进程检测版本 |
| `write_text_best_effort(path, text, *, context)` | 不变 |
| `find_claude_package() -> Path \| None` | Squirrel + WindowsApps 双路检测（从 3 脚本统一） |
| `resolve_config_path() -> Path` | `Claude-3p` → `Claude` 备选回退，返回最终 config 路径 |
| `backup_file(path, base_dir, *, backup_root)` | 统一签名；`base_dir` 为相对根，`backup_root` 决定备份目录 |
| `DRY_RUN` 模块级 flag | 为 M5 预留（默认 `False`） |

三个主线脚本改为 `from best_effort_io import ...`，删除本地重复定义。`best_effort_io` **不 import 任何主线脚本**，避免与 `restore → import patch_chunks` 形成循环依赖。

**约束**：原 `backup_file` 在不同脚本里相对根不同（json-only 脚本用 `app_resources`，chunks 脚本用 `assets_dir`）。统一函数通过参数 `base_dir` 承载差异，不强行统一语义。

### M2 + M4 — patch 可见性报告（零匹配告警）

`patch_chunks_zh_cn.main()` 结束时打印报告：

```
补丁报告:
  c71860c77-*.js   : 2 文件 / 18 替换 (0 未命中 old)
  cbc59a8af-*.js   : 0 文件  ⚠ 未匹配任何文件，该 chunk hash 可能已变化，请检查 PATCHES
  index-*.js       : 1 文件 / 31 替换 (2 未命中: ["Keep awake"])
```

- glob 零匹配 → 显眼 `⚠` 警告（解决静默失败）。
- 每个 `old` 在目标文件中未出现 → 计入"未命中"并提示（说明上游可能已改写该字符串）。
- **不改任何替换行为**，纯输出。

### M3 — `tools/diff_keys.py`

对比本地 `en-US.json` 与 `zh-CN.json`，输出三类报告到 `KEY-DIFF-REPORT.md`：
- `missing_in_zh`：en 有 zh 无 = 新增 key 待翻译
- `extra_in_zh`：zh 有 en 无 = 过期旧 key
- `untranslated`：值仍等于英文

`--en` 缺省时自动检测本地 `AnthropicClaude` / `WindowsApps` 的 `en-US.json`。独立工具，不碰现有代码。

### M5 — dry-run 模式

两个 patch 脚本加 `--dry-run`。通过 `best_effort_io.DRY_RUN` flag，在 dry-run 下 `write_text_best_effort` / `copy2_best_effort` / `backup_file` 变 no-op，但仍打印"会做什么"。默认 off，零侵入。

### M6 — 安装版本指纹

patch 完成后在 `%LOCALAPPDATA%\Claude-zh-CN\claude-zh-cn-version.txt` 写入：
- Claude 包名 / 版本（取自检测到的路径）
- 补丁日期
- 脚本版本 / git commit

restore 时读取并显示，便于排查"这个补丁是给哪个版本打的"。

### M7 — PATCHES 清理

- 移除所有 `old==new` 的 no-op 条目。**安全性证明**：
  - patch 应用：`if old in content and old != new` —— no-op 条目永不触发。
  - revert：`if old == new: continue` —— 本就跳过。
  - 故移除不改变任何行为。
- 给每个 PATCHES 分组补 UI 位置注释。

## 实施顺序（关键路径）

1. ~~建分支 + 提交现有 copy2 重试改动~~ ✅
2. 写设计 spec（本文档）
3. **M1 共享层** ← 其他项依赖
4. M2+M4 可见性报告
5. M7 PATCHES 清理（在 M2 报告基础上更易做）
6. M3 diff_keys 工具
7. M5 dry-run
8. M6 版本指纹

每步：实现 → 跑 `python -m tools.test_patch_behaviors` 守门 → 提交。

## 非目标（YAGNI）

- 不重写 patch 架构、不引入配置中心、不引入 CLI 框架（那是 P2 重构）。
- 不触碰翻译资源 JSON 内容本身（翻译覆盖已足够）。
- 不改动 PowerShell 脚本的核心逻辑（仅 M6 状态显示可能附带读取）。

## 测试策略

- 现有 16 个回归测试全程必须保持绿色。
- M1：新增测试验证 3 个脚本确实从 `best_effort_io` 复用（而非本地定义）。
- M2：新增测试验证零匹配时打印告警、报告含未命中统计。
- M3：新增测试用合成 JSON 验证三类分类。
- M5：新增测试验证 dry-run 不落盘。
- M6：新增测试验证指纹文件写入与读取。
