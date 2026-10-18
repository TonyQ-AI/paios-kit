# -*- coding: utf-8 -*-
"""静默执行器：Windows 上以 CREATE_NO_WINDOW 运行命令，不弹控制台窗口。
必须捕获输出（继承 MSYS stdio 会卡死），按 GBK 解码防中文乱码。
用法：python silent.py <命令> [参数...]
  例：python silent.py cmd /c mklink /J link target
退出码透传；stdout/stderr 按 GBK 解码后转发。
"""
import subprocess, sys

CREATE_NO_WINDOW = 0x08000000

def main():
    args = sys.argv[1:]
    if not args:
        print("usage: python silent.py <cmd> [args...]"); sys.exit(2)
    # MSYS/Git Bash 会把 cmd 的 /c 改写成 C:/ 或吃掉 ——自动纠正（2026-10-02 事故：
    # `silent.py cmd /c npm run build` 实际空跑，exit 0 假成功，产物未更新却以为构建成功）
    if args[0].lower() in ("cmd", "cmd.exe") and len(args) >= 2 and args[1].lower() in ("c:/", "c:\\", "c:"):
        print("[silent] /c 被 MSYS 改写为 %r，已自动纠正为 /c" % args[1], file=sys.stderr)
        args[1] = "/c"
    r = subprocess.run(args, creationflags=CREATE_NO_WINDOW,
                       capture_output=True, encoding="gbk", errors="replace")
    if r.stdout: print(r.stdout, end="")
    if r.stderr: print(r.stderr, end="", file=sys.stderr)
    sys.exit(r.returncode)

if __name__ == "__main__":
    main()
