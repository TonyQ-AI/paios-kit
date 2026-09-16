# -*- coding: utf-8 -*-
"""原子库自动推送：knowledge/ 有变更即 commit + push（Stop hook 第四步）。

- 只提交 knowledge/ 子目录（原子真相源 + 索引），代码仓其余部分不自动推
- push 走 openssl + 代理，重试 3 次；失败只落 stderr（hook 静默纪律）
- 幂等：无变更时静默返回
"""
import os
import subprocess
import sys
import time

REPO = None  # 由 hook 传入，默认 PAIOS 根目录


def _proxy_args():
    """代理可配置：环境变量 PAIOS_GIT_PROXY 设置才走代理，默认直连。"""
    proxy = os.environ.get("PAIOS_GIT_PROXY", "").strip()
    if not proxy:
        return []
    return ["-c", "http.proxy=" + proxy, "-c", "https.proxy=" + proxy]


def _run(args, cwd):
    return subprocess.run(args, cwd=cwd, capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


def push_atoms(repo_dir=None):
    from . import BASE_DIR
    repo = repo_dir or str(BASE_DIR)
    st = _run(["git", "status", "--porcelain", "knowledge/"], repo)
    if st.returncode != 0:
        return "no-repo"

    # 步骤1：未提交变更 → 本地提交（数据先落本地，push 失败也不丢）
    if st.stdout.strip():
        _run(["git", "add", "knowledge/"], repo)
        msg = "atoms: auto-sync %s" % time.strftime("%Y-%m-%d %H:%M")
        c = _run(["git", "commit", "-m", msg], repo)
        if c.returncode != 0 and "nothing to commit" not in (c.stdout + c.stderr):
            print("atoms push: commit failed: %s" % (c.stderr or c.stdout)[:200],
                  file=sys.stderr)
            return "commit-failed"

    # 步骤2：本地有未推送提交（含上次 push 失败的积压）→ 推送
    ahead = _run(["git", "rev-list", "--count", "origin/main..main"], repo)
    pending = ahead.stdout.strip() if ahead.returncode == 0 else "0"
    if pending == "0":
        return "clean"
    if last_failure_note := _read_fail_note(repo):
        print("atoms push: resuming, previous failure: %s" % last_failure_note,
              file=sys.stderr)

    base = ["git", "-c", "http.sslBackend=openssl"] + _proxy_args() + \
        ["push", "origin", "main"]
    last = None
    for _ in range(3):  # 代理路径重试 3 次
        last = _run(base, repo)
        if last.returncode == 0:
            _clear_fail_note(repo)
            return "pushed(%s pending)" % pending
        time.sleep(3)
    # 兜底 A：代理持续失败 → 试一次直连（网络环境可能已变化，无害）
    direct = _run(["git", "-c", "http.sslBackend=openssl", "push", "origin", "main"], repo)
    if direct.returncode == 0:
        _clear_fail_note(repo)
        return "pushed-direct(%s pending)" % pending
    # 兜底 B：记失败便签，下次 hook 自动续推（数据安全在本地提交层）
    note = "%s | %s" % (time.strftime("%m-%d %H:%M"),
                        ((direct.stderr or last.stderr or "")[:120]).strip())
    _write_fail_note(repo, note)
    print("atoms push failed (proxy x3 + direct): %s；本地提交安全，将随下次 hook 自动续推"
          % note, file=sys.stderr)
    return "push-failed(queued)"


def _fail_note_path(repo):
    import os
    return os.path.join(repo, "data", "atoms-push-failed.txt")


def _read_fail_note(repo):
    try:
        with open(_fail_note_path(repo), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def _write_fail_note(repo, note):
    try:
        with open(_fail_note_path(repo), "w", encoding="utf-8", newline="\n") as f:
            f.write(note)
    except OSError:
        pass


def _clear_fail_note(repo):
    try:
        import os
        os.remove(_fail_note_path(repo))
    except OSError:
        pass
