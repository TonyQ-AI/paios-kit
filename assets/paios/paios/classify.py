# -*- coding: utf-8 -*-
"""从路径推断项目归属的判定逻辑。

在三级判定基础上扩展（BLUEPRINT 6.1a-4 项目节点树）：
- 项目节点 = 含信号文件的目录（任意深度），不按固定层级
- 归属判定取最长前缀匹配；子目录无节点时向上回溯父节点
- 注册工作区（workspace-roots.json）下未命中节点时，退化用一级子目录名
"""
import re
from pathlib import Path

# 路径中 projects 目录段之后紧跟的组件即视为候选项目名（legacy 兼容）
PROJECTS_SEG = re.compile(
    r"(?:^|[\\/])projects[\\/]([^\\/:*?\"<>|]+)", re.IGNORECASE
)

# 文件扩展名（说明该组件是文件而不是项目目录）
_HAS_EXT = re.compile(r"\.[A-Za-z0-9]{1,10}$")

# 明确不是项目的目录名
SKIP_NAMES = {
    ".zcode", ".git", ".svn", ".venv", "venv", "__pycache__",
    "node_modules", "dist", "build", "_scratch", "docs",
}

# 需要忽略整个前缀的系统/工具目录（这些 paths 不参与投票）
SKIP_PREFIXES = (
    "c:\\users\\",       # 所有用户目录（含 AppData 等系统路径）
    "c:\\windows",
    "c:\\program files",
    "c:\\programdata",
)

# ---------------- 项目节点树（BLUEPRINT 6.1a-4） ----------------

# 标题/首条消息的 token 化（第 3 级关键词兜底用）
_TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff][\w\u4e00-\u9fff\-]*")

# 强信号：几乎必然是项目根
NODE_STRONG = ("agents.md", "blueprint.md", "pyproject.toml", "package.json",
               "cargo.toml", "go.mod")
# 弱信号：项目通常有；配合深度限制与跳过目录防误判
NODE_WEAK = ("readme.md", "说明.md")
NODE_STRONG_SUFFIX = (".csproj", ".sln")

# 节点扫描时跳过的子目录（避免扫进依赖/产物/素材海）
SKIP_SCAN_DIRS = SKIP_NAMES | {
    "src", "app", "lib", "libs", "tests", "test", "templates", "static",
    "assets", "vendor", "exec", "renders", "out", "bin", "obj", "packages",
    "ui", "config", "data", "backup", "knowledge", "atoms", "scripts",
    "episodes", "output", "tools", "cmd", "internal", "pkg", "_scratch",
}


def scan_project_nodes(roots, max_depth=3):
    """扫描工作区根，返回项目节点路径列表（原始大小写）。

    工作区根本身不参与信号判定（它只是「从哪找」的边界，不是项目）。
    """
    found = []

    def _walk(d, depth):
        if depth > max_depth:
            return
        try:
            entries = list(d.iterdir())
        except OSError:
            return
        names = {e.name.lower() for e in entries}
        strong = any(n in NODE_STRONG or n.endswith(NODE_STRONG_SUFFIX)
                     for n in names)
        weak = any(n in NODE_WEAK for n in names)
        if strong or weak:
            found.append(str(d))
        for e in entries:
            if e.is_dir() and e.name.lower() not in SKIP_SCAN_DIRS \
                    and not e.name.startswith((".", "$", "_")):
                _walk(e, depth + 1)

    for root in roots or []:
        rp = Path(root)
        if rp.is_dir():
            try:
                entries = list(rp.iterdir())
            except OSError:
                continue
            for e in entries:
                if e.is_dir() and e.name.lower() not in SKIP_SCAN_DIRS \
                        and not e.name.startswith((".", "$", "_")):
                    _walk(e, 1)
    # 去重（父子都可能是节点，保留全部，匹配时取最长）
    return sorted(set(found))


def _norm_nodes(nodes):
    """节点列表 → [(归一化路径, 原始路径)]，按长度降序（最长匹配优先）。"""
    out = []
    for n in nodes or []:
        nn = norm(n)
        if nn:
            out.append((nn, n))
    return sorted(out, key=lambda x: -len(x[0]))


def project_from_nodes(path, nodes_norm):
    """最长前缀匹配：返回 (项目显示名, 节点路径) 或 None。

    项目显示名取节点路径最后一段（节点唯一标识是完整路径，不会撞名）。
    """
    p = norm(path)
    if not p:
        return None
    for nn, orig in nodes_norm:
        if p == nn or p.startswith(nn + "\\"):
            tail = orig.rstrip("\\/").split("\\")[-1].split("/")[-1]
            return (tail, orig)
    return None


def norm(path: str):
    """规范化路径为小写反斜杠格式；非法输入返回 None。"""
    if not path or not isinstance(path, str):
        return None
    p = path.replace("/", "\\").strip().lower()
    if len(p) < 4 or ":" not in p:
        return None
    return p


