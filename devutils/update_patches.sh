#!/bin/bash -eux

PLATFORM_ROOT=$(dirname $(dirname $(readlink -f ${BASH_SOURCE[0]})))
FOCUS_REPO=$PLATFORM_ROOT/focus-chromium

_command=$1

$FOCUS_REPO/devutils/update_platform_patches.py $_command $PLATFORM_ROOT/patches
