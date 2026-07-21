#!/bin/bash -eux

PLATFORM_ROOT=$(dirname $(dirname $(readlink -f ${BASH_SOURCE[0]})))
FOCUS_REPO=$PLATFORM_ROOT/focus-chromium

$FOCUS_REPO/devutils/check_patch_files.py -p $PLATFORM_ROOT/patches
