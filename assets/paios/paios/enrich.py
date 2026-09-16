# -*- coding: utf-8 -*-
"""从项目 README 中提取一句话功能描述 + 项目完成度评估。"""
import re
from pathlib import Path

README_NAMES = ("readme.md", "readme", "readme.mdown", "说明.md")

# 入口文件：不限定具体文件名，而是检测「项目配置文件」和「根级源码」
ENTRY_CONFIGS = (
    "*.sln", "*.csproj",          # .NET
    "Makefile", "makefile",        # C/C++
    "go.mod",                      # Go
    "Cargo.toml",                  # Rust
    "pyproject.toml", "setup.py",  # Python
    "package.json",                # Node
    "pom.xml", "build.gradle",     # Java
    "Gemfile",                     # Ruby
    "composer.json",               # PHP
)
ENTRY_SOURCE_EXTS = (
    ".py", ".js", ".ts", ".tsx", ".vue", ".go", ".rs",
    ".cs", ".java", ".rb", ".php", ".swift", ".kt",
)
# 测试标记
TEST_MARKERS = ("tests", "test", "__tests__", "spec", "pytest.ini", "jest.config",
                "vitest.config", ".mocharc", "phpunit.xml", ".nunit")
# 打包/发布标记
PACKAGING_MARKERS = (
    "setup.py", "setup.cfg", "pyproject.toml", "MANIFEST.in",
    "package.json", "Cargo.toml", "go.mod", "pom.xml", "build.gradle",
    "Dockerfile", "docker-compose.yml", "docker-compose.yaml",
    ".github/workflows", "Makefile",
)
PACKAGING_DIRS = ("dist", "build", "release", "out", "bin", "obj", ".next", ".output")
# 依赖文件 + 目录
DEP_MARKERS = (
    "requirements.txt", "requirements-dev.txt", "Pipfile", "poetry.lock",
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "go.sum", "Cargo.lock", "composer.lock", "Gemfile.lock",
)
DEP_DIRS = ("node_modules", ".venv", "venv", "env", "packages", ".nuget")

# 项目类型分类规则（按优先级）
TYPE_RULES = [
    # (类型ID, 类型标签, 检测函数)
    ("desktop-app",   "本地桌面应用",  lambda n, d: bool(
        any(any(x in fn for x in (".sln", ".csproj", "app.xaml")) for fn in n) or
        "electron" in d or "src-tauri" in d or
        "cargo.toml" in n or "pubspec.yaml" in n)),
    ("pyinstaller-gui", "本地桌面应用", lambda n, d: bool(
        # PyInstaller 打包现场：.spec 清单 / .pyw 无窗入口 / .ico 图标
        any(x.endswith((".spec", ".pyw", ".ico")) for x in n))),
    ("python-gui",    "Python桌面GUI", lambda n, d: bool(
        "requirements.txt" in n and any(x in n for x in (".ico", "gui.py", "tray.py")) and
        "templates" not in d)),
    ("python-web",    "Python Web",    lambda n, d: bool(
        ("templates" in d or "static" in d) and "package.json" not in n)),
    ("python-tool",   "Python工具",    lambda n, d: bool(
        any(x.endswith(".py") for x in n) and
        "package.json" not in n and
        "templates" not in d and "static" not in d)),
    ("web-app",       "Web应用",       lambda n, d: bool(
        ("package.json" in n and (
            "vite.config" in n or "next.config" in n or "webpack.config" in n or
            "src" in d or "app" in d or "public" in d or "server" in d or
            "index.html" in n))
        or ("web" in d and "server" in d))),
    ("api-server",    "API服务",       lambda n, d: bool(
        "go.mod" in n or ("main.go" in n) or "docker-compose.yml" in n)),
    ("mcp-tool",      "MCP工具",       lambda n, d: bool(
        "package.json" in n and "tsconfig.json" in n and "vite.config" not in n)),
    ("docs",          "文档/设计",      lambda n, d: bool(
        not any(x in n for x in ("package.json", "requirements.txt", "go.mod",
                                  "cargo.toml", ".sln", "pubspec.yaml",
                                  "app.py", "main.py", "index.js")) and
        not any(x.endswith((".py", ".js", ".ts", ".go", ".cs", ".rs", ".vue")) for x in n) and
        any(x.endswith((".md", ".mdx")) for x in n))),
]


