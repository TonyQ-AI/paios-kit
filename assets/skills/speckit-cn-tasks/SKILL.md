---
name: speckit-cn-tasks
description: "分任务：把计划分解为按用户故事组织、可并行执行标记的中文任务清单 tasks.md（T001 编号 + 依赖图）。对应 SpecKit-CN 命令 /分任务。触发词：分任务、任务分解、生成任务清单、tasks。需项目已有 plan.md 与 spec.md（缺则先跑 $speckit-cn-plan）。"
---

> **已有权威文档时的处理（文档纳入模式）**：若用户提供了「现成需求 / 架构 / API / 测试」文档，把它们当作**输入原料**——本步骤将文档中**已声明**的内容逐条**映射**进本产物，缺口标「（待定义）」，**绝不凭空发明**；本产物生成后即成为**唯一权威信源**，原始文档仅作参考，不得作为直接写代码的依据。

> 适配自 [SpecKit-CN](https://github.com/chameleon-nexus/speckit-cn)（commit 0db9fa5）的 `claude/.claude/commands/分任务.md`；主副本在 `{{ZCODE_DIR}}/speckit-cn`。

## 用户输入

用户输入 = 触发消息中技能名之后的文字（上游 `$ARGUMENTS`）。非空时必须先考虑（如指定只分解某几个用户故事）。

## 本机执行约定

- 运行 `.ps1` 一律套静默包装：`python {{ZCODE_DIR}}/scripts/silent.py powershell -NoProfile -ExecutionPolicy Bypass -File <脚本> [参数...]`
- 项目根缺 `.specify/` → 先执行 `$speckit-cn-init` 流程再继续。
- tasks.md 用 Write/Edit 工具写入，UTF-8，**全部任务描述中文**。

## Outline

1. **Setup**：在项目根运行 `.specify/scripts/powershell/check-prerequisites.ps1 -Json`（只运行一次），解析 `FEATURE_DIR` 与 `AVAILABLE_DOCS` 列表。路径须为绝对路径。

2. **加载设计文档**：从 `FEATURE_DIR` 读取：
   - **必需**: plan.md（技术栈、库、结构）、spec.md（带优先级的用户故事）
   - **可选**: data-model.md（实体）、contracts/（API 端点）、research.md（决策）、quickstart.md（测试场景）
   - 不是所有项目都有全部文档——按实际存在的生成分解。

3. **执行任务生成工作流**（遵循模板结构）：
   - 读 plan.md 提取技术栈、库、项目结构
   - **读 spec.md 提取带优先级的用户故事（P1、P2、P3…）**
   - 有 data-model.md：提取实体 → 映射到用户故事
   - 有 contracts/：每个文件 → 端点映射到用户故事
   - 有 research.md：提取决策 → 生成 setup 任务
   - **按用户故事组织生成任务**：
     - Setup 任务（所有故事共享的基础设施）
     - **Foundational 任务（任何用户故事开工前必须完成的前置）**
     - 每个用户故事（按 P1、P2、P3 优先级）：
       - 归组完成**该故事**所需的全部任务
       - 包含该故事专属的模型、服务、端点、UI 组件
       - 标注可并行任务 [P]
       - 若要求测试：包含该故事专属的测试
     - Polish/Integration 任务（横切关注点）
   - **测试可选**：仅当功能规格明确要求或用户点名 TDD 时才生成测试任务
   - 任务规则：
     - 不同文件 = 标 [P] 可并行
     - 同一文件 = 顺序执行（不加 [P]）
     - 若要求测试：测试先于实现（TDD 顺序）
   - 任务顺序编号（T001、T002…）
   - 生成依赖图（用户故事完成顺序）
   - 为每个用户故事给出并行执行示例
   - 校验任务完备性（每个故事任务齐全、可独立测试）

4. **生成 tasks.md**：以 `.specify/templates/tasks-template.md` 为结构填充。**重要：所有任务描述、目标说明、检查点说明必须中文**。包括：
   - 来自 plan.md 的正确功能名
   - Phase 1: Setup 任务（项目初始化）
   - Phase 2: Foundational 任务（所有用户故事的阻断前置）
   - Phase 3+: 每个用户故事一个阶段（按 spec.md 优先级）
     - 每阶段含：故事目标、独立测试标准、测试（如要求）、实现任务
     - 每个任务带 [Story] 标签（US1、US2、US3…）
     - 阶段内可并行任务标 [P]
     - 每个故事阶段后设检查点标记
   - 末阶段: Polish 与横切关注点
   - 顺序编号（T001、T002…）
   - 每个任务的明确文件路径
   - 依赖章节（故事完成顺序）+ 每故事并行执行示例
   - 实施策略章节（生产级切片优先、增量交付）

5. **报告**：输出 tasks.md 路径与摘要：
   - 任务总数；每用户故事任务数；识别出的并行机会；每故事独立测试标准
   - 建议首个生产级核心切片范围（通常就是用户故事 1）

Context for task generation: 用户输入

tasks.md 应当即取即用——每个任务必须具体到无需额外上下文即可完成。

## Task Generation Rules

**重要**: 测试可选。仅当用户在功能规格中明确要求测试或 TDD 时生成测试任务。

**关键**: 任务必须按用户故事组织，以支持独立实现与独立测试。

1. **来自用户故事（spec.md）**——主组织轴：
   - 每个用户故事（P1、P2、P3…）独占一个阶段
   - 相关组件映射到所属故事：该故事需要的模型 / 服务 / 端点与 UI /（如要求）测试
   - 标注故事间依赖（多数故事应相互独立）

2. **来自契约（contracts/）**：
   - 每个契约/端点 → 映射到它服务的用户故事
   - 如要求测试：每契约 → 该故事阶段内实现之前的契约测试任务 [P]

3. **来自数据模型**：
   - 每个实体 → 映射到需要它的故事；多故事共用 → 放最早的故事或 Setup 阶段
   - 关系 → 相应故事阶段的服务层任务

4. **来自 Setup/基础设施**：
   - 共享基础设施 → Setup 阶段（Phase 1）
   - 阻断性前置 → Foundational 阶段（Phase 2，例：数据库 schema、认证框架、核心库、基础配置）——必须在任何用户故事之前完成
   - 故事专属 setup → 该故事阶段内

5. **排序**：
   - Phase 1: Setup → Phase 2: Foundational → Phase 3+: 用户故事按优先级 → 末阶段: Polish
   - 每阶段内（如要求测试）：测试 → 模型 → 服务 → 端点 → 集成
   - 每个用户故事阶段应是完整、可独立测试的增量
