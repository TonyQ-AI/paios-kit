# paios-kit —— PAIOS 记忆基座 + 知识层 + 新项目启动流程（ZCode 分发包）

把一套在 ZCode 上日常运转的「个人 AI 作业系统」打包成一键可装的分发包：

- **记忆基座**：自动归档你的全部 ZCode 会话（标题/摘要/项目归属/文件操作统计），Web UI 浏览、关键词检索、查历史不丢上下文。
- **知识层（经验原子）**：7 字段经验原子库 + 语义查重 + 检索/沉淀 MCP 工具（`paios_search_atoms` / `paios_save_atom` 等 10 个工具）。任务开工先查坑，任务结束沉淀经验。
- **新项目启动流程（skills）**：`$new-project`（会话大脑 + 登记卡）、SpecKit-CN 全套中文规格驱动技能（提需求→定计划→分任务→开始开发）、`$confirm-vision`（规格可视化确认）、`$knowledge-atom`（经验沉淀技能）。

工作方式：**你干活，它记账**。Stop hook 在每次会话结束时自动增量索引会话、标记值得沉淀的候选、备份；MCP 工具让任何新会话都能查历史、查经验。数据全部落在你本机。

## 前置条件

| 依赖 | 要求 |
|---|---|
| 操作系统 | Windows 10/11（启动器与避坑规则面向中文 Windows + Git Bash） |
| Python | 3.10 及以上（开发环境为 3.11），安装时勾选 Add to PATH |
| ZCode CLI | 已安装且**至少跑过一次会话**（记忆基座从 `~/.zcode/cli/db/db.sqlite` 增量索引） |
| 磁盘 | 首跑会自动下载中文嵌入模型约 90 MB（走 hf-mirror 国内镜像） |

## 安装（三步）

> **没有技术背景？** 直接看 [TROUBLESHOOTING.md](TROUBLESHOOTING.md)——中文图文手册，含每一步怎么点、常见报错对照表。
>
> **更省事的方式**：把下面的这段话原样发给你的 ZCode，让它自动装（装完它会让你重启 ZCode）：
>
> ```
> 请帮我在本机安装 paios-kit：克隆 https://github.com/TonyQ-AI/paios-kit 后，
> 严格按它 README 的「AI 安装指令」一节执行安装，完成后告诉我如何验证。
> ```

```bash
# 1. 获取本仓库（二选一）
#    网页下载: Code → Download ZIP → 解压到如 D:\paios-kit
git clone https://github.com/TonyQ-AI/paios-kit.git D:\tools\paios-kit
cd D:\tools\paios-kit

# 2. 一键安装（双击 install.bat；或命令行）
python install.py

# 3. 完全退出并重启 ZCode，新会话即可使用 paios_* 工具与 $new-project 等技能
```

需要先装 Python 3.10+（安装时**务必勾选 Add Python to PATH**），细节见手册第 1 节。

## AI 安装指令（给执行安装的 ZCode 看）

你是负责在本机安装 paios-kit 的 AI。按顺序执行，每步失败就按括号里的方案处理：

1. 克隆仓库到 `D:\tools\paios-kit`（目录被占用就换 `D:\paios-kit`）；
2. 检查 `python --version` 是否在 3.10–3.13 之间；没有或版本过低 → 用 `winget install -e --id Python.Python.3.12` 安装（winget 不可用则引导用户到 python.org 手动装并勾选 Add to PATH），**装完必须新开终端再继续**（PATH 才生效）；
3. 在仓库目录执行 `python install.py`（默认安装根 `%USERPROFILE%\paios`，端口 8643 被占会自动顺延）。pip 失败安装器会自动切清华镜像重试；模型预热失败不阻塞，可忽略；
4. 执行中用户的杀毒软件/SmartScreen 弹窗属误报（本地脚本，仅下载模型时联网），提醒用户点「允许」；
5. 安装完成后验证：`python -c "import sys; sys.path.insert(0, r'<安装根>'); import paios; print('ok')"` 输出 ok；
6. 可选配置：询问用户项目通常放在哪个目录，有则补跑 `python install.py --workspace-roots "<该目录>"`（多个用逗号分隔）；
7. 最后**必须提醒用户**：完全退出 ZCode（含托盘）并重新打开，paios_* 工具和 $new-project 等技能才会生效；验证方式是在新会话里说「搜一下我的历史会话」。

