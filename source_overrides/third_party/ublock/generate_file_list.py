# Copyright 2025 The Focus Authors
# You can use, redistribute, and/or modify this source code under
# the terms of the GPL-3.0 license that can be found in the LICENSE file.

import json
import os
from sys import argv
from pathlib import Path

ublock_src = Path(argv[1])
out_file = argv[2]

with open(out_file, 'w') as f:
  f.write(json.dumps(
    { "base_dir": ublock_src.as_posix(),
      "files":   [ (Path(root) / file).relative_to(ublock_src).as_posix()
                   for root, _, files in os.walk(ublock_src)
                   for file in files ]
    }
  ))
