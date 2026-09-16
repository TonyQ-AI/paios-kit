---
name: speckit-cn-analyze
description: "一致性分析：跨 spec.md / plan.md / tasks.md 三大核心文档做只读的一致性与质量分析（重复、歧义、欠规格、覆盖缺口、宪章冲突），输出结构化中文报告，不改文件。对应 SpecKit-CN 命令 /一致性分析。触发词：一致性分析、跨文档分析、analyze。需项目已有 tasks.md（缺则先跑 $speckit-cn-tasks）。"
---

> 适配自 [SpecKit-CN](https://github.com/chameleon-nexus/speckit-cn)（commit 0db9fa5）的 `claude/.claude/commands/一致性分析.md`；主副本在 `{{ZCODE_DIR}}/speckit-cn`。

## 用户输入

用户输入 = 触发消息中技能名之后的文字（上游 `$ARGUMENTS`）。非空时必须先考虑。

## 本机执行约定

- 运行 `.ps1` 一律套静默包装：`python {{ZCODE_DIR}}/scripts/silent.py powershell -NoProfile -ExecutionPolicy Bypass -File <脚本> [参数...]`
- 项目根缺 `.specify/` → 先执行 `$speckit-cn-init` 流程再继续。

## Goal

在实现开始前，识别三大核心产物（`spec.md`、`plan.md`、`tasks.md`）之间的不一致、重复、歧义与欠规格项。**只在 `$speckit-cn-tasks` 产出完整 tasks.md 之后运行**。

## Operating Constraints

**严格只读**：**不**修改任何文件。输出结构化分析报告；可提供可选的修复计划（用户明确批准后才执行后续编辑）。

**宪章权威**：项目宪章（`.specify/memory/constitution.md`）在本分析范围内**不可协商**。与宪章冲突自动定为 CRITICAL，须调整 spec/plan/tasks——而不是稀释、重释或无视原则。若原则本身要改，必须在 `/一致性分析` 之外单独显式更新宪章（`$speckit-cn-constitution`）。

## Execution Steps

### 1. 初始化分析上下文

在项目根运行一次 `.specify/scripts/powershell/check-prerequisites.ps1 -Json -RequireTasks -IncludeTasks`，解析 `FEATURE_DIR` 与 `AVAILABLE_DOCS`。推导绝对路径：

- SPEC = FEATURE_DIR/spec.md
- PLAN = FEATURE_DIR/plan.md
- TASKS = FEATURE_DIR/tasks.md

任一必需文件缺失 → 报错中止（提示用户先运行对应的先行技能）。

### 2. 加载产物（渐进披露）

只从每个产物加载最小必要上下文：

**spec.md**：概述/背景；功能需求；非功能需求；用户故事；边界情形（如有）

**plan.md**：架构/技术栈选型；数据模型引用；阶段划分；技术约束

**tasks.md**：任务 ID；描述；阶段分组；[P] 并行标记；引用的文件路径

**宪章**：读取 `.specify/memory/constitution.md` 做原则校验

### 3. 构建语义模型

建立内部表示（不在输出中倾倒原文）：

- **需求清单**：每条功能 + 非功能需求配稳定键（按祈使短语派生 slug，如"用户可上传文件" → `user-can-upload-file`）
- **用户故事/动作清单**：离散用户动作及其验收标准
- **任务覆盖映射**：每个任务映射到一条或多条需求/故事（按关键词或显式引用模式推断）
- **宪章规则集**：提取原则名与 MUST/SHOULD 规范性表述

### 4. 检测 pass（高信号，总量 ≤ 50 条，溢出汇总）

#### A. 重复检测
- 识别近重复需求；标记表述较差的一方供合并

#### B. 歧义检测
- 标记缺少度量标准的模糊形容词（快、可扩展、安全、直观、健壮）
- 标记未解决的占位符（TODO、TKTK、???、`<placeholder>`）

#### C. 欠规格
- 有动词但缺宾语或可度量结果的需求
- 缺验收标准对齐的用户故事
- 引用 spec/plan 未定义文件或组件的任务

#### D. 宪章对齐
- 任何与 MUST 原则冲突的需求或计划元素
- 宪章强制章节或质量门禁缺失

#### E. 覆盖缺口
- 零任务关联的需求
- 无需求/故事映射的任务
- 未反映到任务的非功能需求（性能、安全等）

#### F. 不一致
- 术语漂移（同一概念跨文件不同叫法）
- plan 引用了 spec 缺失的实体（或反之）
- 任务顺序矛盾（如基础 setup 之前就做集成，且无依赖说明）
- 需求冲突（如一处要求 Next.js 另一处要求 Vue）

### 5. 严重度定级

- **CRITICAL**：违反宪章 MUST / 缺失核心产物 / 阻断基线功能的零覆盖需求
- **HIGH**：重复或冲突需求、含糊的安全/性能属性、不可测试的验收标准
- **MEDIUM**：术语漂移、非功能任务覆盖缺失、欠规格边界情形
- **LOW**：风格措辞、不影响执行顺序的轻微冗余

### 6. 生成简洁分析报告

输出 Markdown 报告（**不写入文件**）。**重要：整个报告必须中文**：

## 规格一致性分析报告

| ID | 类别 | 严重度 | 位置 | 摘要 | 建议 |
|----|------|--------|------|------|------|
| A1 | 重复 | HIGH | spec.md:L120-134 | 两条相近需求 … | 合并表述，保留更清晰版本 |

（每个发现一行；ID 用类别首字母前缀 + 序号，保持稳定）

**覆盖摘要表：**

| 需求键 | 有任务? | 任务 ID | 备注 |
|--------|---------|---------|------|

**宪章对齐问题：**（如有）

**未映射任务：**（如有）

**指标：**
- 需求总数 / 任务总数
- 覆盖率 %（有 ≥1 任务的需求占比）
- 歧义数 / 重复数 / CRITICAL 数

### 7. 给出下一步建议

报告末尾输出简短 Next Actions：
- 有 CRITICAL → 建议先解决再 `$speckit-cn-implement`
- 只有 LOW/MEDIUM → 可继续，附改进建议
- 给出显式建议："重新运行 $speckit-cn-specify 细化"、"运行 $speckit-cn-plan 调整架构"、"手动编辑 tasks.md 为 'performance-metrics' 补覆盖"

### 8. 提供修复选项

询问用户："需要我针对前 N 个问题给出具体修复编辑建议吗？"（**不**自动应用。）

## Operating Principles

- **上下文高效**：聚焦可行动的发现；发现表 ≤ 50 行，溢出汇总
- **绝不修改文件**（只读分析）；**绝不编造缺失章节**（缺失就如实报告）
- **宪章违规最优先**（恒为 CRITICAL）
- **用实例而非穷举规则**（引用具体条目，不引泛化模式）
- **零问题也优雅报告**（输出带覆盖统计的成功报告）
