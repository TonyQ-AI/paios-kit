# 贡献指南

感谢您对 SpecKit-CN 项目的关注！我们欢迎任何形式的贡献。

## 🤝 如何贡献

### 报告问题

如果您发现了 bug 或有功能建议：

1. 检查 [Issues](https://github.com/chameleon-nexus/speckit-cn/issues) 中是否已有相关问题
2. 如果没有，创建一个新的 Issue，详细描述：
   - 问题描述或功能需求
   - 复现步骤（如果是 bug）
   - 预期行为
   - 实际行为
   - 环境信息（操作系统、AI 工具版本等）

### 提交代码

1. **Fork 项目**
   ```bash
   # 点击 GitHub 页面右上角的 Fork 按钮
   ```

2. **克隆您的 Fork**
   ```bash
   git clone https://github.com/YOUR_USERNAME/speckit-cn.git
   cd speckit-cn
   ```

3. **创建功能分支**
   ```bash
   git checkout -b feature/your-feature-name
   # 或修复 bug
   git checkout -b fix/your-bug-fix
   ```

4. **进行修改**
   - 遵循现有的代码风格
   - 确保中文翻译准确、自然
   - 添加或更新相关文档

5. **提交更改**
   ```bash
   git add .
   git commit -m "简短描述您的更改"
   ```

6. **推送到您的 Fork**
   ```bash
   git push origin feature/your-feature-name
   ```

7. **创建 Pull Request**
   - 访问 [SpecKit-CN](https://github.com/chameleon-nexus/speckit-cn)
   - 点击 "New Pull Request"
   - 选择您的分支
   - 填写 PR 描述，说明您的更改内容

## 📝 提交信息规范

我们使用 [约定式提交](https://www.conventionalcommits.org/zh-hans/) 规范：

- `feat:` 新功能
- `fix:` Bug 修复
- `docs:` 文档更新
- `style:` 代码格式调整
- `refactor:` 代码重构
- `test:` 测试相关
- `chore:` 构建/工具相关

示例：
```
feat: 添加新的命令 /代码审查
fix: 修复模板中的格式问题
docs: 更新 README 中的安装说明
```

## 🎯 贡献领域

### 高优先级
- 📝 改进文档和示例
- 🐛 修复已知 bug
- 🌍 优化中文翻译质量
- 🧪 添加测试用例

### 欢迎贡献
- ✨ 新功能建议和实现
- 🎨 UI/UX 改进
- 🚀 性能优化
- 📦 新的模板和工作流

### 特别欢迎
- 添加更多 AI 助手支持（如 GPT-4、通义千问等）
- 创建示例项目
- 视频教程和文章
- 社区支持和答疑

## 📋 开发指南

### 目录结构
```
speckit-cn/
├── .claude/commands/      # Claude 命令定义
├── .specify/
│   ├── scripts/          # 自动化脚本
│   └── templates/        # 文档模板
└── README.md
```

### 命令文件格式
每个命令文件包含：
- `description:` 命令的简短描述（中文）
- `## User Input` 用户输入处理
- `## Outline` 执行步骤
- 详细的执行逻辑

### 模板文件
- 使用 Markdown 格式
- 保持结构清晰
- 使用中文注释说明
- 提供示例内容

## ⚠️ 注意事项

1. **保持向后兼容**：避免破坏现有功能
2. **测试您的更改**：确保命令能正常工作
3. **更新文档**：如果添加新功能，请更新 README
4. **遵循风格**：保持与现有代码一致的风格
5. **一个 PR 一个功能**：避免在一个 PR 中包含多个不相关的更改

## 🔍 代码审查

所有的 Pull Request 都会经过审查：

- 代码质量和风格
- 功能完整性
- 文档完善度
- 测试覆盖
- 中文翻译质量

审查可能需要几天时间，请耐心等待。

## 📞 联系方式

如有任何问题：
- 提交 [Issue](https://github.com/chameleon-nexus/speckit-cn/issues)
- 参与 [Discussions](https://github.com/chameleon-nexus/speckit-cn/discussions)

## 🙏 感谢

感谢所有为 SpecKit-CN 做出贡献的开发者！

您的贡献将帮助更多中文开发者提升开发效率。