def project_from_path(path: str):
    """legacy：返回 (项目名, 项目根路径)；不是 projects 下的路径则返回 None。"""
    p = norm(path)
    if not p or p.startswith(SKIP_PREFIXES):
        return None
    orig = path if len(path.strip()) == len(path) else path.strip()
    hit = None
    for m in PROJECTS_SEG.finditer(p):
        name = m.group(1).rstrip(".")
        if not name or _HAS_EXT.search(name) or name in SKIP_NAMES:
            continue
        if len(orig) == len(p):  # 索引安全时保留原始大小写
            hit = (orig[m.start(1): m.end(1)].rstrip("."),
                   orig[: m.start(1)].rstrip("\\"))
        else:
            hit = (name, p[: m.start(1)].rstrip("\\"))
    # 取最深一层匹配（嵌套 workspace 里再套 projects 的场景）
    return hit


def _registered_first_seg(path, registered_roots):
    """注册工作区下未命中节点时的兜底：一级子目录名视为项目（medium）。"""
    p = norm(path)
    if not p:
        return None
    for root in registered_roots or []:
        rn = norm(root)
        if not rn or not (p == rn or p.startswith(rn + "\\")):
            continue
        rest = p[len(rn):].lstrip("\\")
        seg = rest.split("\\", 1)[0] if rest else ""
        if seg and not _HAS_EXT.search(seg) and seg not in SKIP_NAMES:
            orig = path.replace("/", "\\")
            on = norm(orig)
            idx = on.find(rn)
            seg_start = idx + len(rn) + 1
            seg_end = on.find("\\", seg_start)
            seg_orig = orig[seg_start: seg_end if seg_end >= 0 else len(orig)]
            return (seg_orig, (root.rstrip("\\/") + "\\" + seg_orig))
    return None


def collect_roots(directories):
    """从会话目录集合中归纳出所有 projects 根目录（形如 D:\\works\\projects）。"""
    roots = set()
    for d in directories:
        n = norm(d)
        if not n:
            continue
        for m in PROJECTS_SEG.finditer(n):
            root = n[: m.start(1)].rstrip("\\")
            if len(root) > 3:
                roots.add(root)
    return sorted(roots)


def list_project_names(roots):
    """枚举各 roots 下的一级子文件夹名（标题关键词兜底匹配用）。"""
    names = {}
    for root in roots:
        rp = Path(root)
        try:
            for child in rp.iterdir():
                if child.is_dir() and child.name.lower() not in SKIP_NAMES \
                        and not child.name.startswith((".", "$")):
                    names.setdefault(child.name.lower(), str(child))
        except OSError:
            continue
    return names


def classify_session(info, tool_paths, roots, folder_names,
                     nodes=None, registered_roots=None):
    """会话→项目 归属判定（PAIOS 版）。

    1) 工作目录命中项目节点（最长前缀）或 projects 段直推 → high
    2) 文件操作路径投票（节点命中与 legacy projects 命中均计票）→ 票>=5 high，否则 medium
    3) 标题/首条消息 token 恰为项目文件夹名 → medium(唯一)/low(多选)
    4) 注册工作区下一级子目录兜底 → medium
       全部落空 → '未归类' low
    """
    nodes_norm = _norm_nodes(nodes)

    # 1) 工作目录直推
    node_hit = project_from_nodes(info.get("directory") or "", nodes_norm)
    if node_hit:
        return {"project_key": node_hit[0], "project_path": node_hit[1],
                "confidence": "high"}
    cand = project_from_path(info.get("directory") or "")
    if cand:
        return {"project_key": cand[0], "project_path": cand[1], "confidence": "high"}

    # 2) 文件操作投票
    votes = {}
    paths = {}
    lowered = []
    for p in tool_paths:
        lowered.append(norm(p) or "")
        for get in (lambda x: project_from_nodes(x, nodes_norm),
                    lambda x: project_from_path(x)):
            hit = get(p)
            if hit:
                votes[hit[0]] = votes.get(hit[0], 0) + 1
                paths.setdefault(hit[0], hit[1])
    if votes:
        best = max(votes, key=votes.get)
        return {"project_key": best, "project_path": paths.get(best, ""),
                "confidence": "high" if votes[best] >= 5 else "medium"}

    # 3) 标题关键词兜底
    text = ((info.get("title") or "") + " "
            + (info.get("_first_msg") or "")).lower()
    hits = {tok for tok in _TOKEN_RE.findall(text)
            if len(tok) >= 4 and tok in folder_names}
    if hits:
        t = (info.get("title") or "").lower()
        ordered = sorted(hits, key=lambda h: h not in t)  # 标题中出现的优先
        pick = ordered[0]
        return {"project_key": pick, "project_path": folder_names[pick],
                "confidence": "medium" if len(hits) == 1 else "low"}

    # 4) 注册工作区一级子目录兜底
    seg = _registered_first_seg(info.get("directory") or "", registered_roots)
    if seg:
        return {"project_key": seg[0], "project_path": seg[1], "confidence": "medium"}

    d = norm(info.get("directory") or "")
    return {"project_key": "未归类", "project_path": d or "",
            "confidence": "low"}
