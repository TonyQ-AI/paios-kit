# PAIOS · 个人 AI 作业系统（分发版设计说明）

> Personal AI Operation System
> 本文档是 paios-kit 所含「记忆基座 + 知识层」的设计说明，供使用与二次开发参考。

## 一、系统定位

一句话：一个跟着所有 ZCode 会话自动运转的个人作业系统——你干活，它记账；你遇坑，它提醒；换会话、隔半年、换项目，经验都还在。

三句初心：

1. **让任何新会话满血接续**——不问「上次聊到哪了」
2. **让任何旧会话安全死去**——价值沉淀在文件里，不锁在对话里
3. **让付过的学费自动复利**——AI 带着你的经验干活

## 二、三层结构

```
┌─────────────────────────────────────────────────────┐
│ 接入层：ZCode 会话                                    │
│  · MCP stdio server（paios_ 前缀 10 个工具，按需拉起） │
│  · Stop hook（会话结束时自动：索引→候选→备份→推送）     │
│  · Web UI（127.0.0.1 本机浏览，不出网）                │
├─────────────────────────────────────────────────────┤
│ 知识层：经验原子（knowledge/atoms/*.md 唯一真相源）     │
│  · 7 字段提炼：结论/背景/人的判断/理由/AI错误/修正/边界 │
│  · 写入门禁：边界必填、AI错误禁编造、词面+语义双重查重   │
│  · 状态机：draft（不进检索）→ active → deprecated     │
├─────────────────────────────────────────────────────┤
│ 归档层：data/index.sqlite（会话索引，永不进检索）        │
│  · 标题/摘要/项目归属/文件操作统计，增量水位线同步       │
└─────────────────────────────────────────────────────┘
```

**核心原则：原始对话只归档、永不进检索；只有提炼后的「经验原子」才进知识库。**
（原始素材好坏混杂，直接进检索库会把过去的错误尝试当真理输出——上下文污染。）

## 三、数据流

1. **写入**：ZCode 会话结束 → Stop hook 调 `indexer.sync()`（只读打开 `~/.zcode/cli/db/db.sqlite`，按水位线增量拉新会话）→ 项目归属判定 → 写入 index.sqlite；
2. **沉淀**：`candidates.mark()` 按特征（报错修复密集、大规模生产）标记值得提炼的会话 → AI 提议 → 用户点头 → `paios_save_atom` 过门禁写 atoms/*.md（draft）；
3. **读取**：新任务开工 → AI 按关键词调 `paios_search_atoms`（词面评分 + fastembed 语义相似）→ 命中即注入结论+边界，未命中不注入；
4. **备份**：`backup.run_daily()` 每日滚动备份；`push_atoms.push_atoms()` 对 knowledge/ 目录 git commit + push（配远程才启用，失败排队续推）。

## 四、目录结构

```
<安装根>/
├── mcp_entry.py / run_hook.py / webui.py   三个入口
├── paios/                    核心包（纯 Python 标准库 + fastembed）
│   ├── __init__.py           路径与配置中心（全相对推导，可移植）
│   ├── indexer.py            会话增量索引 + 导出
│   ├── classify.py / enrich.py   项目归属判定 / 富化
│   ├── atoms.py              原子读写 / 检索 / 门禁 / 状态机
│   ├── vectors.py            fastembed 语义向量（离线优先，缺失走 hf-mirror）
│   ├── candidates.py         候选沉淀标记
│   ├── backup.py / push_atoms.py   备份 / 原子库 git 推送
│   ├── hook.py / mcp.py / webserver.py   三个接入口
│   └── static/               Web UI 页面
├── knowledge/                知识层（atoms/*.md 真相源 + INDEX.md 索引）
├── data/                     index.sqlite / workspace-roots.json / models/
└── backup/                   每日滚动备份
```

## 五、关键设计决策（可抄的原则）

- **真相源是 markdown**：索引、向量缓存都是派生物，可随时重建；引擎可插拔（vectors.py 隔离了 fastembed）；
- **hook 静默纪律**：stdout 一个字节都不输出（ZCode 严格校验），诊断只走 stderr，永远 exit 0，单步失败不连带；
- **MCP 与业务解耦**：全部工具走 stdin/stdout JSON-RPC，无框架依赖；
- **写入门禁优先于量**：draft 不进检索、AI 错误字段禁编造、适用边界禁写「通用」；
- **本地优先**：全部数据落本机，Web UI 仅绑 127.0.0.1；云备份是可选的 git push。

## 六、扩展指南

- **换端口**：`paios/__init__.py` 的 `DEFAULT_PORT`，或安装时 `--port`；
- **换嵌入模型**：`vectors.py` 的 `MODEL_NAME`（删除 `data/models/` 后重跑自动下载）；
- **加 MCP 工具**：`mcp.py` 的 `TOOLS` 列表加 schema + `call_tool` 加分支；
- **云备份原子库**：安装根 `git init` + 配远程即可，Stop hook 自动 commit + push（代理设 `PAIOS_GIT_PROXY` 环境变量）。
