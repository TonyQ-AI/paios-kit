import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from paios.webserver import serve  # noqa: E402

if __name__ == "__main__":
    serve()
