# -*- coding: utf-8 -*-
"""知识层：经验原子的写入门禁、读写、检索与索引重建。

红线（BLUEPRINT 3.2）：
- 适用边界必填，禁止「通用/都适用」空边界
- 【AI错误】没有就写「无」，禁止编造踩坑记录
- status=draft 不参与检索；转正（draft→active）是人工动作
- 同标题查重，拒绝重复建档
"""
import os
import re
import time
from pathlib import Path

from . import ATOMS_DIR, INDEX_MD, DATA_DIR

ATOM_TYPES = ("踩坑", "方案", "约束", "决策", "工作流")
REQUIRED_FIELDS = ("任务背景", "人的判断", "判断理由", "AI错误", "修正方案", "最终结果", "适用边界")
GENERIC_BOUNDARY = {"通用", "都适用", "所有场景", "不限", "无"}
LOCK_FILE = DATA_DIR / "atoms.lock"

try:                       # 语义通道阈值来自向量引擎；引擎不可用时用默认
    from .vectors import SEM_WARN
except Exception:
    SEM_WARN = 0.82

# 人工晋升全局规则（BLUEPRINT：原子=草稿池，反复命中由人晋升为规则）
AGENTS_MD = Path.home() / ".zcode" / "AGENTS.md"
RULE_SECTION = "## 知识原子晋升规则（人工晋升自 PAIOS 知识层）"

_FRONT_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)
_FIELD_RE = re.compile(r"【([^】]+)】([^【]*)")
_SLUG_RE = re.compile(r'[\\/:*?"<>|\s]+')


# ---------------------------------------------------------------- 并发锁