不要手动编辑 `~/.zcode/cli/config.json` 或 `~/.zcode/AGENTS.md`——`install.py` 会以合并方式写入，重复执行是安全的（幂等升级）。用户的会话数据与经验原子全部存放在安装根目录，卸载用仓库里的 `uninstall.bat`。

安装器做了什么（全部幂等，重跑即升级）：

1. 复制 PAIOS 程序到安装根（默认 `%USERPROFILE%\paios`，`--root` 可改）；
2. `pip install fastembed`（可用 `--skip-deps` 跳过）；
3. 初始化空数据目录（你的会话索引由 Stop hook 首跑自动建立）；
4. 装入 12 个技能到 `~/.zcode/skills/`、SpecKit-CN 主副本到 `~/.zcode/speckit-cn/`、`silent.py` 静默执行器到 `~/.zcode/scripts/`；
5. **合并写入**（不覆盖你已有配置）`~/.zcode/cli/config.json`：注册 `paios` MCP server + Stop hook；
6. 在 `~/.zcode/AGENTS.md` 追加使用规则段（记忆基座 / Windows 避坑手册 / 新项目启动规则，均带管理标记，可整体移除）；
7. 生成双击启动器 `<安装根>\launch_ui.vbs`。

### install.py 常用参数

```bash
python install.py --root D:\tools\paios            # 自定义安装根
python install.py --port 8743                      # 自定义 UI 端口（默认 8643，被占自动顺延）
python install.py --workspace-roots "D:\works,D:\projects"  # 注册工作区根（项目归类更准，之后也可在 UI/文件里改）
python install.py --no-hook                        # 不注册 Stop hook（仅手动索引）
python install.py --no-windows-snippet             # 不追加 Windows 避坑手册段
python install.py --print-plan                     # 只看计划不执行
```

## 装完先做一次

1. 双击 `<安装根>\launch_ui.vbs`，浏览器打开记忆基座 UI（`http://127.0.0.1:8643`）；
2. 第一次让 AI 干个小活并结束会话，Stop hook 会自动做首次会话索引；
3. 在任意 ZCode 会话里说「`$new-project`」或「搜一下我的历史会话」验证链路。

> 模型下载：知识层语义查重用 fastembed + bge-small-zh-v1.5。安装时若未带模型文件，首次检索会自动从国内镜像 `hf-mirror.com` 下载（约 90 MB，仅此一次），之后完全离线运行。

## 更新与卸载

```bash
git pull && python install.py     # 更新：重跑安装器即幂等升级（不碰你的数据）
python uninstall.py               # 摘除：MCP/hook/技能/AGENTS.md 段全部移除；安装根目录保留手动删
```

## 数据与隐私

- 你的会话索引、经验原子全部存放在安装根的 `data/` 与 `knowledge/` 下，**不出本机**；
- 想云备份经验原子：把安装根 `git init` 并配好远程即可，Stop hook 会自动 commit + push（代理环境设 `PAIOS_GIT_PROXY` 环境变量指向你的代理，如 `http://127.0.0.1:7890`；未设则直连）；
- 本仓库不含作者的任何个人数据（会话、原子、密钥），安装脚本也不会读取你 config.json 里的密钥，只做合并写入。

## 常见问题

- **8643 端口被占**：安装器自动顺延到下一个空闲端口，并同步写进 AGENTS.md 规则段；也可 `--port` 指定。
- **`paios_*` 工具没出现**：确认已完全重启 ZCode；`Settings → MCP` 里看 `paios` 是否已连接；`python <安装根>\mcp_entry.py` 手动跑一下看报错。
- **技能没触发**：`Settings → Skills` 确认技能已发现；技能名 `new-project` / `speckit-cn-*` / `confirm-vision` / `knowledge-atom`。
- **Stop hook 报错**：hook 设计为静默尽力而为，永不阻塞会话；stderr 里可看诊断。常见原因是 Python 不在 PATH。
- **换机器迁移**：拷贝安装根的 `data/` + `knowledge/` 到新机同位置，重跑 `python install.py` 即可。
