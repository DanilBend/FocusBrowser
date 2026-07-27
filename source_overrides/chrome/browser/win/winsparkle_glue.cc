// Copyright 2026 The Focus Authors
// You can use, redistribute, and/or modify this source code under
// the terms of the GPL-3.0 license that can be found in the LICENSE file.

#include "chrome/browser/win/winsparkle_glue.h"

#include <windows.h>

#include <shellapi.h>

#include <algorithm>
#include <atomic>
#include <memory>
#include <string>
#include <string_view>
#include <utility>

#include "base/base64.h"
#include "base/base_paths.h"
#include "base/callback_list.h"
#include "base/command_line.h"
#include "base/files/file_path.h"
#include "base/files/file_util.h"
#include "base/functional/bind.h"
#include "base/location.h"
#include "base/logging.h"
#include "base/memory/raw_ptr.h"
#include "base/memory/weak_ptr.h"
#include "base/no_destructor.h"
#include "base/path_service.h"
#include "base/process/launch.h"
#include "base/process/process.h"
#include "base/scoped_observation.h"
#include "base/strings/string_number_conversions.h"
#include "base/strings/utf_string_conversions.h"
#include "base/task/thread_pool.h"
#include "base/time/time.h"
#include "base/version.h"
#include "base/version_info/version_info.h"
#include "chrome/browser/browser_process.h"
#include "chrome/browser/buildflags.h"
#include "chrome/browser/profiles/profile.h"
#include "chrome/browser/profiles/profile_manager.h"
#include "chrome/browser/profiles/profile_manager_observer.h"
#include "chrome/browser/profiles/profile_observer.h"
#include "chrome/browser/ui/browser.h"
#include "chrome/browser/ui/browser_window.h"
#include "chrome/browser/ui/browser_window/public/browser_collection_observer.h"
#include "chrome/browser/ui/browser_window/public/browser_window_interface.h"
#include "chrome/browser/ui/browser_window/public/global_browser_collection.h"
#include "chrome/browser/ui/webui/help/version_updater.h"
#include "chrome/common/chrome_paths.h"
#include "chrome/grit/generated_resources.h"
#include "chrome/install_static/install_util.h"
#include "chrome/installer/util/update_install_status.h"
#include "chrome/installer/util/util_constants.h"
#include "components/focus_services/focus_services_helpers.h"
#include "components/focus_services/pref_names.h"
#include "components/prefs/pref_change_registrar.h"
#include "components/prefs/pref_service.h"
#include "content/public/browser/browser_task_traits.h"
#include "content/public/browser/browser_thread.h"
#include "content/public/browser/web_contents.h"
#include "third_party/winsparkle/include/winsparkle.h"
#include "ui/base/l10n/l10n_util.h"
#include "ui/base/mojom/dialog_button.mojom.h"
#include "ui/base/mojom/ui_base_types.mojom-shared.h"
#include "ui/base/ui_base_types.h"
#include "ui/views/controls/button/md_text_button.h"
#include "ui/views/controls/label.h"
#include "ui/views/layout/box_layout.h"
#include "ui/views/layout/layout_provider.h"
#include "ui/views/widget/widget.h"
#include "ui/views/window/dialog_delegate.h"
#include "url/gurl.h"
#include "url/url_constants.h"

