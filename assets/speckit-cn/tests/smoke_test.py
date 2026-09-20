#!/usr/bin/env python3
"""冒烟测试：验证本次改造后 .specify 脚本的行为契约。

改动背景：本项目的工作约定是「master 直做，不建功能分支」，因此
`create-new-feature.ps1` 不再 `git checkout -b`，改为把当前切片写入
`.specify/feature.json`，由 `common.ps1` 统一解析（SPECIFY_FEATURE →
feature.json → specs 编号目录）。

本测试在系统临时目录里建一次性 git 仓库跑真实脚本，断言四条契约：
  1. 建切片时不产生任何 git 分支（始终停在默认分支）
  2. 切片目录、spec.md、.specify/feature.json 都正确生成，且编号递增
  3. 站在默认分支上 check-prerequisites / setup-plan 能正常跑通
  4. 空项目（还没有 specs/）必须被拦住：返回码非 0、给出明确指引、
     且不得创建任何垃圾目录（如 specs/master）

用法（需要 Windows PowerShell 与 git 在 PATH 上）：
    python tests/smoke_test.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MASTER = os.path.join(REPO, "claude", ".specify")
NO_WINDOW = 0x08000000
results = []


def check(name, condition, detail=""):
    results.append((name, bool(condition), detail))


def run(args, cwd):
    proc = subprocess.run(args, cwd=cwd, capture_output=True, encoding="gbk",
                          errors="replace", creationflags=NO_WINDOW)
    return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()


def ps(script, *args, cwd):
    return run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                os.path.join(".specify", "scripts", "powershell", script), *args], cwd)


def make_repo(path):
    os.makedirs(path)
    shutil.copytree(os.path.join(MASTER, "scripts"), os.path.join(path, ".specify", "scripts"))
    shutil.copytree(os.path.join(MASTER, "templates"), os.path.join(path, ".specify", "templates"))
    with open(os.path.join(path, "README.md"), "w", encoding="utf-8") as fh:
        fh.write("smoke test\n")
    run(["git", "init", "-q"], path)
    run(["git", "config", "user.email", "smoke@test.local"], path)
    run(["git", "config", "user.name", "smoke test"], path)
    run(["git", "add", "-A"], path)
    run(["git", "commit", "-qm", "init"], path)


def main():
    if not os.path.isdir(MASTER):
        print("找不到 %s（请在仓库根运行本测试）" % MASTER)
        return 2

    tmp = tempfile.mkdtemp(prefix="speckit-smoke-")
    good = os.path.join(tmp, "repo")
    empty = os.path.join(tmp, "empty")
    try:
        make_repo(good)

        rc, out, _err = ps("create-new-feature.ps1", "-Json", "data layer", cwd=good)
        payload = {}
        try:
            payload = json.loads(out)
        except ValueError:
            pass
        check("建切片返回 0 且输出契约不变", rc == 0 and payload.get("FEATURE_NUM") == "001", out[:160])

        branches = run(["git", "branch"], good)[1].replace("\n", " ").strip()
        check("未创建任何 git 分支", branches.startswith("* master") and "001-" not in branches, branches)

        check("切片目录与 spec.md 已生成",
              os.path.exists(os.path.join(good, "specs", "001-data-layer", "spec.md")))
        fj = os.path.join(good, ".specify", "feature.json")
        content = open(fj, encoding="utf-8").read() if os.path.exists(fj) else ""
        check(".specify/feature.json 指向当前切片",
              json.loads(content or "{}").get("feature_directory") == "specs/001-data-layer", content)

        rc, out, _err = ps("setup-plan.ps1", "-Json", cwd=good)
        check("setup-plan 在默认分支上可跑通", rc == 0 and
              os.path.exists(os.path.join(good, "specs", "001-data-layer", "plan.md")), out[:120])

        rc, out, _err = ps("check-prerequisites.ps1", "-Json", "-PathsOnly", cwd=good)
        check("check-prerequisites 在默认分支上可跑通",
              rc == 0 and "001-data-layer" in out, out[:120])

        rc, _out, err = ps("check-prerequisites.ps1", "-Json", "-RequireTasks", cwd=good)
        check("缺 tasks.md 时仍然拦住（返回码非 0）", rc != 0 and "tasks.md" in (_out + err), (_out + err)[:120])

        rc, out, _err = ps("create-new-feature.ps1", "-Json", "orchestration skeleton", cwd=good)
        check("编号递增到 002",
              rc == 0 and json.loads(out or "{}").get("FEATURE_NUM") == "002" and
              os.path.exists(os.path.join(good, "specs", "002-orchestration-skeleton", "spec.md")), out[:120])
        fj2 = json.loads(open(fj, encoding="utf-8").read() or "{}")
        check("feature.json 跟随切到 002", fj2.get("feature_directory") == "specs/002-orchestration-skeleton")

        make_repo(empty)
        rc, _out, err = ps("check-prerequisites.ps1", "-Json", cwd=empty)
        check("空项目：check-prerequisites 被拦住", rc != 0 and "slice" in err.lower(), err[:120])
        rc, _out, err = ps("setup-plan.ps1", "-Json", cwd=empty)
        check("空项目：setup-plan 被拦住", rc != 0, err[:120])
        check("空项目：未创建垃圾目录",
              not os.path.exists(os.path.join(empty, "specs", "master")) and
              not os.path.exists(os.path.join(empty, "specs", "main")))
    finally:
        def onerror(func, path, _exc):
            os.chmod(path, 0o777)
            func(path)
        shutil.rmtree(tmp, onerror=onerror)

    failed = 0
    for name, ok, detail in results:
        print(("  PASS  " if ok else "  FAIL  ") + name + ("" if ok else "   <- " + detail))
        failed += 0 if ok else 1
    print("\n%d 项断言，%d 项失败" % (len(results), failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