def classify_type(project_path: str) -> str:
    """根据项目目录结构判断类型，返回类型标签。"""
    if not project_path:
        return "其他"
    p = Path(project_path.replace("/", "\\"))
    if not p.is_dir():
        return "其他"
    names = set()
    dirs = set()
    try:
        for f in p.iterdir():
            names.add(f.name.lower())
            if f.is_dir():
                dirs.add(f.name.lower())
    except OSError:
        return "其他"
    for type_id, label, check in TYPE_RULES:
        try:
            if check(names, dirs):
                return label
        except Exception:
            continue
    return "其他"


# 粗粒度应用类型：把技术栈向的细分类收敛为用户视角的少数几类
COARSE_MAP = {
    "本地桌面应用": "桌面应用",
    "Python桌面GUI": "桌面应用",
    "Python Web": "Web应用",
    "Web应用": "Web应用",
    "API服务": "Web应用",
    "Python工具": "工具/脚本",
    "MCP工具": "工具/脚本",
    "文档/设计": "文档/资料",
}
COARSE_ORDER = ["桌面应用", "Web应用", "工具/脚本", "文档/资料", "其他", "未归类"]


def coarse_type(type_label: str) -> str:
    """细粒度类型标签 → 粗粒度应用类型。空标签（无项目路径）= 未归类。"""
    t = (type_label or "").strip()
    if not t:
        return "未归类"
    return COARSE_MAP.get(t, "其他")


def read_project_desc(project_path: str, project_key: str = "") -> str:
    """读取项目目录下的 README，提取第一个有意义的段落（≤120字）。

    返回空字符串表示未找到或内容为空。
    project_path 可能只到 projects 根目录，此时用 project_key 拼接子目录再试。
    """
    if not project_path:
        return ""
    p = Path(project_path.replace("/", "\\"))
    if not p.is_dir():
        return ""
    # 先在 project_path 下找，找不到且有 project_key 时试子目录
    desc = _try_read(p)
    if not desc and project_key:
        sub = p / project_key
        if sub.is_dir():
            desc = _try_read(sub)
    return desc


def _try_read(p: Path) -> str:
    """在目录 p 下找 README 并提取描述。"""
    readme = None
    for name in README_NAMES:
        candidate = p / name
        if candidate.is_file():
            readme = candidate
            break
    if readme is None:
        docs = p / "docs"
        if docs.is_dir():
            for name in README_NAMES:
                candidate = docs / name
                if candidate.is_file():
                    readme = candidate
                    break
    if readme is None:
        return ""
    try:
        text = readme.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    return _extract_first_paragraph(text)


def _extract_first_paragraph(text: str) -> str:
    """从 Markdown 文本中提取第一个有意义的段落。

    跳过：标题行(#开头)、空行、badge/shield 图片行、HTML 标签行。
    取到的第一个非空段落截断到 120 字。
    """
    lines = text.splitlines()
    paragraph = []
    collecting = False
    for line in lines:
        stripped = line.strip()
        # 跳过空行（如果还没开始收集，继续跳）
        if not stripped:
            if collecting:
                break  # 段落结束
            continue
        # 跳过纯标题行
        if re.match(r"^#{1,6}\s", stripped):
            if collecting:
                break
            continue
        # 跳过 badge/shield 图片
        if re.match(r"!\[.*\]\(https?://", stripped) or "badge" in stripped.lower():
            continue
        # 跳过 HTML 标签行
        if re.match(r"^</?[a-zA-Z]", stripped):
            continue
        # 跳过分割线
        if re.match(r"^[-*_]{3,}$", stripped):
            continue
        # 跳过纯链接行
        if re.match(r"^\[.*\]\(https?://", stripped) and not collecting:
            continue
        # 去掉行内 Markdown 图片
        cleaned = re.sub(r"!\[.*?\]\(.*?\)", "", stripped).strip()
        if not cleaned:
            continue
        collecting = True
        paragraph.append(cleaned)
    if not paragraph:
        return ""
    result = " ".join(paragraph)
    if len(result) > 120:
        result = result[:117] + "…"
    return result


