# -*- coding: utf-8 -*-
"""向量语义查重（BLUEPRINT：真相源 markdown 不动，引擎可插拔）。

引擎：fastembed + BAAI/bge-small-zh-v1.5（中文专用 0.09GB，ONNX CPU 推理）。
模型文件缓存在 data/models/；向量缓存 data/vec-cache.json（id+mtime 失效）。
判别力实测：无关对 ~0.37 / 相邻主题 0.55~0.70 / 语义重复预期 0.85+；
SEM_WARN=0.82 作为语义层软门禁，词面层照旧并行。
"""
import json
import os

from . import DATA_DIR

MODEL_NAME = "BAAI/bge-small-zh-v1.5"
CACHE_FILE = DATA_DIR / "vec-cache.json"
SEM_WARN = 0.82

_model = None


def _get_model():
    global _model
    if _model is None:
        if (DATA_DIR / "models" / "fast-bge-small-zh-v1.5").is_dir():
            # 模型已在本地：强制离线，跳过 fastembed 启动时的 HF 联网检查
            # （服务器进程无代理，直连 huggingface.co 会挂死 → 找相似接口无响应）
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
            os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        else:
            os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
        from fastembed import TextEmbedding
        _model = TextEmbedding(model_name=MODEL_NAME,
                               cache_dir=str(DATA_DIR / "models"))
    return _model


def embed_texts(texts):
    m = _get_model()
    return [v.tolist() for v in m.embed(["query: " + t for t in texts])]


def _load_cache():
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cache(cache):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache), encoding="utf-8")


def _atom_text(meta, body):
    import re as _re
    m = _re.search(r"【一句话结论】\s*(.+)", body)
    return (meta.get("title", "") + "。 " + (m.group(1).strip() if m else ""))[:300]


def atom_vectors(atoms_list):
    """atoms_list = [(meta, body, path)] → {id: vec}，带 mtime 缓存。"""
    cache = _load_cache()
    need, result = [], {}
    for meta, body, path in atoms_list:
        aid = meta.get("id")
        key = "%s:%s" % (aid, int(path.stat().st_mtime))
        if aid in cache and cache[aid].get("k") == key:
            result[aid] = cache[aid]["v"]
        else:
            need.append((aid, key, _atom_text(meta, body)))
    if need:
        vecs = embed_texts([t for _a, _k, t in need])
        for (aid, key, _t), v in zip(need, vecs):
            result[aid] = v
            cache[aid] = {"k": key, "v": v}
        _save_cache(cache)
    return result


def semantic_similar(title, body, atoms_list, vecs, top=5, exclude_id=None):
    """新文本对库内原子的语义相似度 [(score, meta, body)] 降序。"""
    q = embed_texts([_atom_text({"title": title}, body)])[0]
    out = []
    for meta, b, path in atoms_list:
        aid = meta.get("id")
        v = vecs.get(aid)
        if not v or aid == exclude_id:
            continue
        dot = sum(x * y for x, y in zip(q, v))
        na = sum(x * x for x in q) ** 0.5
        nb = sum(x * x for x in v) ** 0.5
        out.append((dot / (na * nb) if na and nb else 0.0, meta, b))
    out.sort(key=lambda x: -x[0])
    return out[:top]