class _FileLock:
    """写操作互斥（hook 与 UI 并发场景兜底）。"""

    def __init__(self, timeout=5.0):
        self.timeout = timeout
        self.fd = None

    def __enter__(self):
        start = time.time()
        while True:
            try:
                self.fd = os.open(str(LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                return self
            except FileExistsError:
                if time.time() - start > self.timeout:  # 僵死锁强抢
                    try:
                        LOCK_FILE.unlink()
                    except OSError:
                        pass
                    start = time.time()
                time.sleep(0.05)

    def __exit__(self, *a):
        try:
            os.close(self.fd)
            LOCK_FILE.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------- 解析

def parse_frontmatter(text):
    m = _FRONT_RE.match(text)
    meta, body = {}, text
    if m:
        body = text[m.end():]
        for line in m.group(1).splitlines():
            if ":" not in line:
                continue
            k, v = line.split(":", 1)
            v = v.strip()
            if v.startswith("[") and v.endswith("]"):
                v = [x.strip() for x in v[1:-1].split(",") if x.strip()]
            meta[k.strip()] = v
    return meta, body


def dump_frontmatter(meta):
    lines = ["---"]
    for k, v in meta.items():
        if isinstance(v, list):
            lines.append("%s: [%s]" % (k, ", ".join(v)))
        else:
            lines.append("%s: %s" % (k, v))
    return "\n".join(lines) + "\n" + "---\n"


def extract_fields(body):
    return {name.strip(): content.strip() for name, content in _FIELD_RE.findall(body or "")}


# ---------------------------------------------------------------- 门禁

def _check_boundary(fields):
    b = fields.get("适用边界", "")
    if not b:
        return "【适用边界】缺失"
    if b.rstrip("。.").strip() in GENERIC_BOUNDARY:
        return "【适用边界】为空泛表述（%s），必须写具体场景" % b.strip()
    return None


def gate_check(title, atom_type, body):
    """save_atom 前置门禁。返回拒绝原因列表（空列表=通过）。"""
    reasons = []
    if not (title or "").strip():
        reasons.append("缺少标题")
    if (atom_type or "").strip() not in ATOM_TYPES:
        reasons.append("类型必须是 %s 之一，收到：%s" % ("/".join(ATOM_TYPES), (atom_type or "").strip() or "空"))
    fields = extract_fields(body or "")
    for f in REQUIRED_FIELDS:
        if f not in fields:
            reasons.append("缺少字段【%s】" % f)
    if "AI错误" in fields and not fields["AI错误"]:
        reasons.append("【AI错误】为空：没有踩坑请写「无」，禁止留空")
    b = _check_boundary(fields)
    if b:
        reasons.append(b)
    return reasons


# ---------------------------------------------------------------- 读写

def _slug(title):
    s = _SLUG_RE.sub("-", title.strip())[:30].strip("-.")
    return s or "untitled"


def _atom_file(aid):
    for p in sorted(ATOMS_DIR.glob(aid + "-*.md")):
        return p
    return None


def next_id():
    nums = []
    if ATOMS_DIR.is_dir():
        for p in ATOMS_DIR.glob("A-*.md"):
            m = re.match(r"A-(\d+)", p.name)
            if m:
                nums.append(int(m.group(1)))
    return "A-%04d" % (max(nums) + 1 if nums else 1)


def list_atoms():
    """全部原子 [(meta, body, path)]，按 id 排序。"""
    out = []
    if not ATOMS_DIR.is_dir():
        return out
    for p in sorted(ATOMS_DIR.glob("A-*.md")):
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        meta, body = parse_frontmatter(text)
        out.append((meta, body, p))
    return out


def save_atom(title, atom_type, body, tags=None, project="", source="zcode-session",
              source_ref="", status="draft", date=None, force=False, reviewed=None):
    """写入一条经验原子（门禁+软查重）。返回 (ok, id 或拒绝原因列表)。

    词面相似度 >= SIM_WARN 时软拦截。查重流程强制：调用方须先检索并阅读
    相关原子，把已判断非重复的 ID 填入 reviewed（证据落参）；确属重复则勿
    入库；确非重复可 force=true 越过。语义级重复（跨措辞/跨语言）由写入前
    的检索判断与人工审阅兜底。
    """
    reasons = gate_check(title, atom_type, body)
    if reasons:
        return False, reasons

    with _FileLock():
        norm = re.sub(r"\s+", "", (title or "").lower())
        for meta, b, _p in list_atoms():
            if re.sub(r"\s+", "", (meta.get("title") or "").lower()) == norm:
                return False, ["已存在同标题原子 %s（查重拒绝）" % meta.get("id")]
        sims = find_similar(title, body, tags, top=5)
        # 通道感知阈值：词面 30% 即拦（词面高=几乎必重复），语义 82% 才拦（给同族不同场景留空间）
        hits = [(s, m, b, ch) for s, m, b, ch in sims
                if (ch == "lex" and s >= SIM_WARN) or (ch == "sem" and s >= SEM_WARN)]
        if hits and not force:
            reviewed_set = set(reviewed or [])
            unreviewed = [(s, m, ch) for s, m, b, ch in hits if m.get("id") not in reviewed_set]
            if unreviewed:
                s, m, ch = unreviewed[0]
                chan = "语义" if ch == "sem" else "词面"
                return False, [
                    "疑似与 %s「%s」重复（%s相似度 %.0f%%）。查重流程强制：先 "
                    "paios_search_atoms 检索并阅读相关原子，把已阅读且判断非重复的 ID "
                    "填入 reviewed 参数后重新提交；确属重复则勿入库；确非重复可 force=true"
                    % (m.get("id"), m.get("title"), chan, s * 100)]
        aid = next_id()
        meta = {
            "id": aid,
            "title": title.strip(),
            "type": atom_type.strip(),
            "tags": [t.strip() for t in (tags or []) if t.strip()],
            "project": (project or "").strip(),
            "source": source or "zcode-session",
            "source_ref": source_ref or "",
            "date": date or time.strftime("%Y-%m-%d"),
            "status": status if status in ("draft", "active") else "draft",
        }
        path = ATOMS_DIR / ("%s-%s.md" % (aid, _slug(title)))
        path.write_text(dump_frontmatter(meta) + "\n" + (body or "").strip() + "\n",
                        encoding="utf-8", newline="\n")
    rebuild_index()
    return True, aid


def set_status(aid, status):
    """状态流转：draft→active（转正）、draft→deprecated（草稿丢弃）、
    active→deprecated（废弃）、deprecated→active（恢复）、
    active|deprecated→draft（撤回转正/废弃，回到待审草稿——可回滚的关键）。"""
    if status not in ("draft", "active", "deprecated"):
        return False, "非法状态 %s" % status
    with _FileLock():
        p = _atom_file(aid)
        if not p:
            return False, "找不到原子 %s" % aid
        meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
        pair = (meta.get("status"), status)
        # 允许回到 draft（撤回转正/废弃决定），中途状态不丢、内容不动
        if pair not in (("draft", "active"), ("draft", "deprecated"),
                        ("active", "deprecated"), ("deprecated", "active"),
                        ("active", "draft"), ("deprecated", "draft")):
            return False, "非法流转 %s → %s" % pair
        meta["status"] = status
        p.write_text(dump_frontmatter(meta) + "\n" + body, encoding="utf-8", newline="\n")
    rebuild_index()
    return True, aid


# ---------------------------------------------------------------- 检索

def search_atoms(q, limit=10):
    """关键词检索：多词 AND，无结果降级首词。deprecated/draft 均不参与检索。

    相关性评分排序（省 token 的关键：让 top-1 就是员工最想要的，精读 1 条即可）：
    标题命中 ×5 > 标签 ×3 > 一句话结论 ×2 > 正文 ×1，按总分降序。
    """
    tokens = [t for t in (q or "").split() if t]
    if not tokens:
        return []

    def _fields(meta, body):
        tags = meta.get("tags")
        return (
            meta.get("title", "").lower(),
            " ".join(tags).lower() if isinstance(tags, list) else str(tags or "").lower(),
            _one_liner(body).lower(),
            (body or "").lower(),
        )

    def _score(meta, body):
        title, tag_s, concl, body_l = _fields(meta, body)
        score = 0
        for t in tokens:
            tl = t.lower()
            if tl in title:
                score += 5
            if tl in tag_s:
                score += 3
            if tl in concl:
                score += 2
            if tl in body_l:
                score += 1
        return score

    hits = []
    for meta, body, path in list_atoms():
        if meta.get("status") in ("deprecated", "draft"):
            continue
        hay = " ".join(_fields(meta, body))
        if all(t.lower() in hay for t in tokens):
            hits.append((_score(meta, body), meta.get("id") or "", meta, body))
    if not hits and len(tokens) > 1:
        return search_atoms(tokens[0], limit)
    hits.sort(key=lambda x: (-x[0], x[1]))
    return [(meta, body) for _s, _i, meta, body in hits[:limit]]


def count_by_project():
    """按 project 字段统计在役原子数（项目总览知识存量列用）。"""
    out = {}
    for meta, body, path in list_atoms():
        if meta.get("status") == "deprecated":
            continue
        pr = (meta.get("project") or "").strip()
        if pr:
            out[pr] = out.get(pr, 0) + 1
    return out


def read_push_fail_note():
    """原子库推送失败便签（Stop hook 写入；空串=推送正常）。"""
    p = DATA_DIR / "atoms-push-failed.txt"
    try:
        return p.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


# ---------------------------------------------------------------- 查重

def _sim_tokens(text):
    """标题+正文的相似度 token：ASCII 词 + 中文二元组。"""
    toks = re.findall(r"[a-z0-9]+", (text or "").lower())
    for run in re.findall(r"[\u4e00-\u9fff]+", text or ""):
        toks += [run[i:i + 2] for i in range(len(run) - 1)]
    return set(toks)


def _similarity(meta_a, body_a, meta_b, body_b):
    """0~1：标题 60% + 正文 25% + 标签 15%。词面相似度，语义重复仍需 LLM/人判断。"""
    ja = _sim_tokens(meta_a.get("title", ""))
    jb = _sim_tokens(meta_b.get("title", ""))
    s_title = len(ja & jb) / len(ja | jb) if (ja | jb) else 0
    ba = _sim_tokens(meta_a.get("title", "") + " " + body_a)
    bb = _sim_tokens(meta_b.get("title", "") + " " + body_b)
    s_body = len(ba & bb) / len(ba | bb) if (ba | bb) else 0
    ta = set(meta_a.get("tags") or [])
    tb = set(meta_b.get("tags") or [])
    s_tag = len(ta & tb) / len(ta | tb) if (ta | tb) else 0
    return 0.6 * s_title + 0.25 * s_body + 0.15 * s_tag


SIM_WARN = 0.30  # 词面相似告警阈值（软门禁，force 可越过）


def find_similar(title, body, tags=None, exclude_id=None, top=5):
    """双通道相似：词面（标题/正文/标签）∪ 语义（向量），各取高分合并。

    返回 [(score, meta, body, channel)] 降序；channel: 'lex'|'sem'。
    """
    entries = list_atoms()
    scored = {}
    for meta, b, path in entries:
        if exclude_id and meta.get("id") == exclude_id:
            continue
        s = _similarity({"title": title, "tags": tags or []}, body or "", meta, b)
        if s > 0.05:
            scored[meta.get("id")] = (s, meta, b, "lex")
    try:
        from . import vectors
        vecs = vectors.atom_vectors(entries)
        for s, meta, b in vectors.semantic_similar(title, body, entries, vecs,
                                                   top=10, exclude_id=exclude_id):
            if s > 0.35 and meta.get("id") not in scored:
                scored[meta.get("id")] = (s, meta, b, "sem")
            elif s > 0.35 and scored[meta.get("id")][3] == "lex" and s > scored[meta.get("id")][0]:
                scored[meta.get("id")] = (s, meta, b, "sem")
    except Exception:
        pass  # 向量引擎不可用时词面层照常工作
    ranked = sorted(scored.values(), key=lambda x: -x[0])
    return ranked[:top]


def similar_atoms(aid, top=5):
    """已有原子找相似（UI 查重/审阅合并用）。"""
    for meta, body, path in list_atoms():
        if meta.get("id") == aid:
            return [(round(s, 2), m, b, ch) for s, m, b, ch in
                    find_similar(meta.get("title", ""), body,
                                 meta.get("tags"), exclude_id=aid, top=top)]
    return None


# ---------------------------------------------------------------- 同族关联与合并

def link_family(aid, other_id, note=""):
    """双向标注「同族但不同场景」：原子 frontmatter 写 family 列表。"""
    with _FileLock():
        ids = [x for x in (aid, other_id) if _atom_file(x)]
        if len(ids) != 2:
            return False, "找不到原子：%s" % ",".join(set((aid, other_id)) - set(ids))
        for x in ids:
            p = _atom_file(x)
            meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
            fam = meta.get("family") or []
            if isinstance(fam, str):
                fam = [fam]
            other = other_id if x == aid else aid
            if other not in fam:
                fam.append(other)
            meta["family"] = fam
            if note and x == aid:  # 关联说明只写主标注方
                meta["family_note"] = note
            _write_atom(p, meta, body)
    rebuild_index()
    return True, "%s <-> %s" % (aid, other_id)


def delete_atoms(ids):
    """彻底删除（硬删文件），仅允许已废弃状态——防误删在役知识。"""
    deleted, errors = [], []
    with _FileLock():
        for aid in ids:
            p = _atom_file(aid)
            if not p:
                errors.append("%s 不存在" % aid)
                continue
            meta, _b = parse_frontmatter(p.read_text(encoding="utf-8"))
            if meta.get("status") != "deprecated":
                errors.append("%s 未废弃，禁止删除（先标记废弃）" % aid)
                continue
            p.unlink()
            deleted.append(aid)
    rebuild_index()
    return (not errors), {"deleted": deleted, "errors": errors}


def split_atom(aid, parts):
    """拆分复合原子：parts = [{title,type,body,tags,project,...}]，逐条建档。

    新原子继承原原子状态；原原子废弃并标 split_into=[新id...]（溯源不断）。
    """
    p = _atom_file(aid)
    if not p:
        return False, "找不到原子 %s" % aid
    if not parts or not isinstance(parts, list):
        return False, "parts 为空：拆分至少要给出一条子原子"
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    inherit_status = meta.get("status", "draft")
    new_ids = []
    for part in parts:
        ok, r = save_atom(
            title=str(part.get("title") or ""),
            atom_type=str(part.get("type") or meta.get("type", "踩坑")),
            body=str(part.get("body") or ""),
            tags=part.get("tags") or meta.get("tags") or [],
            project=str(part.get("project") or meta.get("project", "")),
            source=meta.get("source", "zcode-session"),
            source_ref=(meta.get("source_ref") or "") + " (split from %s)" % aid,
            status=inherit_status,
            force=True, reviewed="split",
        )
        if not ok:
            return False, ["拆分子原子被拒：%s" % r] + (r if isinstance(r, list) else [str(r)])
        new_ids.append(r)
    with _FileLock():
        p = _atom_file(aid)
        meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
        meta["status"] = "deprecated"
        meta["split_into"] = new_ids
        _write_atom(p, meta, body)
    rebuild_index()
    return True, new_ids


def _content_units(body):
    """把原子正文拆成 (kind, sentence) 内容单元：core=一句话结论、boundary=适用边界、
    field:XX=其他字段句。合并记录块是溯源不算内容，跳过。"""
    units = []
    clean = re.sub(r"【合并记录（[^）]*）】[\s\S]*?(?=\n【|\Z)", "", body or "")
    ol = _one_liner(clean)
    for s in re.split(r"[。；]", ol):
        s = s.strip()
        if len(s) >= 4:
            units.append(("core", s))
    for k, v in (extract_fields(clean) or {}).items():
        if k == "一句话结论":
            continue
        kind = "boundary" if k == "适用边界" else "field:" + k
        for s in re.split(r"[。；]", v or ""):
            s = s.strip()
            if len(s) >= 6:
                units.append((kind, s))
    return units


def _unit_covered(text, primary_tokens, threshold=0.6):
    """单元文本被主条词面覆盖 ≥threshold 视为共享句。"""
    toks = _sim_tokens(text)
    if not toks:
        return True
    return len(toks & primary_tokens) / len(toks) >= threshold


def merge_atoms(primary_id, absorbed_ids, merged_body=None, merged_title=None,
                merged_tags=None):
    """部分合并：只把相似度高的共享句并入主条【合并记录】。

    - 逐句判定：被主条覆盖 ≥60% 的句 = 共享句 → 记入主条合并记录
    - 被并条含独有句 → 保留在役（状态不变），frontmatter 标 partial_merged_into
      并联动同族，正文不动（独有内容继续可检索）
    - 被并条无独有内容 → 整条废弃（旧行为，标 merged_into）
    - merged_body 覆盖 / 并入方向守卫 / 重复合并跳过：同前
    """
    def _num(aid):
        m = re.match(r"A-(\d+)", aid or "")
        return int(m.group(1)) if m else -1
    pnum = _num(primary_id)
    if pnum >= 0 and any(_num(x) < pnum for x in absorbed_ids):
        bad = [x for x in absorbed_ids if _num(x) < pnum]
        return False, "并入方向违规：后来者并入先来者，%s 早于 %s，不能被并入" % (",".join(bad), primary_id)
    with _FileLock():
        pp = _atom_file(primary_id)
        if not pp:
            return False, "找不到主原子 %s" % primary_id
        pmeta, pbody = parse_frontmatter(pp.read_text(encoding="utf-8"))
        # 查重：已并入过的不重复处理（防重复合并记录 / merged 元数据翻倍）
        already = [x for x in absorbed_ids if x in (pmeta.get("merged") or [])]
        todo = [x for x in absorbed_ids if x not in already]
        if not todo:
            return True, "%s 已合并过（%s），本次无操作" % (primary_id, ",".join(already))
        if merged_body:
            pbody = merged_body
            survivors = {}
        else:
            p_tokens = _sim_tokens(pbody)
            extra, survivors = [], {}
            for x in todo:
                ap = _atom_file(x)
                if not ap:
                    return False, "找不到被并原子 %s" % x
                ameta, abody = parse_frontmatter(ap.read_text(encoding="utf-8"))
                units = _content_units(abody)
                covered = [(k, s) for k, s in units if _unit_covered(s, p_tokens)]
                unique = [(k, s) for k, s in units if (k, s) not in covered]
                if not unique:
                    # 无独有内容 → 整条并入（废弃）：记录结论+边界
                    m = re.search(r"【一句话结论】\s*(.+)", abody)
                    extra.append("- %s（%s）：%s\n  场景边界：%s" % (
                        ameta.get("title"), ameta.get("id"),
                        m.group(1).strip() if m else "",
                        extract_fields(abody).get("适用边界", "")[:120]))
                else:
                    # 部分并入：只记共享句，被并条保留在役
                    survivors[x] = (ameta, abody, len(covered), len(unique))
                    core_sh = [s for k, s in covered if k == "core"]
                    bound_sh = [s for k, s in covered if k == "boundary"]
                    if core_sh or bound_sh:
                        extra.append("- %s（%s）：%s\n  场景边界：%s" % (
                            ameta.get("title"), ameta.get("id"),
                            "。".join(core_sh) or "（结论句独有，见本条）",
                            "。".join(bound_sh)[:120] or "（边界独有，见本条）"))
            if extra:  # 被并条无内容时不写空头记录
                pbody = pbody.rstrip() + ("\n\n【合并记录（同族不同场景，%s）】\n%s\n"
                                          % (time.strftime("%Y-%m-%d"), "\n".join(extra)))
        pmeta["merged"] = list(dict.fromkeys((pmeta.get("merged") or []) + todo))
        if merged_title:
            pmeta["title"] = merged_title
        if merged_tags:
            pmeta["tags"] = merged_tags
        _write_atom(pp, pmeta, pbody)
        full_merged = []
        for x in todo:
            ap = _atom_file(x)
            ameta, abody = parse_frontmatter(ap.read_text(encoding="utf-8"))
            if x in survivors:
                # 部分并入：保留在役，标注指向主条 + 同族联动
                ameta["partial_merged_into"] = primary_id
                fam = ameta.get("family") or []
                if isinstance(fam, str):
                    fam = [fam]
                if primary_id not in fam:
                    fam.append(primary_id)
                ameta["family"] = fam
                if not ameta.get("family_note"):
                    ameta["family_note"] = ("与 %s 共享核心已并入其合并记录，本条保留独有部分"
                                            % primary_id)
            else:
                ameta["status"] = "deprecated"
                ameta["merged_into"] = primary_id
                full_merged.append(x)
            _write_atom(ap, ameta, abody)
    rebuild_index()
    parts = []
    if full_merged:
        parts.append("整条并入(废弃) %s" % ",".join(full_merged))
    if survivors:
        parts.append("部分并入(保留在役) %s" % ",".join(survivors))
    msg = "%s：%s" % (primary_id, "；".join(parts))
    if already:
        msg += "；跳过已并入 " + ",".join(already)
    return True, msg


# ---------------------------------------------------------------- 晋升全局规则

def _write_atom(p, meta, body):
    p.write_text(dump_frontmatter(meta) + "\n" + body, encoding="utf-8", newline="\n")


# ---------------------------------------------------------- 同族多场景提炼

_GENERIC_TAGS = {"python", "windows", "zcode", "测试", "test"}  # 过泛标签不做聚类依据


def _one_liner(body):
    m = re.search(r"【一句话结论】\s*(.+)", body or "")
    return m.group(1).strip() if m else ""


def family_clusters(min_size=2):
    """多场景同族高发区提炼（在役+草稿，废弃原子不参与）。

    聚类依据：
    - 显式链接（强信号）：family / merged_into / partial_merged_into
    - 共有标签（弱信号）：全库出现 ≤3 次的稀有标签才连接——
      windows/python 这类出现在十几条上的标签会把松相关原子黏成大组
    主题词：取覆盖 ≥半数成员的标签（按覆盖率取最高），否则「同族组合」。
    """
    items = [(meta, body) for meta, body, _p in list_atoms()
             if meta.get("status") in ("active", "draft")]
    ids_alive = {m.get("id") for m, _b in items}
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for m, _b in items:
        find(m.get("id"))
    for m, _b in items:  # 显式链接
        for f in (m.get("family") or []):
            if isinstance(f, str) and f in ids_alive:
                union(m.get("id"), f)
        for key in ("merged_into", "partial_merged_into"):
            mi = m.get(key)
            if isinstance(mi, str) and mi in ids_alive:
                union(m.get("id"), mi)
    tag_cnt = {}
    for m, _b in items:  # 先数稀有度
        tags = m.get("tags")
        for t in set(tags if isinstance(tags, list) else []):
            if isinstance(t, str) and t.strip() and t.lower() not in _GENERIC_TAGS:
                tag_cnt[t] = tag_cnt.get(t, 0) + 1

    def _sizes():
        """当前各根的成员数。"""
        s = {}
        for m, _b in items:
            r = find(m.get("id"))
            s[r] = s.get(r, 0) + 1
        return s

    # 稀有标签（≤3 次出现）连接：可把单身原子吸进组、或并两个单身，
    # 但禁止合并两个已有 ≥2 成员的组——否则巧合共词（如 node）会桥接出大杂烩
    rare = [t for t, c in tag_cnt.items() if 2 <= c <= 3]
    for t in sorted(rare, key=lambda x: -tag_cnt[x]):
        tl = t.lower()
        holders = [m.get("id") for m, _b in items
                   if tl in {x.lower() for x in (m.get("tags") if isinstance(m.get("tags"), list) else [])
                             if isinstance(x, str)}]
        if len(holders) < 2:
            continue
        roots = {}
        for hid in holders:
            roots.setdefault(find(hid), []).append(hid)
        sizes = _sizes()
        big = [r for r in roots if sizes.get(r, 0) >= 2]
        if len(big) > 1:
            continue  # 会合并两个成形组 → 放弃该标签
        for r in list(roots)[1:]:
            union(list(roots)[0], r)

    groups = {}
    for m, b in items:
        groups.setdefault(find(m.get("id")), []).append((m, b))

    text = AGENTS_MD.read_text(encoding="utf-8") if AGENTS_MD.exists() else ""
    out = []
    for members in groups.values():
        if len(members) < min_size:
            continue
        members.sort(key=lambda mb: mb[0].get("id", ""))
        cov = {}
        for m, _b in members:
            tags = {t.lower() for t in (m.get("tags") if isinstance(m.get("tags"), list) else [])
                    if isinstance(t, str)}
            for t in tags:
                cov[t] = cov.get(t, 0) + 1
        n = len(members)
        best, best_cov = None, 0.0
        for t, c in cov.items():
            frac = c / n
            if frac >= 0.5 and (frac, c) > (best_cov, cov.get(best, 0) if best else 0):
                best, best_cov = t, frac
        theme = best if best else "同族组合"
        out.append({
            "theme": theme, "n": n,
            "promoted": ("[cluster:%s]" % theme) in text,
            "members": [{
                "id": m.get("id"), "title": m.get("title"),
                "status": m.get("status"), "one_liner": _one_liner(b),
                "boundary": extract_fields(b).get("适用边界", "")[:80],
                "project": m.get("project", ""),
            } for m, b in members],
        })
    out.sort(key=lambda c: -c["n"])
    return out


def condense_cluster(theme, members):
    """程序性凝练同族规则初稿：供 UI 预填/AI 再加工，目标是 2~4 行短规则。

    规则：一句话结论只取首个完整句（按句号/问号/叹号切，不做半截截断）、
    边界取并集、成员 ID 折叠为主题引用。
    """
    bounds, seen, cores = [], set(), []
    for m in members:
        core = (m.get("one_liner") or m.get("title") or "").strip()
        core = re.sub(r"^\[[^\]]+\]\s*", "", core)      # 去掉已带 ID 前缀
        core = re.sub(r"（边界：.*?）\s*$", "", core)     # 去掉尾缀边界
        first = re.split(r"[。？！?!]", core)[0].strip()  # 只留首个完整句
        if first and first not in seen:
            seen.add(first)
            cores.append(first)
        b = (m.get("boundary") or "").strip("。；; ")
        # 边界只取首个完整句，避免拼出截断长串
        b_first = re.split(r"[。；;]", b)[0].strip() if b else ""
        if b_first and b_first not in bounds:
            bounds.append(b_first)
    ids = ",".join(m.get("id") for m in members)
    lines = ["- 【%s 同族】这些场景一律先文件化/对照纪律，勿内联硬拼（详情查原子 %s）" % (theme, ids)]
    for m in members[:4]:  # 高度凝练：最多 4 条，每条=首个完整句；超长句弃用改用标题
        core = (m.get("one_liner") or "").strip()
        core = re.sub(r"（边界：.*?）\s*$", "", core)
        first = re.split(r"[。？！?!]", core)[0].strip()
        if not first or len(first) > 90:
            first = re.sub(r"^\[[^\]]+\]\s*", "", m.get("title") or "").strip()
        if first:
            lines.append("  - %s" % first)
    if bounds:
        lines.append("  - 边界：%s" % "；".join(bounds)[:150].rstrip("；:：") + "（详见各原子）")
    return "\n".join(lines)


def promote_cluster_rule(theme, ids, condensed=None):
    """把同族高发区升级为全局防范规则，写入 AGENTS.md 规则区（幂等）。

    condensed=凝练后的规则文本（首行需含主题标识）；不传则回退为逐条堆叠
    （不推荐，易膨胀——UI/MCP 调用方应先走 condense_cluster 或 AI 凝练）。
    """
    with _FileLock():
        text = AGENTS_MD.read_text(encoding="utf-8") if AGENTS_MD.exists() else ""
        marker = "[cluster:%s]" % theme
        if marker in text:
            return True, "该同族规则已在全局 AGENTS.md（幂等）"
        if condensed:
            first, _, rest = condensed.partition("\n")
            if marker in first:            # 初稿首行已带主题标记，直接用
                block = condensed.strip()
            else:                          # 补一行带标记的标题
                block = "- %s 同族防范规则（%s）\n%s" % (theme, marker, condensed.strip())
        else:
            lines = ["- %s 同族高发（%d 个场景，%s）：相关纪律如下，任何会话开工先对照"
                     % (marker, len(ids), ",".join(ids))]
            for aid in ids:
                p = _atom_file(aid)
                if not p:
                    continue
                m, b = parse_frontmatter(p.read_text(encoding="utf-8"))
                lines.append("  - [%s] %s（边界：%s）" % (
                    aid, _one_liner(b) or m.get("title", ""),
                    extract_fields(b).get("适用边界", "")[:60]))
            block = "\n".join(lines)
        if RULE_SECTION in text:
            text = text.replace(RULE_SECTION, RULE_SECTION + "\n" + block, 1)
        else:
            text = text.rstrip() + "\n\n" + RULE_SECTION + "\n" + block + "\n"
        AGENTS_MD.write_text(text, encoding="utf-8", newline="\n")
    return True, "已写入全局 AGENTS.md 规则区"


def promote_to_rule(aid):
    """把原子蒸馏成一行规则写入全局 AGENTS.md（幂等）。"""
    with _FileLock():
        p = _atom_file(aid)
        if not p:
            return False, "找不到原子 %s" % aid
        meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
        text = AGENTS_MD.read_text(encoding="utf-8") if AGENTS_MD.exists() else ""
        if ("[%s]" % aid) in text:
            meta["rule"] = "global"
            _write_atom(p, meta, body)
            return True, aid + " 已在全局规则中（幂等）"
        m = re.search(r"【一句话结论】\s*(.+)", body)
        rule = (m.group(1).strip() if m else meta.get("title", "")).strip()
        boundary = extract_fields(body).get("适用边界", "")
        line = "- [%s] %s（边界：%s）" % (aid, rule, boundary[:60])
        if RULE_SECTION in text:
            text = text.replace(RULE_SECTION, RULE_SECTION + "\n" + line, 1)
        else:
            text = text.rstrip() + "\n\n" + RULE_SECTION + "\n" + line + "\n"
        AGENTS_MD.write_text(text, encoding="utf-8", newline="\n")
        meta["rule"] = "global"
        _write_atom(p, meta, body)
    rebuild_index()
    return True, aid


def demote_from_rule(aid):
    """从全局 AGENTS.md 撤下该原子的规则行。"""
    with _FileLock():
        p = _atom_file(aid)
        if not p:
            return False, "找不到原子 %s" % aid
        meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
        if AGENTS_MD.exists():
            text = AGENTS_MD.read_text(encoding="utf-8")
            kept = [l for l in text.splitlines() if ("[%s]" % aid) not in l]
            # 晋升区空了就连区头一起撤，不留空壳
            out = []
            for i, l in enumerate(kept):
                if l.strip() == RULE_SECTION:
                    nxt = kept[i + 1] if i + 1 < len(kept) else ""
                    if not nxt.strip() or nxt.strip().startswith("##"):
                        continue  # 区头下一条不是规则行 → 跳过区头
                out.append(l)
            AGENTS_MD.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8", newline="\n")
        meta.pop("rule", None)
        _write_atom(p, meta, body)
    rebuild_index()
    return True, aid


# ---------------------------------------------------------------- 索引

def rebuild_index():
    """重建 knowledge/INDEX.md。返回 (active, draft, deprecated) 数量。"""
    entries = []
    for meta, body, path in list_atoms():
        m = re.search(r"【一句话结论】\s*(.+)", body)
        entries.append((meta, m.group(1).strip() if m else ""))

    active = [e for e in entries if e[0].get("status") == "active"]
    drafts = [e for e in entries if e[0].get("status") == "draft"]
    dep = [e for e in entries if e[0].get("status") == "deprecated"]

    lines = ["# 知识原子索引（自动生成，勿手改）", "",
             "共 %d 在役 / %d 待审草稿 / %d 已废弃。" % (len(active), len(drafts), len(dep)),
             "检索：paios_search_atoms 工具，或本文件浏览。", ""]
    groups = {}
    for meta, first in active:
        groups.setdefault(meta.get("type", "未分类"), []).append((meta, first))
    for g in sorted(groups):
        lines.append("## %s" % g)
        lines.append("")
        for meta, first in sorted(groups[g], key=lambda x: x[0].get("id", "")):
            tags = meta.get("tags")
            tags = ",".join(tags) if isinstance(tags, list) else (tags or "")
            lines.append("### %s %s" % (meta.get("id"), meta.get("title")))
            lines.append("- 标签：%s ｜ 日期：%s ｜ 项目：%s ｜ 来源：%s%s"
                         % (tags, meta.get("date"), meta.get("project") or "-",
                            meta.get("source"),
                            " ｜ 📌已晋升全局规则" if meta.get("rule") == "global" else ""))
            if first:
                lines.append("- 结论：%s" % first)
            lines.append("- 溯源：%s" % (meta.get("source_ref") or "-"))
            lines.append("")
    if drafts:
        lines.append("## 待审草稿（转正后才进入上方检索）")
        lines.append("")
        for meta, first in drafts:
            lines.append("- %s %s（%s）" % (meta.get("id"), meta.get("title"), meta.get("type")))
        lines.append("")
    INDEX_MD.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return len(active), len(drafts), len(dep)
