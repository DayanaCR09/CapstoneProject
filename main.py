import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from cli.menu import CLIApp  # noqa: E402, I001

__all__ = ["CLIApp"]


def main() -> None:
    CLIApp().run()


if __name__ == "__main__":
    main()
