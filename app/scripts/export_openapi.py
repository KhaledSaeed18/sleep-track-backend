"""Write the OpenAPI document to disk without starting a server.

python -m app.scripts.export_openapi docs/openapi.json [more/paths.json ...]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from app.main import create_app

DEFAULT_TARGET = Path("docs/openapi.json")


def main(argv: list[str] | None = None) -> None:
    targets = [Path(arg) for arg in (argv if argv is not None else sys.argv[1:])] or [
        DEFAULT_TARGET
    ]
    document = json.dumps(create_app().openapi(), indent=2, ensure_ascii=False) + "\n"
    for target in targets:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(document, encoding="utf-8")
        print(f"Wrote {target}")


if __name__ == "__main__":
    main()
