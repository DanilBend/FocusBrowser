# Copyright 2026 The Focus Browser Authors

import json
from pathlib import Path
from sys import argv

source_dir = Path(argv[1])
output_file = argv[2]

# Keep this list explicit. Stale upstream account, payment, telemetry or
# promotion files can never enter the component package by directory globbing.
files = [
    "manifest.json",
    "popup.html",
    "background/events.js",
    "content-script/main.css",
    "content-script/main.js",
    "css/popup.css",
    "js/popup.js",
    "shared/main.js",
    "shared/utils.js",
    "images/icon-16.png",
    "images/icon-32.png",
    "images/icon-48.png",
    "images/icon-128.png",
    "images/icon-off-16.png",
    "images/icon-off-32.png",
    "images/icon-off-48.png",
    "images/icon-off-128.png",
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
