# SpecKit-CN (中文版)

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

💫 基于规格驱动开发（Spec-Driven Development）的 AI 辅助开发工具包 - 中文版

## 📖 简介

**SpecKit-CN** 是基于 [GitHub Spec-Kit](https://github.com/github/spec-kit) 开发的完全汉化版本，专为中文开发者优化，帮助您通过 AI 助手（如 Claude）实现从需求到实现的完整软件开发流程自动化。

本项目实现了一套规范化的软件开发方法论，通过 AI 助手来自动化整个需求分析、设计、任务分解和实现的过程。

## ✨ 核心特性

### 🌏 完全中文化支持
- ✅ **命令指令汉化** - 所有命令使用中文名称，更符合中文用户习惯
- ✅ **描述文本汉化** - 命令描述和提示文本全部中文化
- ✅ **文档模板汉化** - 规格文档、计划文档、任务文档模板全部中文化
- ✅ **输出提示汉化** - AI 执行命令后的输出提示支持中文

### 🤖 AI 原生设计
- ✅ **Claude 完美支持** - 针对 Claude AI 优化的命令系统
- ✅ **智能技术栈生成** - AI 自动分析需求并推荐合适的技术栈
- ✅ **自动化工作流** - 从需求到实现的完整 AI 自动化流程

### 🎯 规格驱动开发
- ✅ **需求质量保证** - 内置需求质量检查清单（需求的单元测试）
- ✅ **一致性分析** - 自动检查规格、计划、任务三大核心文档的一致性
- ✅ **渐进式工作流** - 每个阶段输出清晰，支持迭代优化

## 🚀 快速开始

### 🌟 推荐方式：通过 Chameleon 使用（一键安装）

**[Chameleon AI Assistant](https://github.com/chameleon-nexus/Chameleon)** 已经集成了 SpecKit-CN！这是最简单的使用方式：

1. **安装 Chameleon VSCode 扩展**
   - 在 VS Code 中搜索并安装 `chameleon-ai-launcher`
   - 或从 [GitHub Releases](https://github.com/chameleon-nexus/Chameleon/releases) 下载 VSIX 文件安装

2. **配置 Claude Code 模式**
   - 打开 Chameleon 的 Claude 页面
   - 在页面顶部的下拉列表中选择 **"Claude Code（第三方AI）"**
   - 这是使用第三方 AI 的模式

3. **一键安装 SpecKit-CN**
   - 在依赖检测列表中找到 **SpecKit-CN (中文规格驱动开发工具)**
   - 点击 **"一键安装"** 按钮
   - 系统会自动：
     - 从 GitHub 克隆 SpecKit-CN
     - 复制 `.specify/` 到项目根目录
     - 复制中文命令到 `C:\Users\{用户名}\.claude\commands`

4. **开始使用**
   - 点击 **"启动终端"** 按钮进入 Claude Code
   - 在 Claude Code 中直接使用中文命令
   - 无需手动配置，开箱即用

> 💡 **推荐理由**：Chameleon 提供了完整的 Claude Code 环境、Agent 市场和多 AI 引擎支持，配合 SpecKit-CN 实现最佳开发体验。

### 📦 传统方式：手动安装

如果您不使用 Chameleon，也可以手动安装：

#### 前置要求

- Git
- Claude AI（通过 Cursor、Claude Desktop 等）

#### 安装步骤

1. **下载 SpecKit-CN**

```bash
git clone https://github.com/chameleon-nexus/speckit-cn.git
cd speckit-cn
```

2. **复制文件到正确位置**（Windows PowerShell）

```powershell
# 复制命令文件到用户目录
Copy-Item -Path "claude\.claude\commands\*" -Destination "$env:USERPROFILE\.claude\commands\" -Recurse -Force

# 复制模板和脚本到项目根目录
Copy-Item -Path "claude\.specify" -Destination ".\.specify" -Recurse -Force
```

> **注意**: 本项目目前仅支持 Windows 系统。

3. **验证安装**

检查文件是否复制成功：
- `C:\Users\{用户名}\.claude\commands\` 应包含 8 个中文命令文件
- 项目根目录 `.specify\` 应包含模板和脚本文件

4. **开始使用**

⚠️ **重要提示**：使用 SpecKit-CN 命令前，需要先进入 Claude Code 环境：

```powershell
# 在项目根目录打开终端，输入：
claude

# 进入 Claude Code 后，即可使用中文命令：
/提需求 创建一个博客系统
/定计划
/分任务
/开始开发
```

## 📋 完整工作流程

SpecKit-CN 提供了 8 个核心命令，覆盖从需求到实现的完整开发生命周期：

### 1. `/提需求` - 创建需求规格
描述您的功能需求，AI 会自动生成结构化的需求规格文档。

```
/提需求 我需要一个任务管理系统，用户可以创建、编辑、删除任务
```

### 2. `/需求变更` - 澄清需求（可选）
识别并澄清需求规格中的模糊之处。

```
/需求变更
```

### 3. `/定规则` - 设置项目宪章（可选）
定义不可协商的开发原则和质量标准。

```
/定规则
```

### 4. `/定计划` - 生成技术方案
AI 架构师自动分析需求并生成技术实现计划和设计文档。

**✨ 智能技术栈推荐**：如果您不指定技术栈，AI 会根据需求自动推荐合适的技术方案。

```
/定计划
# 或指定技术栈
/定计划 使用 Python FastAPI + PostgreSQL + React
```

### 5. `/分任务` - 任务分解
将计划分解为可执行的任务列表，自动生成任务依赖关系。

```
/分任务
```

### 6. `/测试清单` - 生成质量检查清单
生成需求质量验证清单，确保需求的完整性和清晰度。

```
/测试清单 UX需求质量检查
```

### 7. `/一致性分析` - 跨文档分析（可选）
分析 spec.md、plan.md、tasks.md 三个核心文档的一致性。

```
/一致性分析
```

### 8. `/开始开发` - 执行实现
AI 程序员按照任务列表开始执行所有开发任务。

```
/开始开发
```

## 📁 项目结构

```
speckit-cn/
├── .claude/
│   └── commands/          # Claude 命令定义（中文版）
│       ├── 提需求.md
│       ├── 需求变更.md
│       ├── 定规则.md
│       ├── 定计划.md
│       ├── 分任务.md
│       ├── 测试清单.md
│       ├── 一致性分析.md
│       └── 开始开发.md
├── .specify/
│   ├── memory/
│   │   └── constitution.md
│   ├── scripts/
│   │   └── powershell/    # PowerShell 脚本
│   └── templates/         # 中文化文档模板
│       ├── spec-template.md
│       ├── plan-template.md
│       ├── tasks-template.md
│       └── checklist-template.md
└── specs/                 # 功能规格存储目录
```

## 🆚 与原版的区别

| 特性 | GitHub Spec-Kit | SpecKit-CN |
|------|----------------|------------|
| 语言支持 | 英文 | 中文 |
| 命令名称 | `/speckit.specify` | `/提需求` |
| 文档模板 | 英文 | 中文 |
| AI 支持 | Claude, Copilot | 优化 Claude 支持 |
| 技术栈推荐 | 手动指定 | AI 自动推荐 |
| 输出提示 | 英文 | 中文 |

## 📚 命令对照表

| 中文命令 | 英文命令 | 功能 |
|---------|---------|------|
| `/提需求` | `/speckit.specify` | 创建需求规格 |
| `/需求变更` | `/speckit.clarify` | 澄清需求 |
| `/定规则` | `/speckit.constitution` | 设置项目宪章 |
| `/定计划` | `/speckit.plan` | 生成技术方案 |
| `/分任务` | `/speckit.tasks` | 任务分解 |
| `/测试清单` | `/speckit.checklist` | 生成质量检查清单 |
| `/一致性分析` | `/speckit.analyze` | 跨文档一致性分析 |
| `/开始开发` | `/speckit.implement` | 执行实现 |

## 🎯 使用示例

### 示例：创建一个博客系统

```bash
# 1. 提需求
/提需求 创建一个个人博客系统，支持文章发布、分类、标签、评论功能

# 2. AI 会询问技术栈偏好（可选）
# 如果不指定，AI 会自动推荐：Python + FastAPI + PostgreSQL + Vue.js

# 3. 定计划
/定计划

# 4. 分任务
/分任务

# 5. 生成测试清单
/测试清单 API接口质量检查

# 6. 开始开发
/开始开发
```

## 🤝 贡献

欢迎贡献！请查看 [CONTRIBUTING.md](CONTRIBUTING.md) 了解详情。

### 贡献领域

- 📝 文档改进和示例
- 🐛 Bug 修复
- ✨ 新功能建议
- 🌍 翻译优化
- 🧪 测试用例

## 📄 许可证

本项目基于 MIT 许可证开源。详见 [LICENSE](LICENSE) 文件。

## 🙏 致谢

- 本项目基于 [GitHub Spec-Kit](https://github.com/github/spec-kit) 开发
- 感谢 John Lam 和 Den Delimarsky 的原创工作
- 感谢所有贡献者的支持

## 📞 支持

如有问题或建议，请：
- 📧 提交 [GitHub Issue](https://github.com/chameleon-nexus/speckit-cn/issues)
- 💬 参与 [Discussions](https://github.com/chameleon-nexus/speckit-cn/discussions)

## 🔗 相关链接

### 推荐工具
- **[Chameleon AI Assistant](https://github.com/chameleon-nexus/Chameleon)** - 已集成 SpecKit-CN 的 VSCode AI 启动器
  - 一键安装 SpecKit-CN
  - 完整的 Claude Code 环境
  - 500+ AI Agent 市场
  - 多 AI 引擎支持

### 相关项目
- [GitHub Spec-Kit（原版）](https://github.com/github/spec-kit)
- [Claude AI](https://claude.ai/)
- [Cursor](https://cursor.sh/)

---

**Made with ❤️ for Chinese Developers**