def assess_project(project_path: str) -> dict:
    """评估项目完成度，返回 {level, label, icon, score, details}。

    level: 'done' | 'wip' | 'draft'
    score: 0~100 分
    """
    if not project_path:
        return {"level": "unknown", "label": "未归类", "icon": "❓", "score": 0, "details": []}
    p = Path(project_path.replace("/", "\\"))
    if not p.is_dir():
        return {"level": "unknown", "label": "路径不存在", "icon": "❓", "score": 0, "details": []}

    # 收集目录下一层所有文件和目录名
    names = set()
    dirs = set()
    try:
        for f in p.iterdir():
            names.add(f.name.lower())
            if f.is_dir():
                dirs.add(f.name.lower())
    except OSError:
        pass

    score = 0
    details = []

    # 1. 有文档 (+15)
    if any(n.startswith("readme") for n in names):
        score += 15
        details.append("✅文档")

    # 2. 有可运行入口/项目配置 (+20)
    #    方式A：匹配已知项目配置文件（.sln/.csproj/go.mod/Cargo.toml/package.json 等）
    has_config_file = any(
        any(Path(n).match(pat) for pat in ENTRY_CONFIGS)
        for n in names
    )
    #    方式B：根目录或 src/ 下有源码文件
    has_root_source = any(n.endswith(ENTRY_SOURCE_EXTS) for n in names)
    src_dirs = {"src", "app", "lib", "cmd", "internal", "pkg"}
    has_src_source = False
    for sd in src_dirs:
        if sd in dirs:
            try:
                for f in (p / sd).iterdir():
                    if f.name.lower().endswith(ENTRY_SOURCE_EXTS):
                        has_src_source = True
                        break
            except OSError:
                pass
            if has_src_source:
                break
    #    方式C：有可执行脚本
    has_script = any(n.endswith((".sh", ".cmd", ".bat", ".ps1")) for n in names)

    if has_config_file or has_root_source or has_src_source or has_script:
        score += 20
        details.append("✅入口")

    # 3. 有测试 (+15)
    if any(any(t in n for t in TEST_MARKERS) for n in names | dirs):
        score += 15
        details.append("✅测试")

    # 4. 有打包/CI (+15)
    has_packaging = any(any(pk in n for pk in PACKAGING_MARKERS) for n in names)
    has_pkg_dir = bool(dirs & set(PACKAGING_DIRS))
    if has_packaging or has_pkg_dir:
        score += 15
        details.append("✅发布" + ("📦" if has_pkg_dir else ""))

    # 5. 有依赖管理 (+10)
    has_dep_file = any(any(d in n for d in DEP_MARKERS) for n in names)
    has_dep_dir = bool(dirs & set(DEP_DIRS))
    # .NET 项目：.csproj 内含 PackageReference，也算有依赖
    has_csproj = any(n.endswith(".csproj") for n in names)
    if has_dep_file or has_dep_dir or has_csproj:
        score += 10
        details.append("✅依赖")

    # 6. 代码量估算 (+15)
    code_exts = ENTRY_SOURCE_EXTS + (".jsx", ".mjs", ".cjs", ".c", ".cpp", ".h")
    code_files = sum(1 for n in names if any(n.endswith(e) for e in code_exts))
    # 也扫 src/ 等目录（含子目录）
    for sd in ("src", "app", "lib", "cmd", "internal", "pkg"):
        if sd in dirs:
            try:
                for f in (p / sd).rglob("*"):
                    if f.is_file() and f.name.lower().endswith(code_exts):
                        code_files += 1
            except OSError:
                pass
    if code_files >= 5:
        score += 15
        details.append(f"✅代码({code_files}文件)")
    elif code_files >= 1:
        score += 8
        details.append(f"🟡代码({code_files}文件)")

    # 7. 有构建/配置文件 (+10)
    build_configs = {
        "config.json", "config.yaml", "config.yml", ".env", ".env.example",
        "tsconfig.json", "vite.config.js", "vite.config.ts",
        "webpack.config.js", ".eslintrc", ".prettierrc", ".editorconfig",
        "launchsettings.json", "appsettings.json", "appsettings.Development.json",
        ".gitignore", ".dockerignore", "docker-compose.yml", "docker-compose.yaml",
    }
    if names & build_configs:
        score += 10
        details.append("✅配置")

    # 定级
    if score >= 70:
        level, label, icon = "done", "可运行", "🟢"
    elif score >= 40:
        level, label, icon = "wip", "开发中", "🟡"
    else:
        level, label, icon = "draft", "草稿", "🔴"

    return {"level": level, "label": label, "icon": icon, "score": score, "details": details}
