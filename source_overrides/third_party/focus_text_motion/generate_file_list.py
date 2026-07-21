# Copyright 2026 The Focus Browser Authors

import json
from pathlib import Path
from sys import argv

source_dir = Path(argv[1])
output_file = argv[2]

files = [
    "manifest.json",
    "background.js",
    "content-script.js",
]

for relative_path in files:
    if not (source_dir / relative_path).is_file():
        raise FileNotFoundError(relative_path)

with open(output_file, "w", encoding="utf-8") as output:
    json.dump(
        {"base_dir": source_dir.as_posix(), "files": files},
        output,
        ensure_ascii=False,
        indent=2,
    )
