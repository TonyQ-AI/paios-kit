---
name: speckit-cn-init
description: "为当前项目初始化 SpecKit-CN（GitHub Spec-Kit 中文版，规格驱动开发）环境：从本机主副本 {{ZCODE_DIR}}/speckit-cn 复制中文模板与 PowerShell 脚本到项目根 .specify/，必要时 git init，并写入 AGENTS.md 流程约定。用户说'初始化规格环境'、'这个项目用 SpecKit-CN/中文规格流程'，或其他 speckit-cn-* 技能发现项目缺 .specify/ 时使用。"
---

# 初始化 SpecKit-CN 环境（中文版规格驱动开发）

主副本仓库：`{{ZCODE_DIR}}/speckit-cn`（clone 自 https://github.com/chameleon-nexus/speckit-cn ，commit 0db9fa5）。本技能把其中的 `claude/.specify/`（中文模板 + PowerShell 脚本 + 宪章模板）装入当前项目。

## 步骤

### 1. 前置检查

- 目标项目 = 当前工作目录；用户指定了其他目录则先切换过去。
- 记录项目根 `.specify/` 是否已存在（决定第 3 步宪章保护逻辑）。

### 2. 确保 git 仓库

- 不是 git 仓库 → `git init -q`（规格产物需要版本化与可回溯的提交历史）。

### 3. 复制 .specify/

用 Bash 工具执行（源路径纯 ASCII，用正斜杠避免转义问题）：

```bash
python -c "import shutil, os; shutil.copytree('{{ZCODE_DIR}}/speckit-cn/claude/.specify', os.path.join(os.getcwd(), '.specify'), dirs_exist_ok=True); print('copied')"
```

- 若复制前项目已有 `.specify/memory/constitution.md`：先复制一份备份（如 `constitution.md.bak-20260903`），copytree 覆盖后再恢复——用户宪章不被模板覆盖。
- 复制后把 `.specify/feature.json` 写进项目 `.gitignore`（文件不存在则创建；已有该行则跳过）：它记录「当前切片」，每次提需求都会被覆盖，属本地状态，不该进版本库。

### 4. 写入 AGENTS.md 流程约定

- 项目 `AGENTS.md` 已包含 "SpecKit-CN" 字样 → 跳过（保持幂等）。
- 未包含 → 用 Write/Edit 工具追加以下内容（文件不存在则创建，保留原有内容）：

```markdown
# 开发流程约定（SpecKit-CN）

本仓库启用 SpecKit-CN（中文规格驱动开发）流程，遵循以下约定：

- 新功能开发统一走完整流程：定规则 `$speckit-cn-constitution`（可选）→ 提需求 `$speckit-cn-specify` → 需求变更 `$speckit-cn-clarify`（可选）→ 定计划 `$speckit-cn-plan` → 分任务 `$speckit-cn-tasks` → 一致性分析 `$speckit-cn-analyze`（可选）→ 开始开发 `$speckit-cn-implement`
- 质量检查清单（需求的单元测试）用 `$speckit-cn-checklist`，产物存于 `specs/<编号>-<功能名>/checklists/`
- 规格/计划/任务产物统一存放在 `specs/<编号>-<功能名>/` 目录（spec.md / plan.md / tasks.md）
- 涉及规格驱动的开发一律走 speckit-cn-* 技能，不维护 docs/superpowers/ 规格产物
```

### 5. 验证

- `.specify/scripts/powershell/common.ps1` 与 `.specify/templates/spec-template.md` 存在；
- PowerShell 可用：`python {{ZCODE_DIR}}/scripts/silent.py powershell -NoProfile -Command "Write-Output ok"` 输出 `ok`。

### 6. 报告

- 已装入内容与验证结果；
- 用法：在该项目里直接说需求（如「提需求：做一个xxx」）或点名 `$speckit-cn-specify`；标准链路：定规则(可选) → 提需求 → 需求变更(可选) → 定计划 → 分任务 → 一致性分析(可选) → 开始开发；
- 更新方式：`git -C {{ZCODE_DIR}}/speckit-cn pull`（如需代理按本机代理配置设置 HTTPS_PROXY），拉完可在各项目重跑本技能刷新模板。

## 本机执行约定（所有 speckit-cn-* 技能通用）

1. 运行 `.specify/scripts/powershell/` 下任何 `.ps1` 一律套静默包装防控制台弹窗：`python {{ZCODE_DIR}}/scripts/silent.py powershell -NoProfile -ExecutionPolicy Bypass -File <脚本相对路径> [参数...]`
2. 产物文件（spec/plan/tasks/checklist）一律用 Write/Edit 工具写入，UTF-8 编码，正文中文。
3. 传给脚本的切片短名只用 ASCII（英文/拼音）——`create-new-feature.ps1` 会把非 ASCII 字符全部替换成 `-`，中文参数会生成残缺目录名。脚本**不建 git 分支**（全局约定 master 直做）：只创建 `specs/<NNN-短名>/` 与 spec.md，并把当前切片写入 `.specify/feature.json`（该文件是本地状态，建议加入项目 `.gitignore`）。
