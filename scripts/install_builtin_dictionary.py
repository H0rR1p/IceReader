import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.builtin_dictionary import install


if __name__ == "__main__":
    state = install()
    print(state["job"]["message"])

