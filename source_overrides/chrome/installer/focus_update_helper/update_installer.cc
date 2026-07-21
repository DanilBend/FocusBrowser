// Copyright 2026 The Focus Authors
// You can use, redistribute, and/or modify this source code under
// the terms of the GPL-3.0 license that can be found in the LICENSE file.

#include <windows.h>

#include <cstdint>
#include <vector>

#include "base/base_paths.h"
#include "base/command_line.h"
#include "base/containers/span.h"
#include "base/files/file.h"
#include "base/path_service.h"
#include "base/process/launch.h"
#include "base/process/process.h"
#include "base/strings/strcat_win.h"
#include "base/strings/string_number_conversions_win.h"
#include "base/win/elevation_util.h"
#include "chrome/installer/focus_update_helper/focus_update_helper_internal.h"
#include "chrome/installer/util/update_install_status.h"

namespace focus_updater {

namespace {

// Upper bound on the update payload we'll read into memory. The mini_installer
// is hundred to a few hundred MB; reject anything beyond 512 MiB as bogus.
constexpr int64_t kMaxPayloadBytes = int64_t{512} * 1024 * 1024;

base::FilePath InstalledChromeExe() {
  base::FilePath exe;
  if (!base::PathService::Get(base::FILE_EXE, &exe)) {
    return base::FilePath();
  }
  return exe.DirName().DirName().Append(FILE_PATH_LITERAL("chrome.exe"));
}

}  // namespace

int ApplyUpdate(const base::FilePath& payload, const std::string& signature) {
  if (!IsAcceptablePayloadPath(payload)) {
    LogEvent(EVENTLOG_ERROR_TYPE,
             L"Payload path missing or not a local absolute path");
    return kBadArgs;
  }

  base::File file(payload, base::File::FLAG_OPEN | base::File::FLAG_READ |
                               base::File::FLAG_WIN_EXCLUSIVE_WRITE);
  if (!file.IsValid()) {
    LogEvent(EVENTLOG_ERROR_TYPE, L"Cannot open payload.");
    return kCannotOpenPayload;
  }

  const int64_t length = file.GetLength();
  if (length <= 0 || length > kMaxPayloadBytes) {
    LogEvent(EVENTLOG_ERROR_TYPE, L"Payload too large or unreadable.");
    return kCannotOpenPayload;
  }

  std::vector<uint8_t> bytes(static_cast<size_t>(length));
  if (!file.ReadAndCheck(0, base::span(bytes))) {
    LogEvent(EVENTLOG_ERROR_TYPE, L"Cannot read payload.");
    return kCannotOpenPayload;
  }

  if (!VerifyPayload(file.GetPlatformFile(), payload, bytes, signature)) {
    LogEvent(EVENTLOG_ERROR_TYPE,
             L"Payload failed verification; refusing to install.");
    return kVerificationFailed;
  }

  // Run the installer. The browser has exited, so this is a clean install
  // (new version dir + rename), no deferral.
  base::CommandLine command(payload);
  command.AppendSwitch("system-level");
  command.AppendSwitch("do-not-launch-chrome");
  base::LaunchOptions options;
  options.start_hidden = true;
  base::Process installer = base::LaunchProcess(command, options);
  if (!installer.IsValid()) {
    LogEvent(EVENTLOG_ERROR_TYPE, L"Failed to launch installer.");
    return kInstallLaunchFailed;
  }

  int exit_code = -1;
  if (!installer.WaitForExit(&exit_code) ||
      !::installer::IsSuccessfulUpdateInstallerExitCode(exit_code)) {
    LogEvent(EVENTLOG_ERROR_TYPE,
             base::StrCat({L"Installer reported failure, exit code ",
                           base::NumberToWString(exit_code)}));
    return kInstallFailed;
  }

  LogEvent(EVENTLOG_INFORMATION_TYPE, L"System update installed.");
  return kOk;
}

bool RelaunchBrowser() {
  const base::FilePath chrome_exe = InstalledChromeExe();
  if (chrome_exe.empty()) {
    return false;
  }
  const HRESULT hr = base::win::RunDeElevatedNoWait(chrome_exe.value(),
                                                    L"--restore-last-session");
  if (FAILED(hr)) {
    LogEvent(EVENTLOG_WARNING_TYPE, L"Relaunch via shell failed.");
    return false;
  }
  return true;
}

}  // namespace focus_updater
