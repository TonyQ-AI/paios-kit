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
    if len(sys.argv) < 2:
        print("usage: python silent.py <cmd> [args...]"); sys.exit(2)
    r = subprocess.run(sys.argv[1:], creationflags=CREATE_NO_WINDOW,
                       capture_output=True, encoding="gbk", errors="replace")
    if r.stdout: print(r.stdout, end="")
    if r.stderr: print(r.stderr, end="", file=sys.stderr)
    sys.exit(r.returncode)

if __name__ == "__main__":
    main()
