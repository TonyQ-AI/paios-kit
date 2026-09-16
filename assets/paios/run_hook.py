import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _run():
    from paios import hook  # noqa: E402

    hook.main()


try:
    _run()
except Exception:
    # hook 尽力而为：异常只落 stderr，永远不阻塞会话
    traceback.print_exc(file=sys.stderr)
    sys.exit(0)
sys.exit(0)
