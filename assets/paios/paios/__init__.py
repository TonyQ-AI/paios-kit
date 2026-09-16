# -*- coding: utf-8 -*-
"""PAIOS 公共路径与配置（个人 AI 作业系统，BLUEPRINT v1.1）。"""
import json
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
BASE_DIR = PACKAGE_DIR.parent                 # 安装根（paios-kit 布局）
STATIC_DIR = PACKAGE_DIR / "static"
KNOWLEDGE_DIR = BASE_DIR / "knowledge"
ATOMS_DIR = KNOWLEDGE_DIR / "atoms"
INDEX_MD = KNOWLEDGE_DIR / "INDEX.md"
DATA_DIR = BASE_DIR / "data"
INDEX_DB = DATA_DIR / "index.sqlite"
BACKUP_DIR = BASE_DIR / "backup"
WORKSPACE_ROOTS_FILE = DATA_DIR / "workspace-roots.json"

DEFAULT_PORT = 8643        # 记忆基座 Web UI
DEFAULT_PORTAL_PORT = 8766  # 流程基座/门户（本分发版不含）

ZCODE_DIR = Path.home() / ".zcode"
SOURCE_DB = ZCODE_DIR / "cli" / "db" / "db.sqlite"


def ensure_data_dir():
    for d in (DATA_DIR, KNOWLEDGE_DIR, ATOMS_DIR, BACKUP_DIR, STATIC_DIR):
        d.mkdir(parents=True, exist_ok=True)


def source_db_uri():
    """ZCode 主库只读 URI。"""
    return "file:" + str(SOURCE_DB).replace("\\", "/") + "?mode=ro"


def load_workspace_roots():
    """已注册的工作区根目录列表。"""
    try:
        return json.loads(WORKSPACE_ROOTS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def save_workspace_roots(roots):
    ensure_data_dir()
    WORKSPACE_ROOTS_FILE.write_text(
        json.dumps(roots, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
