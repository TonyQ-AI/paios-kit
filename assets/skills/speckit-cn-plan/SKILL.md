---
name: speckit-cn-plan
description: "定计划：AI 架构师生成中文技术实现计划与设计文档（research.md、data-model.md、contracts/、quickstart.md），未指定技术栈时自动推荐。对应 SpecKit-CN 命令 /定计划。触发词：定计划、技术方案、实现计划、plan。需项目已有 spec.md（缺则先跑 $speckit-cn-specify）。"
---

> **已有权威文档时的处理（文档纳入模式）**：若用户提供了「现成需求 / 架构 / API / 测试」文档，把它们当作**输入原料**——本步骤将文档中**已声明**的内容逐条**映射**进本产物，缺口标「（待定义）」，**绝不凭空发明**；本产物生成后即成为**唯一权威信源**，原始文档仅作参考，不得作为直接写代码的依据。

> 适配自 [SpecKit-CN](https://github.com/chameleon-nexus/speckit-cn)（commit 0db9fa5）的 `claude/.claude/commands/定计划.md`；主副本在 `{{ZCODE_DIR}}/speckit-cn`。

## 用户输入

用户输入 = 触发消息中技能名之后的文字（上游 `$ARGUMENTS`），可包含指定技术栈（如"使用 Python FastAPI + PostgreSQL + React"）。非空时必须先考虑。

## 本机执行约定

- 运行 `.ps1` 一律套静默包装：`python {{ZCODE_DIR}}/scripts/silent.py powershell -NoProfile -ExecutionPolicy Bypass -File <脚本> [参数...]`
- 项目根缺 `.specify/` → 先执行 `$speckit-cn-init` 流程再继续。
- 产物用 Write/Edit 工具写入，UTF-8，**正文一律中文**。

## Outline

1. **Setup**：在项目根运行 `.specify/scripts/powershell/setup-plan.ps1 -Json`（只运行一次），解析 JSON 取 `FEATURE_SPEC`、`IMPL_PLAN`、`SPECS_DIR`、`BRANCH`。经 silent.py 以参数列表传参，无引号转义问题。

2. **加载上下文**：读取 `FEATURE_SPEC` 与 `.specify/memory/constitution.md`；读取 IMPL_PLAN 模板（脚本已复制到位）。

3. **确定技术栈**：
   * 用户输入指定了技术栈 → 按指定执行。
   * 未指定 → 分析 `FEATURE_SPEC` 核心需求（Web 应用？CLI 工具？需要数据库吗？），**主动推荐合适的现代技术栈**，并明确告知用户你的选择与理由（例："未指定技术栈，基于规格推荐：Python + FastAPI 后端 + SQLite 存储，轻量且够用"）。后续步骤全部使用该自拟技术栈。

4. **执行计划工作流**：按 IMPL_PLAN 模板结构：
   - 填技术上下文（未知项标 "NEEDS CLARIFICATION"）
   - 从宪章填 Constitution Check
   - 评估门禁（违规且无正当理由 → ERROR）
   - Phase 0：生成 research.md（消解所有 NEEDS CLARIFICATION）
   - Phase 1：生成 data-model.md、contracts/、quickstart.md
   - Phase 1：运行 agent 上下文更新脚本
   - 设计完成后复评 Constitution Check

5. **停止并报告**：本技能在 Phase 2 规划完成后结束。报告分支、IMPL_PLAN 路径与生成的产物。

## Phases

### Phase 0: Outline & Research

1. 从上面的技术上下文提取未知项：
   - 每个 NEEDS CLARIFICATION → 研究任务
   - 每个依赖 → 最佳实践任务
   - 每个集成 → 模式任务

2. 逐项研究（可派发子代理并行）：
   ```
   对技术上下文中每个未知项：
     任务: "为{功能上下文}研究{未知项}"
   对每个技术选型：
     任务: "寻找{技术}在{领域}的最佳实践"
   ```

3. 汇总到 `research.md`，每项格式：
   - 决策：[选了什么]
   - 理由：[为什么]
   - 已考虑的备选：[评估过什么]

**输出**: research.md，消解所有"需要澄清"项。**重要：全部中文**。

### Phase 1: Design & Contracts

**前置**: research.md 完成

1. 从功能规格提取实体 → `data-model.md`：
   - 实体名、字段、关系
   - 来自需求的校验规则
   - 状态迁移（如适用）

2. 从功能需求生成 API 契约：
   - 每个用户动作 → 端点
   - 用标准 REST/GraphQL 模式
   - OpenAPI/GraphQL schema 输出到 `contracts/`

3. Agent 上下文更新：
   - 运行 `.specify/scripts/powershell/update-agent-context.ps1 -AgentType codex`
   - **本机适配说明**：上游用 `-AgentType claude`（写 CLAUDE.md），对 ZCode 无效；`codex` 类型写入 `AGENTS.md`——正是 ZCode 读取的项目上下文文件。脚本只新增当前计划涉及的技术栈，保留标记之间的手工内容。

**输出**: data-model.md、contracts/*、quickstart.md、AGENTS.md 技术栈段落。**重要：全部中文**。

## Key rules

- 使用绝对路径
- 门禁失败或澄清未消解 → ERROR
