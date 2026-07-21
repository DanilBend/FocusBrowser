// Copyright 2026 The Focus Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#include "chrome/installer/util/update_install_status.h"

#include "testing/gtest/include/gtest/gtest.h"

namespace focus_updater {

TEST(UpdateInstallStatusTest, AcceptsEverySuccessfulInstallerOutcome) {
  EXPECT_TRUE(installer::IsSuccessfulUpdateInstallerExitCode(
      installer::FIRST_INSTALL_SUCCESS));
  EXPECT_TRUE(installer::IsSuccessfulUpdateInstallerExitCode(
      installer::INSTALL_REPAIRED));
  EXPECT_TRUE(installer::IsSuccessfulUpdateInstallerExitCode(
      installer::NEW_VERSION_UPDATED));
  EXPECT_TRUE(installer::IsSuccessfulUpdateInstallerExitCode(
      installer::EXISTING_VERSION_LAUNCHED));
  EXPECT_TRUE(installer::IsSuccessfulUpdateInstallerExitCode(
      installer::IN_USE_UPDATED));
}

TEST(UpdateInstallStatusTest, RejectsFailureAndNoUpdateOutcomes) {
  EXPECT_FALSE(installer::IsSuccessfulUpdateInstallerExitCode(
      installer::HIGHER_VERSION_EXISTS));
  EXPECT_FALSE(installer::IsSuccessfulUpdateInstallerExitCode(
      installer::INSTALL_FAILED));
  EXPECT_FALSE(installer::IsSuccessfulUpdateInstallerExitCode(
      installer::INSUFFICIENT_RIGHTS));
  EXPECT_FALSE(installer::IsSuccessfulUpdateInstallerExitCode(-1));
}

}  // namespace focus_updater
