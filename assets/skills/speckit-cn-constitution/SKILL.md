---
name: speckit-cn-constitution
description: "定规则：创建或更新项目宪章（.specify/memory/constitution.md），定义不可协商的开发原则与质量基线。对应 SpecKit-CN 命令 /定规则。触发词：定规则、项目宪章、constitution、开发原则。需项目已初始化 .specify/（缺则先跑 $speckit-cn-init）。"
---

> 适配自 [SpecKit-CN](https://github.com/chameleon-nexus/speckit-cn)（commit 0db9fa5）的 `claude/.claude/commands/定规则.md`；主副本在 `{{ZCODE_DIR}}/speckit-cn`。

## 用户输入

用户输入 = 触发本技能的消息中、技能名之后的文字（上游 `$ARGUMENTS`）。非空时必须先考虑；为空则从对话上下文提取，没有再询问。

## 本机执行约定

- 本技能不运行脚本，但要求项目根已有 `.specify/`；缺失则先执行 `$speckit-cn-init` 流程再继续。
- 宪章写回必须用 Write/Edit 工具，UTF-8 编码，**全部内容中文**。

## Outline

你正在更新项目宪章 `.specify/memory/constitution.md`。该文件是一个含方括号占位符的模板（如 `[PROJECT_NAME]`、`[PRINCIPLE_1_NAME]`）。你的任务：(a) 收集/推导具体值，(b) 精确填充模板，(c) 将修订同步到依赖产物。

执行流程：

1. 读取 `.specify/memory/constitution.md` 现有模板。
   - 找出所有 `[ALL_CAPS_IDENTIFIER]` 形式占位符。
   - **重要**：用户要求的原则数量可以多于或少于模板；若指定了数量，遵照执行并相应调整文档。

2. 收集/推导占位符的值：
   - 用户输入（对话）提供了值 → 直接使用。
   - 否则从仓库上下文推断（README、docs、历史宪章版本）。
   - 治理日期：`RATIFICATION_DATE` 是最初批准日（未知则询问或标 TODO）；`LAST_AMENDED_DATE` 本次有修改则为今天，否则保留原值。
   - `CONSTITUTION_VERSION` 按语义化版本递增：
     * MAJOR：不向后兼容的治理/原则删除或重定义。
     * MINOR：新增原则/章节，或实质性扩展现有指引。
     * PATCH：澄清、措辞、笔误、非语义性修订。
   - 版本递增类型不明确时，先给出理由再定稿。

3. 起草更新后的宪章内容：
   - 每个占位符替换为具体文本（除项目明确选择暂不定义的槽位外不留方括号，保留的必须显式说明理由）。
   - 保持标题层级；注释在替换后可删除，除非仍有澄清价值。
   - 每条原则：简洁的名称行 + 段落/列表（不可协商规则）+ 必要的理由说明。
   - 治理章节列明修订程序、版本策略、合规审查要求。

4. 一致性传播检查：
   - 读 `.specify/templates/plan-template.md`，确保 "Constitution Check" 与新原则一致。
   - 读 `.specify/templates/spec-template.md`，检查范围/需求对齐——宪章增删强制章节或约束时同步更新。
   - 读 `.specify/templates/tasks-template.md`，确保任务分类反映新增/移除的原则性任务类型（如可观测性、版本管理、测试纪律）。
   - `.specify/templates/commands/` 目录（若存在）逐一检查过时引用；本适配中命令即 speckit-cn-* 技能，目录不存在则跳过该项。
   - 读运行时指引文档（README、docs/quickstart.md 等），更新被修改原则的引用。

5. 产出 Sync Impact Report（以 HTML 注释形式前置在宪章文件顶部）：
   - 版本变化：旧 → 新
   - 修改的原则清单（改名则旧名 → 新名）
   - 新增/删除的章节
   - 需要更新的模板（✅ 已更新 / ⚠ 待处理）及路径
   - 后续 TODO（如有刻意保留的占位符）

6. 定稿前校验：
   - 无未说明的方括号占位符残留。
   - 版本行与报告一致；日期 ISO 格式 YYYY-MM-DD。
   - 原则均为声明式、可测试、无模糊语言（"should" 视情况改为 MUST/SHOULD 并给出理由）。

7. **使用中文编写宪章**：完成后写回 `.specify/memory/constitution.md`（覆盖）。**重要：所有原则、说明、理由必须使用中文**。

8. 向用户输出最终摘要：
   - 新版本号与递增理由。
   - 标记需人工跟进的文件。
   - 建议的 commit message（如 `docs: 宪章修订至 vX.Y.Z（新增原则 + 治理更新）`）。

格式与风格：
- 标题层级严格沿用模板（不升降级）。
- 长理由行折行保持可读（理想 <100 字符），不做生硬硬折。
- 章节间保留单个空行；避免行尾空白。

若用户只给部分更新（如仅修订一条原则），仍执行校验与版本决策步骤。
若关键信息缺失（如批准日期实在不可考），插入 `TODO(<FIELD_NAME>): 说明` 并列入 Sync Impact Report 的暂缓项。
不要另造新模板；始终在现有 `.specify/memory/constitution.md` 上操作。
