// Copyright 2026 The Focus Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#ifndef CHROME_INSTALLER_UTIL_UPDATE_INSTALL_STATUS_H_
#define CHROME_INSTALLER_UTIL_UPDATE_INSTALL_STATUS_H_

#include "chrome/installer/util/util_constants.h"

namespace installer {

// setup.exe and mini_installer.exe use non-zero exit codes for several
// successful outcomes. Keep the updater paths from treating a staged update,
// repair, or already-current install as a failure.
constexpr bool IsSuccessfulUpdateInstallerExitCode(int exit_code) {
  switch (exit_code) {
    case FIRST_INSTALL_SUCCESS:
    case INSTALL_REPAIRED:
    case NEW_VERSION_UPDATED:
    case EXISTING_VERSION_LAUNCHED:
    case IN_USE_UPDATED:
      return true;
    default:
      return false;
  }
}

static_assert(IsSuccessfulUpdateInstallerExitCode(FIRST_INSTALL_SUCCESS));
static_assert(IsSuccessfulUpdateInstallerExitCode(INSTALL_REPAIRED));
static_assert(IsSuccessfulUpdateInstallerExitCode(NEW_VERSION_UPDATED));
static_assert(IsSuccessfulUpdateInstallerExitCode(EXISTING_VERSION_LAUNCHED));
static_assert(IsSuccessfulUpdateInstallerExitCode(IN_USE_UPDATED));
static_assert(!IsSuccessfulUpdateInstallerExitCode(HIGHER_VERSION_EXISTS));
static_assert(!IsSuccessfulUpdateInstallerExitCode(INSTALL_FAILED));

}  // namespace installer

#endif  // CHROME_INSTALLER_UTIL_UPDATE_INSTALL_STATUS_H_
