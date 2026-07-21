#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Copyright 2025 The Focus Authors
# You can use, redistribute, and/or modify this source code under
# the terms of the GPL-3.0 license that can be found in the LICENSE file.

# Copyright (c) 2018 The ungoogled-chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""
Focus Browser packaging script for Microsoft Windows
"""

import sys
if sys.version_info.major < 3:
    raise RuntimeError('Python 3 is required for this script.')

import argparse
import os
import platform
from pathlib import Path
import shutil
import subprocess

sys.path.insert(0, str(Path(__file__).resolve().parent / 'focus-chromium' / 'utils'))
import focus_version
import filescfg
from _common import ENCODING, get_chromium_version
sys.path.pop(0)

_ROOT_DIR = Path(__file__).resolve().parent
_BUILD_SRC = _ROOT_DIR / 'build' / 'src'
_ICON_PATH = _BUILD_SRC / 'chrome' / 'app' / 'theme' / 'chromium' / 'win' / 'chromium.ico'

_cached_target_cpu = None


def _get_display_version(version):
    """Drops only trailing zero components while keeping major.minor."""
    parts = version.split('.')
    while len(parts) > 2 and parts[-1] == '0':
        parts.pop()
    return '.'.join(parts)


def _get_target_cpu(build_outputs):
    global _cached_target_cpu
    if not _cached_target_cpu:
        with open(build_outputs / 'args.gn', 'r') as f:
            args_gn_text = f.read()
            for cpu in ('x64', 'arm64'):
                if f'target_cpu="{cpu}"' in args_gn_text:
                    _cached_target_cpu = cpu
                    break
    assert _cached_target_cpu
    return _cached_target_cpu

def _build_nsis_installer(version, display_version, arch, build_outputs,
                          output_file):
    cmd = [
        str(_BUILD_SRC / 'third_party' / 'nsis' / 'makensis.exe'),
        '-NOCD',
        f'-DVERSION={version}',
        f'-DDISPLAY_VERSION={display_version}',
        f'-DARCH={arch}',
        f'-DSETUP_EXE={build_outputs / "setup.exe"}',
        f'-DFOCUS_BROWSER_7Z={build_outputs / "focus_browser.packed.7z"}',
        f'-DICON_FILE={_ICON_PATH}',
        f'-DOUTPUT_FILE={output_file}',
        f'-DLICENSE_FILE={_ROOT_DIR / "LICENSE"}',
        str(_ROOT_DIR / 'installer' / 'focus_browser.nsi'),
    ]
    subprocess.run(cmd, check=True)


def main():
    """Entrypoint"""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--cpu-arch',
        metavar='ARCH',
        default=platform.architecture()[0],
        choices=('64bit', '32bit'),
        help=('Filter build outputs by a target CPU. '
              'This is the same as the "arch" key in FILES.cfg. '
              'Default (from platform.architecture()): %(default)s'))
    args = parser.parse_args()

    build_outputs = Path('build/src/out/Default')

    version_parts = focus_version.get_version_parts(_ROOT_DIR / 'focus-chromium', _ROOT_DIR)
    version = f"{version_parts['FOCUS_MAJOR']}.{version_parts['FOCUS_MINOR']}." + \
              f"{version_parts['FOCUS_PATCH']}.{version_parts['FOCUS_PLATFORM']}"
    display_version = _get_display_version(version)

    target_cpu = _get_target_cpu(build_outputs)

    installer_output = (
        _ROOT_DIR / 'build' /
        f'FocusBrowser_{display_version}_{target_cpu}-installer.exe')
    _build_nsis_installer(
        version, display_version, target_cpu, build_outputs, installer_output)

    mini_installer = build_outputs / 'mini_installer.exe'
    shutil.copy2(
        mini_installer,
        _ROOT_DIR / 'build' /
        f'FocusBrowser_{display_version}_{target_cpu}-mini-installer.exe')

    timestamp = None
    try:
        with open('build/src/build/util/LASTCHANGE.committime', 'r') as ct:
            timestamp = int(ct.read())
    except FileNotFoundError:
        pass

    output = Path('build/FocusBrowser_{}_{}-windows.zip'.format(
        display_version, target_cpu))

    excluded_files = set([
        Path('mini_installer.exe'),
        Path('mini_installer_exe_version.rc'),
        Path('setup.exe'),
        Path('focus_browser.packed.7z'),
    ])
    files_generator = filescfg.filescfg_generator(
        Path('build/src/chrome/tools/build/win/FILES.cfg'),
        build_outputs, args.cpu_arch, excluded_files)
    filescfg.create_archive(
        files_generator, tuple(), build_outputs, output, timestamp)

if __name__ == '__main__':
    main()