namespace focus {

namespace {

std::atomic<bool> g_nearly_updated{false};

const GURL& ConfiguredAppcastUrl() {
  static const base::NoDestructor<GURL> appcast_url(
      BUILDFLAG(WINSPARKLE_APPCAST_URL));
  return *appcast_url;
}

bool UpdaterRuntimeConfigured() {
  if (!BUILDFLAG(ENABLE_WINSPARKLE)) {
    return false;
  }

  const std::string_view public_key = BUILDFLAG(WINSPARKLE_ED_KEY);
  std::string decoded_key;
  if (!base::Base64Decode(public_key, &decoded_key) ||
      decoded_key.size() != 32 ||
      base::Base64Encode(decoded_key) != public_key) {
    return false;
  }

  const GURL& appcast_url = ConfiguredAppcastUrl();
  return appcast_url.is_valid() && appcast_url.SchemeIs(url::kHttpsScheme) &&
         appcast_url.has_host() && !appcast_url.has_username() &&
         !appcast_url.has_password() && !appcast_url.has_ref();
}

std::wstring& PendingSystemPayload() {
  static base::NoDestructor<std::wstring> path;
  return *path;
}
std::wstring& PendingSystemSignature() {
  static base::NoDestructor<std::wstring> sig;
  return *sig;
}

bool HasPendingUpdateSwap() {
  base::FilePath dir;
  if (!base::PathService::Get(base::DIR_EXE, &dir)) {
    return false;
  }
  const base::FilePath new_exe = dir.Append(installer::kChromeNewExe);
  return ::GetFileAttributesW(new_exe.value().c_str()) !=
         INVALID_FILE_ATTRIBUTES;
}

base::FilePath SystemUpdateMarkerPath() {
  base::FilePath dir;
  if (!base::PathService::Get(chrome::DIR_USER_DATA, &dir)) {
    return base::FilePath();
  }
  return dir.Append(FILE_PATH_LITERAL("PendingSystemUpdate"));
}

std::string SessionToken() {
  return base::NumberToString(base::Process::Current()
                                  .CreationTime()
                                  .ToDeltaSinceWindowsEpoch()
                                  .InMicroseconds());
}

enum class UpdatePromptResult {
  kUpdateNow,
  kRemindLater,
  kSkipVersion,
};

// Pure state rules are kept separate from Views and PrefService so the
// same-session and exact-version behavior remains compile-time testable.
constexpr bool IsNewUpdateDiscovery(std::string_view stored_version,
                                    std::string_view discovered_version) {
  return !discovered_version.empty() &&
         stored_version != discovered_version;
}

constexpr bool ShouldOfferStoredUpdate(std::string_view available_version,
                                       std::string_view skipped_version,
                                       std::string_view suppressed_session,
                                       std::string_view current_session) {
  return !available_version.empty() && available_version != skipped_version &&
         suppressed_session != current_session;
}

static_assert(!IsNewUpdateDiscovery("1.0.3", "1.0.3"));
static_assert(IsNewUpdateDiscovery("1.0.3", "1.0.4"));
static_assert(!ShouldOfferStoredUpdate("1.0.3", "", "session-a",
                                      "session-a"));
static_assert(!ShouldOfferStoredUpdate("1.0.3", "1.0.3", "session-a",
                                      "session-b"));
static_assert(ShouldOfferStoredUpdate("1.0.4", "1.0.3", "session-a",
                                     "session-b"));

PrefService* GetUpdaterLocalState() {
  return g_browser_process ? g_browser_process->local_state() : nullptr;
}

bool IsAvailableVersionNewerThanCurrent(std::string_view available_version) {
  const base::Version available{std::string(available_version)};
  const base::Version current{
      std::string(version_info::GetFocusVersionNumber())};
  return available.IsValid() && current.IsValid() &&
         current.CompareTo(available) < 0;
}

class FocusUpdatePrompt final : public views::DialogDelegate {
 public:
  using ResultCallback =
      base::OnceCallback<void(UpdatePromptResult, const std::string&)>;

