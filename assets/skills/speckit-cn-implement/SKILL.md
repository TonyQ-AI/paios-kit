---
name: speckit-cn-implement
description: "开始开发：AI 程序员按 tasks.md 执行全部开发任务（逐阶段推进、TDD 顺序、并行任务协调、检查清单门禁、进度中文汇报）。对应 SpecKit-CN 命令 /开始开发。触发词：开始开发、执行任务、implement。需项目已有 tasks.md（缺则先跑 $speckit-cn-tasks）。"
---

> 适配自 [SpecKit-CN](https://github.com/chameleon-nexus/speckit-cn)（commit 0db9fa5）的 `claude/.claude/commands/开始开发.md`；主副本在 `{{ZCODE_DIR}}/speckit-cn`。

## 用户输入

用户输入 = 触发消息中技能名之后的文字（上游 `$ARGUMENTS`）。非空时必须先考虑（如"只做 US1"、"跳过 Polish"）。

## 本机执行约定

- 运行 `.ps1` 一律套静默包装：`python {{ZCODE_DIR}}/scripts/silent.py powershell -NoProfile -ExecutionPolicy Bypass -File <脚本> [参数...]`
- 项目根缺 `.specify/` → 先执行 `$speckit-cn-init` 流程再继续。
- 实现代码按项目既有风格写；勾选任务、更新 tasks.md 用 Edit 工具；进度汇报中文。

## Outline

1. 在项目根运行 `.specify/scripts/powershell/check-prerequisites.ps1 -Json -RequireTasks -IncludeTasks`，解析 `FEATURE_DIR` 与 `AVAILABLE_DOCS`。路径须为绝对路径。

2. **检查清单状态**（`FEATURE_DIR/checklists/` 存在时）：
   - 扫描 checklists/ 下所有清单文件，统计每份：
     * 总项数：匹配 `- [ ]` 或 `- [X]`/`- [x]` 的行
     * 已完成：`- [X]`/`- [x]`；未完成：`- [ ]`
   - 生成状态表：
     ```
     | 清单 | 总数 | 已完成 | 未完成 | 状态 |
     |------|------|--------|--------|------|
     | ux.md       | 12 | 12 | 0 | ✓ PASS |
     | test.md     |  8 |  5 | 3 | ✗ FAIL |
     ```
   - 总体判定：**PASS** 全部清单 0 未完成；**FAIL** 存在未完成项
   - **有未完成清单**：
     * 展示表格 → **停下**询问："部分清单未完成，仍要继续实现吗？（yes/no）"
     * 用户答 no/wait/stop → 终止；答 yes/proceed/continue → 进入第 3 步
   - **全部完成** → 展示全 PASS 表格，自动进入第 3 步

3. 加载并分析实现上下文：
   - **必需**: tasks.md（完整任务清单与执行计划）、plan.md（技术栈、架构、文件结构）
   - **如有**: data-model.md（实体与关系）、contracts/（API 规格与测试要求）、research.md（技术决策与约束）、quickstart.md（集成场景）

4. 解析 tasks.md 结构，提取：
   - **任务阶段**: Setup、Tests、Core、Integration、Polish
   - **任务依赖**: 顺序 vs 并行（[P]）执行规则
   - **任务详情**: ID、描述、文件路径、[P] 标记
   - **执行流**: 顺序与依赖要求

5. 按任务计划执行实现：
   - **逐阶段推进**：每阶段完成后才进入下一阶段
   - **尊重依赖**：顺序任务按序执行；[P] 并行任务可同时进行
   - **遵循 TDD**：测试任务先于对应实现任务执行
   - **按文件协调**：影响同一文件的任务必须串行
   - **验证检查点**：每阶段完成须验证后才继续

6. 实现执行规则：
   - **Setup 先行**：初始化项目结构、依赖、配置
   - **测试先行**：需为契约、实体、集成场景写测试时先写测试
   - **核心开发**：实现模型、服务、CLI 命令、端点
   - **集成工作**：数据库连接、中间件、日志、外部服务
   - **打磨与验证**：单元测试、性能优化、文档

7. 进度跟踪和错误处理：
   - 每完成一个任务报告一次进度。**重要：所有进度报告、错误消息、建议必须中文**
   - 任一非并行任务失败 → 暂停执行
   - [P] 并行任务 → 继续成功的任务，汇报失败项
   - 提供带上下文的清晰错误信息，便于调试；无法继续时给出下一步建议
   - **重要**：完成任务后，务必在 tasks.md 中把对应任务勾选为 `[X]`

8. 完成校验：
   - 核对所有必需任务已完成
   - 核对实现与原始规格一致
   - 验证测试通过、覆盖达标
   - 确认实现遵循技术方案
   - 报告最终状态与完成工作摘要

注意：本技能假定 tasks.md 已有完整任务分解。任务不完整或缺失 → 建议先运行 `$speckit-cn-tasks` 重新生成。
