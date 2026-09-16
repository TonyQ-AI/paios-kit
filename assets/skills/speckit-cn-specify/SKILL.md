---
name: speckit-cn-specify
description: "提需求：从自然语言功能描述创建结构化中文需求规格（spec.md），含质量校验与澄清问题。对应 SpecKit-CN 命令 /提需求。触发词：提需求、写需求规格、新建功能规格、specify。需项目已初始化 .specify/（缺则先跑 $speckit-cn-init）。"
---

> **已有权威文档时的处理（文档纳入模式）**：若用户提供了「现成需求 / 架构 / API / 测试」文档，把它们当作**输入原料**——本步骤将文档中**已声明**的内容逐条**映射**进本产物，缺口标「（待定义）」，**绝不凭空发明**；本产物生成后即成为**唯一权威信源**，原始文档仅作参考，不得作为直接写代码的依据。

> 适配自 [SpecKit-CN](https://github.com/chameleon-nexus/speckit-cn)（commit 0db9fa5）的 `claude/.claude/commands/提需求.md`；主副本在 `{{ZCODE_DIR}}/speckit-cn`。

## 用户输入

触发本技能的消息中、技能名之后的文字**就是**功能需求描述（上游 `$ARGUMENTS`）。假定它在本对话中始终可得，即使下文出现字面 `$ARGUMENTS`。为空才向用户索取。

## 本机执行约定

- 运行 `.ps1` 一律套静默包装：`python {{ZCODE_DIR}}/scripts/silent.py powershell -NoProfile -ExecutionPolicy Bypass -File <脚本> [参数...]`
- **分支短名只用 ASCII**（见步骤 1）——脚本会把非 ASCII 全部替换成 `-`，中文参数会生成残缺分支名。
- 项目根缺 `.specify/` → 先执行 `$speckit-cn-init` 流程再继续。
- 产物用 Write/Edit 工具写入，UTF-8，正文中文。

## Outline

1. **生成 ASCII 短名**（2-4 个词）：从功能描述提取最有概括力的关键词，动宾式优先（如 `user-auth`、`fix-payment-timeout`；中文描述取英文/拼音译名）。保留专有技术名词（OAuth2、API、JWT）。此短名将决定分支名与 specs 目录名。

2. 运行脚本（**只运行一次**），从 JSON 输出解析 `BRANCH_NAME` 和 `SPEC_FILE`（绝对路径）：

   ```bash
   python {{ZCODE_DIR}}/scripts/silent.py powershell -NoProfile -ExecutionPolicy Bypass -File .specify/scripts/powershell/create-new-feature.ps1 -Json "<ASCII短名>"
   ```

   经 silent.py 以参数列表传参，无需 shell 引号转义。脚本会创建并切换分支、初始化 spec 文件。

3. 读取 `.specify/templates/spec-template.md` 了解必需章节。

4. 按以下执行流处理：

   1. 从用户输入解析功能描述；为空 → ERROR "未提供功能描述"
   2. 提取关键概念：参与者、动作、数据、约束
   3. 对不明确处：
      - 基于上下文与行业惯例做合理假设
      - 仅在同时满足以下条件时标记 `[NEEDS CLARIFICATION: 具体问题]`：显著影响范围或体验 / 存在多种合理解释且后果不同 / 无合理默认值
      - **上限：全文最多 3 处**；按影响排序：范围 > 安全/隐私 > 用户体验 > 技术细节
   4. 填写「用户场景与测试」章节；无法确定用户流 → ERROR
   5. 生成功能需求（每条必须可测试；未指定细节用合理默认值并记入 Assumptions）
   6. 定义成功标准（可度量、技术无关；含量化指标与定性指标）
   7. 涉及数据时识别关键实体
   8. 返回：SUCCESS（规格就绪，可进入定计划）

5. **使用中文编写规格**：将规格写入 `SPEC_FILE`，沿用模板结构与章节顺序，用功能描述中的具体细节替换占位符。**重要：所有内容必须中文**（用户故事、需求描述、验收场景等）。

6. **规格质量校验**：

   a. **创建规格质量清单**：在 `SPEC_FILE` 同目录的 `checklists/requirements.md` 生成校验清单（结构参照 `.specify/templates/checklist-template.md`），校验项至少覆盖：

      ```markdown
      ## 内容质量
      - [ ] 不含实现细节（语言、框架、API）
      - [ ] 聚焦用户价值与业务需要
      - [ ] 面向非技术干系人可读
      - [ ] 必填章节全部完成
      ## 需求完备性
      - [ ] 无残留 [NEEDS CLARIFICATION] 标记
      - [ ] 需求可测试、无歧义
      - [ ] 成功标准可度量且技术无关
      - [ ] 所有验收场景已定义
      - [ ] 边界情形已识别
      - [ ] 范围边界清晰
      - [ ] 依赖与假设已识别
      ## 功能就绪度
      - [ ] 每条功能需求有明确验收标准
      - [ ] 用户场景覆盖主流程
      - [ ] 与成功标准呼应且可度量
      - [ ] 无实现细节泄漏进规格
      ```

   b. **逐项校验**：对照清单审查规格，记录通过与不通过项（引用具体章节）。

   c. **处理校验结果**：
      - 全部通过 → 勾选清单，进入第 7 步
      - 有不合格项（[NEEDS CLARIFICATION] 除外）→ 列出问题 → 修订规格 → 复验（最多 3 轮；仍不合格则记入清单备注并警告用户）
      - 仍有 [NEEDS CLARIFICATION] → 超过 3 处时只保留最关键的 3 处，其余做合理假设；然后逐条向用户提问（Q1/Q2/Q3，Markdown 表格给选项 A/B/C/Custom：选项 | 答案 | 影响），**一次性列出全部问题后等待用户答复**（如 "Q1: A, Q2: Custom-xxx"）；收到答复后把标记替换为用户的选择，并复验

   d. **更新清单**：每轮校验后把最新通过/不通过状态写回清单文件。

7. 报告完成：分支名、spec 文件路径、清单校验结果、下一阶段建议（`$speckit-cn-clarify` 或 `$speckit-cn-plan`）。

## 编写守则

- 聚焦用户需要什么（WHAT）与为什么（WHY），**不写怎么实现**（无技术栈、API、代码结构）。
- 面向业务干系人而非开发者。
- 不在规格内嵌套清单——清单是独立技能（`$speckit-cn-checklist`）。
- 章节不适用就整体删除，不留 "N/A"。

### 合理默认值示例（这些不要问用户）

- 数据保留期限：领域通行做法
- 性能目标：常规 Web/移动应用预期
- 错误处理：用户友好提示 + 合理降级
- 认证方式：Web 应用用标准 session 或 OAuth2
- 集成模式：Web 服务默认 RESTful

### 成功标准写法

- 好："用户 3 分钟内完成结账" / "1 万并发下正常响应" / "任务完成率提升 40%"
- 差："API 响应 <200ms"（太技术化）/ "React 组件高效渲染"（框架相关）