  FocusUpdatePrompt(std::string version,
                    ResultCallback result_callback,
                    base::OnceClosure widget_zombie_callback)
      : version_(std::move(version)),
        result_callback_(std::move(result_callback)),
        widget_zombie_callback_(std::move(widget_zombie_callback)) {
    SetTitle(l10n_util::GetStringUTF16(IDS_FOCUS_UPDATE_PROMPT_TITLE));
    SetButtons(static_cast<int>(ui::mojom::DialogButton::kOk) |
               static_cast<int>(ui::mojom::DialogButton::kCancel));
    SetButtonLabel(
        ui::mojom::DialogButton::kOk,
        l10n_util::GetStringUTF16(IDS_FOCUS_UPDATE_PROMPT_UPDATE_NOW));
    SetButtonLabel(
        ui::mojom::DialogButton::kCancel,
        l10n_util::GetStringUTF16(IDS_FOCUS_UPDATE_PROMPT_REMIND_LATER));
    SetButtonStyle(ui::mojom::DialogButton::kOk,
                   ui::ButtonStyle::kProminent);
    SetButtonStyle(ui::mojom::DialogButton::kCancel,
                   ui::ButtonStyle::kTonal);
    SetDefaultButton(static_cast<int>(ui::mojom::DialogButton::kOk));
    SetAcceptCallback(base::BindOnce(&FocusUpdatePrompt::Resolve,
                                     base::Unretained(this),
                                     UpdatePromptResult::kUpdateNow));
    SetCancelCallback(base::BindOnce(&FocusUpdatePrompt::Resolve,
                                     base::Unretained(this),
                                     UpdatePromptResult::kRemindLater));
    SetCloseCallback(base::BindOnce(&FocusUpdatePrompt::Resolve,
                                    base::Unretained(this),
                                    UpdatePromptResult::kRemindLater));
    auto* skip_button =
        SetExtraView(std::make_unique<views::MdTextButton>(
            base::BindRepeating(&FocusUpdatePrompt::SkipVersion,
                                base::Unretained(this)),
            l10n_util::GetStringUTF16(
                IDS_FOCUS_UPDATE_PROMPT_SKIP_VERSION)));
    skip_button->SetStyle(ui::ButtonStyle::kTonal);

    SetModalType(ui::mojom::ModalType::kWindow);
    set_fixed_width(views::LayoutProvider::Get()->GetDistanceMetric(
        views::DISTANCE_MODAL_DIALOG_PREFERRED_WIDTH));
    views::LayoutProvider* provider = views::LayoutProvider::Get();
    auto contents = std::make_unique<views::View>();
    contents->SetLayoutManager(std::make_unique<views::BoxLayout>(
        views::BoxLayout::Orientation::kVertical,
        provider->GetInsetsMetric(views::InsetsMetric::INSETS_DIALOG),
        provider->GetDistanceMetric(
            views::DISTANCE_RELATED_CONTROL_VERTICAL)));

    auto message = std::make_unique<views::Label>(l10n_util::GetStringFUTF16(
        IDS_FOCUS_UPDATE_PROMPT_BODY, base::UTF8ToUTF16(version_)));
    message->SetMultiLine(true);
    message->SetHorizontalAlignment(gfx::ALIGN_LEFT);
    contents->AddChildView(std::move(message));
    SetContentsView(std::move(contents));
  }

  FocusUpdatePrompt(const FocusUpdatePrompt&) = delete;
  FocusUpdatePrompt& operator=(const FocusUpdatePrompt&) = delete;
  ~FocusUpdatePrompt() override = default;

  void ResolveIfPending(UpdatePromptResult result) { Resolve(result); }

 private:
  void WidgetIsZombie(views::Widget*) override {
    if (widget_zombie_callback_) {
      std::move(widget_zombie_callback_).Run();
    }
  }

  void Resolve(UpdatePromptResult result) {
    if (result_callback_) {
      std::move(result_callback_).Run(result, version_);
    }
  }

  void SkipVersion() {
    Resolve(UpdatePromptResult::kSkipVersion);
    if (GetWidget()) {
      GetWidget()->Close();
    }
  }

  const std::string version_;
  ResultCallback result_callback_;
  base::OnceClosure widget_zombie_callback_;
};

void PostDiscoveredUpdateVersion(std::string version);
void PostNoUpdateAvailable();

void MarkSystemUpdatePending() {
  const base::FilePath path = SystemUpdateMarkerPath();
  if (!path.empty()) {
    base::WriteFile(path, SessionToken());
  }
}

void ClearStaleSystemUpdateMarker() {
  const base::FilePath path = SystemUpdateMarkerPath();
  std::string token;
  if (!path.empty() && base::ReadFileToString(path, &token) &&
      token != SessionToken()) {
    base::DeleteFile(path);
  }
}

bool RunPerUserInstallerWait(const std::wstring& installer_path) {
  base::CommandLine command_line{base::FilePath(installer_path)};
  command_line.AppendSwitch("do-not-launch-chrome");
  base::Process process =
      base::LaunchProcess(command_line, base::LaunchOptions());
  if (!process.IsValid()) {
    LOG(ERROR) << "WinSparkle: failed to launch per-user update installer";
    return false;
  }

  int exit_code = -1;
  if (!process.WaitForExit(&exit_code) ||
      !installer::IsSuccessfulUpdateInstallerExitCode(exit_code)) {
    LOG(ERROR) << "WinSparkle: per-user installer failed, exit code "
               << exit_code;
    return false;
  }
  return true;
}

// Maps WinSparkle's status callbacks onto VersionUpdater::Status.
class WinSparkleStatusBroadcaster {
 public:
  using StatusCallbackList = base::RepeatingCallbackList<
      void(VersionUpdater::Status, int progress, const std::u16string&)>;

  static WinSparkleStatusBroadcaster& GetInstance() {
    static base::NoDestructor<WinSparkleStatusBroadcaster> instance;
    return *instance;
  }

  base::CallbackListSubscription Subscribe(
      StatusCallbackList::CallbackType callback) {
    return callbacks_.Add(std::move(callback));
  }

  void Notify(VersionUpdater::Status status,
              int progress,
              const std::u16string& message) {
    DCHECK_CURRENTLY_ON(content::BrowserThread::UI);
    callbacks_.Notify(status, progress, message);
  }

 private:
  StatusCallbackList callbacks_;
};

void PostStatus(VersionUpdater::Status status,
                int progress = 0,
                const std::u16string& message = std::u16string()) {
  content::GetUIThreadTaskRunner({})->PostTask(
      FROM_HERE,
      base::BindOnce(
          &WinSparkleStatusBroadcaster::Notify,
          base::Unretained(&WinSparkleStatusBroadcaster::GetInstance()), status,
          progress, message));
}

// WinSparkle C callbacks
void __cdecl OnDidFindUpdate() {
  char version[128] = {};
  if (win_sparkle_get_pending_update_version(version, sizeof(version)) > 0) {
    PostDiscoveredUpdateVersion(version);
  }
  PostStatus(VersionUpdater::UPDATING);
}

void __cdecl OnDidNotFindUpdate() {
  PostNoUpdateAvailable();
  PostStatus(VersionUpdater::UPDATED);
}

void __cdecl OnUpdateError() {
  char buf[512] = {};
  win_sparkle_get_last_error_message(buf, sizeof(buf));
  const std::string message(buf);
  LOG(ERROR) << "WinSparkle update error: "
             << (message.empty() ? "(no detail)" : message.c_str());
  if (message.empty()) {
    PostStatus(VersionUpdater::FAILED);
  } else {
    PostStatus(VersionUpdater::FAILED, /*progress=*/0,
               base::UTF8ToUTF16(message));
  }
}

void __cdecl OnDownloadProgress(size_t downloaded, size_t total) {
  size_t percent = 0;
  if (total > 0) {
    percent = std::clamp<size_t>(downloaded * 100 / total, 0, 100);
  }
  PostStatus(VersionUpdater::UPDATING, static_cast<int>(percent));
}

void StorePendingSystemUpdate(const std::wstring& payload,
                              const std::wstring& signature) {
  DCHECK_CURRENTLY_ON(content::BrowserThread::UI);
  PendingSystemPayload() = payload;
  PendingSystemSignature() = signature;
  g_nearly_updated.store(true, std::memory_order_release);
  WinSparkleStatusBroadcaster::GetInstance().Notify(
      VersionUpdater::NEARLY_UPDATED, /*progress=*/0, std::u16string());
}

// Invoked by WinSparkle once the update payload is downloaded and verified.
// Returning 1 tells WinSparkle we handled installation and it must not relaunch
// the app; the new version is applied on the next relaunch.
int __cdecl OnRunInstaller(const wchar_t* file_path) {
  if (install_static::IsSystemInstall()) {
    // System installs need elevation. We do not elevate now, instead record
    // the verified payload and apply it only when the user clicks Relaunch.
    char sig[256] = {};
    win_sparkle_get_pending_update_eddsa_signature(sig, sizeof(sig));
    // Drop the marker the update chip polls for.
    MarkSystemUpdatePending();
    content::GetUIThreadTaskRunner({})->PostTask(
        FROM_HERE,
        base::BindOnce(&StorePendingSystemUpdate, std::wstring(file_path),
                       base::UTF8ToWide(std::string(sig))));
    return 1;
  }

  // Per-user: no elevation needed. Stage the update silently now.
  if (!RunPerUserInstallerWait(file_path) || !HasPendingUpdateSwap()) {
    PostStatus(VersionUpdater::FAILED);
    return WINSPARKLE_RETURN_ERROR;
  }

  g_nearly_updated.store(true, std::memory_order_release);
  PostStatus(VersionUpdater::NEARLY_UPDATED);
  return 1;
}

void RegisterWinSparkleCallbacks() {
  win_sparkle_set_did_find_update_callback(&OnDidFindUpdate);
  win_sparkle_set_did_not_find_update_callback(&OnDidNotFindUpdate);
  win_sparkle_set_error_callback(&OnUpdateError);
  win_sparkle_set_download_progress_callback(&OnDownloadProgress);
  win_sparkle_set_user_run_installer_callback(&OnRunInstaller);
}

void ApplyAppcastUrl() {
  DCHECK(UpdaterRuntimeConfigured());
  win_sparkle_set_appcast_url(ConfiguredAppcastUrl().spec().c_str());
}

class WinSparkleController : public ProfileObserver,
                             public ProfileManagerObserver,
                             public BrowserCollectionObserver {
 public:
  static WinSparkleController& GetInstance() {
    static base::NoDestructor<WinSparkleController> instance;
    return *instance;
  }

  void Start(Profile* initial_profile) {
    if (started_) {
      return;
    }
    started_ = true;
    base::ThreadPool::PostTask(
        FROM_HERE, {base::MayBlock(), base::TaskPriority::BEST_EFFORT},
        base::BindOnce(&ClearStaleSystemUpdateMarker));
    if (ProfileManager* manager = GetProfileManager()) {
      profile_manager_observation_.Observe(manager);
    }
    if (GlobalBrowserCollection* browsers =
            GlobalBrowserCollection::GetInstance()) {
      browser_collection_observation_.Observe(browsers);
    }
    DiscardObsoleteStoredUpdate();
    AcquireProfile(initial_profile, /*exclude=*/nullptr);
    if (GlobalBrowserCollection* browsers =
            GlobalBrowserCollection::GetInstance()) {
      if (BrowserWindowInterface* active = browsers->GetActiveBrowser()) {
        MaybeShowUpdatePrompt(active);
      }
    }
  }

  void RecordDiscoveredVersion(const std::string& version) {
    DCHECK_CURRENTLY_ON(content::BrowserThread::UI);
    PrefService* local_state = GetUpdaterLocalState();
    if (!local_state || !IsAvailableVersionNewerThanCurrent(version)) {
      return;
    }

    const std::string stored =
        local_state->GetString(prefs::kFocusUpdaterAvailableVersion);
    if (!IsNewUpdateDiscovery(stored, version)) {
      return;
    }

    local_state->SetString(prefs::kFocusUpdaterAvailableVersion, version);
    local_state->SetString(prefs::kFocusUpdaterSuppressedSession,
                           SessionToken());
    if (local_state->GetString(prefs::kFocusUpdaterSkippedVersion) !=
        version) {
      local_state->ClearPref(prefs::kFocusUpdaterSkippedVersion);
    }
  }

  void ClearDiscoveredVersion() {
    DCHECK_CURRENTLY_ON(content::BrowserThread::UI);
    PrefService* local_state = GetUpdaterLocalState();
    if (!local_state) {
      return;
    }
    const std::string available =
        local_state->GetString(prefs::kFocusUpdaterAvailableVersion);
    local_state->ClearPref(prefs::kFocusUpdaterAvailableVersion);
    local_state->ClearPref(prefs::kFocusUpdaterSuppressedSession);
    if (!available.empty() &&
        local_state->GetString(prefs::kFocusUpdaterSkippedVersion) ==
            available) {
      local_state->ClearPref(prefs::kFocusUpdaterSkippedVersion);
    }
  }

  // BrowserCollectionObserver:
  void OnBrowserActivated(BrowserWindowInterface* browser) override {
    MaybeShowUpdatePrompt(browser);
  }

  // ProfileManagerObserver:
  void OnProfileAdded(Profile* new_profile) override {
    if (!profile_) {
      AcquireProfile(new_profile, /*exclude=*/nullptr);
    } else if (!ProfileCanUpdate(profile_) && ProfileCanUpdate(new_profile) &&
               IsCandidate(new_profile)) {
      // The active profile isn't asking for updates but this new
      // one is; let it take over.
      Unbind();
      Bind(new_profile);
    }
  }

  void OnProfileManagerDestroying() override {
    profile_manager_observation_.Reset();
  }

  // ProfileObserver:
  void OnProfileWillBeDestroyed(Profile* profile) override {
    DCHECK_EQ(profile, profile_);
    Unbind();
    AcquireProfile(/*preferred=*/nullptr, /*exclude=*/profile);
  }

 private:
  friend class base::NoDestructor<WinSparkleController>;
  WinSparkleController() = default;
  ~WinSparkleController() override = default;

  static ProfileManager* GetProfileManager() {
    return g_browser_process ? g_browser_process->profile_manager() : nullptr;
  }

  static bool IsCandidate(Profile* profile) {
    return profile && profile->IsRegularProfile();
  }

  static bool ProfileCanUpdate(Profile* profile) {
    return profile && focus::WinSparkleEnabled(profile->GetPrefs());
  }

  void DiscardObsoleteStoredUpdate() {
    PrefService* local_state = GetUpdaterLocalState();
    if (!local_state) {
      return;
    }
    const std::string available =
        local_state->GetString(prefs::kFocusUpdaterAvailableVersion);
    if (available.empty() || IsAvailableVersionNewerThanCurrent(available)) {
      return;
    }
    local_state->ClearPref(prefs::kFocusUpdaterAvailableVersion);
    local_state->ClearPref(prefs::kFocusUpdaterSuppressedSession);
    if (local_state->GetString(prefs::kFocusUpdaterSkippedVersion) ==
        available) {
      local_state->ClearPref(prefs::kFocusUpdaterSkippedVersion);
    }
  }

  void MaybeShowUpdatePrompt(BrowserWindowInterface* browser) {
    DCHECK_CURRENTLY_ON(content::BrowserThread::UI);
    if (prompt_visible_ || !browser ||
        browser->GetType() != BrowserWindowInterface::TYPE_NORMAL ||
        !browser->GetProfile() ||
        !browser->GetProfile()->IsRegularProfile() ||
        !ProfileCanUpdate(browser->GetProfile())) {
      return;
    }

    PrefService* local_state = GetUpdaterLocalState();
    if (!local_state) {
      return;
    }
    const std::string available =
        local_state->GetString(prefs::kFocusUpdaterAvailableVersion);
    if (!IsAvailableVersionNewerThanCurrent(available) ||
        !ShouldOfferStoredUpdate(
            available,
            local_state->GetString(prefs::kFocusUpdaterSkippedVersion),
            local_state->GetString(prefs::kFocusUpdaterSuppressedSession),
            SessionToken())) {
      return;
    }

    Browser* legacy_browser = browser->GetBrowserForMigrationOnly();
    if (!legacy_browser || !legacy_browser->window()) {
      return;
    }

    // Mark the process session before creating the widget. This makes both
    // window close and any activation reentrancy a single prompt per launch.
    local_state->SetString(prefs::kFocusUpdaterSuppressedSession,
                           SessionToken());
    prompt_visible_ = true;
    auto prompt = std::make_unique<FocusUpdatePrompt>(
        available,
        base::BindOnce(&WinSparkleController::OnUpdatePromptResult,
                       base::Unretained(this)),
        base::BindOnce(
            &WinSparkleController::OnUpdatePromptWidgetBecameZombie,
            base::Unretained(this)));
    prompt->SetOwnershipOfNewWidget(
        views::Widget::InitParams::CLIENT_OWNS_WIDGET);
    update_prompt_ = std::move(prompt);
    update_prompt_widget_.reset(views::DialogDelegate::CreateDialogWidget(
        update_prompt_.get(), legacy_browser->window()->GetNativeWindow(),
        nullptr));
    update_prompt_widget_->MakeCloseSynchronous(base::BindOnce(
        &WinSparkleController::OnUpdatePromptCloseRequested,
        base::Unretained(this)));
    update_prompt_widget_->Show();
  }

  void OnUpdatePromptResult(UpdatePromptResult result,
                            const std::string& version) {
    DCHECK_CURRENTLY_ON(content::BrowserThread::UI);
    switch (result) {
      case UpdatePromptResult::kUpdateNow:
        // This is the only prompt action that starts an update download.
        win_sparkle_check_update_with_ui_and_install();
        return;
      case UpdatePromptResult::kRemindLater:
        return;
      case UpdatePromptResult::kSkipVersion:
        if (PrefService* local_state = GetUpdaterLocalState()) {
          const std::string available = local_state->GetString(
              prefs::kFocusUpdaterAvailableVersion);
          if (available == version) {
            local_state->SetString(prefs::kFocusUpdaterSkippedVersion,
                                   version);
          }
        }
        return;
    }
  }

  void OnUpdatePromptCloseRequested(views::Widget::ClosedReason) {
    ScheduleUpdatePromptDestruction();
  }

  void OnUpdatePromptWidgetBecameZombie() {
    ScheduleUpdatePromptDestruction();
  }

  void ScheduleUpdatePromptDestruction() {
    DCHECK_CURRENTLY_ON(content::BrowserThread::UI);
    if (update_prompt_) {
      update_prompt_->ResolveIfPending(UpdatePromptResult::kRemindLater);
    }
    if (prompt_destruction_scheduled_) {
      return;
    }
    prompt_destruction_scheduled_ = true;
    content::GetUIThreadTaskRunner({})->PostTask(
        FROM_HERE,
        base::BindOnce(&WinSparkleController::DestroyUpdatePrompt,
                       base::Unretained(this)));
  }

  void DestroyUpdatePrompt() {
    DCHECK_CURRENTLY_ON(content::BrowserThread::UI);
    update_prompt_widget_.reset();
    update_prompt_.reset();
    prompt_destruction_scheduled_ = false;
    prompt_visible_ = false;
  }

  // Adopts a profile to drive updates if none is active. No-op if one
  // is already active or none qualifies.
  void AcquireProfile(Profile* preferred, Profile* exclude) {
    if (profile_) {
      return;
    }

    if (preferred != exclude && IsCandidate(preferred)) {
      Bind(preferred);
      return;
    }

    ProfileManager* manager = GetProfileManager();
    if (!manager) {
      return;
    }

    Profile* fallback = nullptr;
    for (Profile* profile : manager->GetLoadedProfiles()) {
      if (profile == exclude || !IsCandidate(profile)) {
        continue;
      }
      if (ProfileCanUpdate(profile)) {
        Bind(profile);
        return;
      }
      if (!fallback) {
        fallback = profile;
      }
    }
    if (fallback) {
      Bind(fallback);
    }
  }

  void Bind(Profile* profile) {
    DCHECK(!profile_);
    profile_ = profile;
    profile_observation_.Observe(profile);
    registrar_.Init(profile->GetPrefs());
    registrar_.Add(
        prefs::kFocusUpdateFetchingEnabled,
        base::BindRepeating(&WinSparkleController::ApplyUpdaterState,
                            base::Unretained(this)));
    ApplyUpdaterState();
  }

  void Unbind() {
    registrar_.RemoveAll();
    profile_observation_.Reset();
    profile_ = nullptr;
    win_sparkle_set_automatic_check_for_updates(0);
  }

  void ApplyUpdaterState() {
    if (!profile_) {
      return;
    }
    PrefService* prefs = profile_->GetPrefs();
    const bool enabled = focus::WinSparkleEnabled(prefs);

    if (!initialized_) {
      if (!enabled) {
        return;
      }
      initialized_ = true;

      base::FilePath module_dir;
      if (base::PathService::Get(base::DIR_MODULE, &module_dir)) {
        ::LoadLibraryW(
            module_dir.Append(FILE_PATH_LITERAL("WinSparkle.dll"))
                .value()
                .c_str());
      }

      const std::wstring version =
          base::UTF8ToWide(std::string(version_info::GetFocusVersionNumber()));
      win_sparkle_set_app_details(L"Focus Browser", L"Focus Browser",
                                  version.c_str());
      win_sparkle_set_eddsa_public_key(BUILDFLAG(WINSPARKLE_ED_KEY));
      RegisterWinSparkleCallbacks();
      win_sparkle_set_update_check_interval(
          BUILDFLAG(WINSPARKLE_CHECK_INTERVAL));
      win_sparkle_set_automatic_check_for_updates(
          BUILDFLAG(WINSPARKLE_AUTOMATIC_CHECKS));

      ApplyAppcastUrl();
      win_sparkle_init();
      return;
    }

    // Already running: reflect the current consent live (no re-init).
    win_sparkle_set_automatic_check_for_updates(
        enabled ? BUILDFLAG(WINSPARKLE_AUTOMATIC_CHECKS) : 0);
  }

  raw_ptr<Profile> profile_ = nullptr;
  bool initialized_ = false;
  bool started_ = false;
  bool prompt_visible_ = false;
  bool prompt_destruction_scheduled_ = false;
  std::unique_ptr<FocusUpdatePrompt> update_prompt_;
  std::unique_ptr<views::Widget> update_prompt_widget_;
  PrefChangeRegistrar registrar_;
  base::ScopedObservation<Profile, ProfileObserver> profile_observation_{this};
  base::ScopedObservation<ProfileManager, ProfileManagerObserver>
      profile_manager_observation_{this};
  base::ScopedObservation<GlobalBrowserCollection, BrowserCollectionObserver>
      browser_collection_observation_{this};
};

void PostDiscoveredUpdateVersion(std::string version) {
  content::GetUIThreadTaskRunner({})->PostTask(
      FROM_HERE,
      base::BindOnce(&WinSparkleController::RecordDiscoveredVersion,
                     base::Unretained(&WinSparkleController::GetInstance()),
                     std::move(version)));
}

void PostNoUpdateAvailable() {
  content::GetUIThreadTaskRunner({})->PostTask(
      FROM_HERE,
      base::BindOnce(&WinSparkleController::ClearDiscoveredVersion,
                     base::Unretained(&WinSparkleController::GetInstance())));
}

class VersionUpdaterWinSparkle : public VersionUpdater {
 public:
  explicit VersionUpdaterWinSparkle(bool enabled) : enabled_(enabled) {
    subscription_ = WinSparkleStatusBroadcaster::GetInstance().Subscribe(
        base::BindRepeating(&VersionUpdaterWinSparkle::OnStatus,
                            weak_factory_.GetWeakPtr()));
  }

  VersionUpdaterWinSparkle(const VersionUpdaterWinSparkle&) = delete;
  VersionUpdaterWinSparkle& operator=(const VersionUpdaterWinSparkle&) = delete;

  ~VersionUpdaterWinSparkle() override = default;

  // VersionUpdater:
  void CheckForUpdate(StatusCallback status_callback,
                      PromoteCallback /*promote_callback*/) override {
    status_callback_ = std::move(status_callback);

    if (!enabled_) {
      RunStatus(DISABLED);
      return;
    }

    if (ApplicationIsNearlyUpdated()) {
      RunStatus(NEARLY_UPDATED);
      return;
    }

    RunStatus(CHECKING);
    win_sparkle_check_update_with_ui_and_install();
  }

 private:
  void RunStatus(Status status,
                 int progress = 0,
                 const std::u16string& message = {}) {
    if (status_callback_.is_null()) {
      return;
    }
    status_callback_.Run(status, progress, /*rollback=*/false,
                         /*powerwash=*/false, /*version=*/std::string(),
                         /*update_size=*/0, message);
  }

  void OnStatus(VersionUpdater::Status status,
                int progress,
                const std::u16string& message) {
    RunStatus(status, progress, message);
  }

  const bool enabled_;
  StatusCallback status_callback_;
  base::CallbackListSubscription subscription_;
  base::WeakPtrFactory<VersionUpdaterWinSparkle> weak_factory_{this};
};

}  // namespace

bool WinSparkleEnabled(PrefService* prefs) {
  return UpdaterRuntimeConfigured() && prefs &&
         focus::ShouldAccessUpdateService(*prefs);
}

void InitializeWinSparkle(Profile* profile) {
  WinSparkleController::GetInstance().Start(profile);
}

bool ApplicationIsNearlyUpdated() {
  return g_nearly_updated.load(std::memory_order_acquire) ||
         HasPendingUpdateSwap();
}

bool MaybeElevateSystemUpdateOnRelaunch() {
  DCHECK_CURRENTLY_ON(content::BrowserThread::UI);

  // Per-user installs apply with no elevation.
  if (!install_static::IsSystemInstall()) {
    return false;
  }

  // No verified system update waiting.
  if (PendingSystemPayload().empty()) {
    return false;
  }

  // The apply helper ships next to chrome.dll in the version dir.
  base::FilePath module_dir;
  if (!base::PathService::Get(base::DIR_MODULE, &module_dir)) {
    return false;
  }
  const base::FilePath helper =
      module_dir.Append(FILE_PATH_LITERAL("focus_update_helper.exe"));

  const std::wstring params = L"--payload=\"" + PendingSystemPayload() +
                              L"\" --signature=\"" + PendingSystemSignature() +
                              L"\" --wait-pid=" +
                              base::NumberToWString(::GetCurrentProcessId());

  SHELLEXECUTEINFOW sei = {sizeof(sei)};
  sei.fMask = SEE_MASK_NOASYNC;
  sei.lpVerb = L"runas";
  sei.lpFile = helper.value().c_str();
  sei.lpParameters = params.c_str();
  sei.nShow = SW_SHOWNORMAL;
  if (!::ShellExecuteExW(&sei)) {
    LOG(WARNING) << "WinSparkle: elevated update apply not started, err="
                 << ::GetLastError();
    return false;
  }
  return true;
}

}  // namespace focus

std::unique_ptr<VersionUpdater> VersionUpdater::Create(
    content::WebContents* web_contents) {
  PrefService* prefs = nullptr;
  if (web_contents) {
    if (auto* profile =
            Profile::FromBrowserContext(web_contents->GetBrowserContext())) {
      prefs = profile->GetPrefs();
    }
  }
  return std::make_unique<focus::VersionUpdaterWinSparkle>(
      focus::WinSparkleEnabled(prefs));
}
