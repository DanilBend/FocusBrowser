#!/usr/bin/env python3
"""Source-level regression checks for the Focus startup updater flow."""

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OVERRIDE = ROOT / "source_overrides/chrome/browser/win/winsparkle_glue.cc"
ACTIVE = ROOT / "build/src/chrome/browser/win/winsparkle_glue.cc"
PATCH = ROOT / "patches/focus/windows/updater/glue.patch"
QA_RUNTIME = ROOT / "qa/verify_startup_update_prompt.ps1"


def function_body(source: str, signature: str) -> str:
    start = source.find(signature)
    if start < 0:
        raise AssertionError(f"missing function: {signature}")
    opening = source.find("{", start)
    if opening < 0:
        raise AssertionError(f"missing function body: {signature}")
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[opening + 1:index]
    raise AssertionError(f"unterminated function body: {signature}")


class StartupUpdatePromptTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = OVERRIDE.read_text(encoding="utf-8")
        cls.qa_runtime = QA_RUNTIME.read_text(encoding="utf-8")

    def test_runtime_qa_quotes_user_data_dir_with_spaces(self):
        self.assertIn(
            "('--user-data-dir=\"{0}\"' -f $userData)",
            self.qa_runtime,
        )
        self.assertNotIn('"--user-data-dir=$userData"', self.qa_runtime)
        self.assertIn("-ArgumentList $launchArguments -PassThru", self.qa_runtime)

    def test_runtime_qa_enumerates_top_level_panes(self):
        top_level_scan = re.compile(
            r"RootElement\.FindAll\(\s*"
            r"\[System\.Windows\.Automation\.TreeScope\]::Children,\s*"
            r"\[System\.Windows\.Automation\.Condition\]::TrueCondition\s*\)",
            re.MULTILINE,
        )
        self.assertRegex(self.qa_runtime, top_level_scan)
        self.assertNotIn("$windowCondition", self.qa_runtime)

    def test_runtime_qa_rejects_stale_cache_without_requiring_a_prompt(self):
        self.assertIn(
            "RejectStaleCachedOfferBeforeCurrentFeedDiscovery",
            self.qa_runtime,
        )
        self.assertIn("SeededVersion = '9.9.9.9'", self.qa_runtime)
        self.assertIn("$hasSeededVersion", self.qa_runtime)
        self.assertIn("StalePromptDetected", self.qa_runtime)
        self.assertIn("CachedSentinelRevalidated", self.qa_runtime)
        self.assertIn("QuietObservationWindowCompleted", self.qa_runtime)
        self.assertIn(
            "Stale cached update was not offered before feed discovery",
            self.qa_runtime,
        )
        self.assertNotIn(
            "The startup update prompt did not appear",
            self.qa_runtime,
        )
        self.assertNotIn(
            "Startup update prompt appeared without opening Settings/About",
            self.qa_runtime,
        )

    def test_override_active_source_and_canonical_patch_match(self):
        if ACTIVE.is_file():
            self.assertEqual(OVERRIDE.read_bytes(), ACTIVE.read_bytes())

        lines = PATCH.read_text(encoding="utf-8").splitlines()
        second_file = lines.index("--- /dev/null", 1)
        match = re.fullmatch(r"@@ -0,0 \+1,(\d+) @@", lines[2])
        self.assertIsNotNone(match)
        reconstructed = [line[1:] for line in lines[3:second_file]]
        expected = self.source.splitlines()
        self.assertEqual(int(match.group(1)), len(expected))
        self.assertEqual(reconstructed, expected)

    def test_browser_start_runs_immediate_background_check(self):
        created = function_body(self.source, "void OnBrowserCreated(")
        activated = function_body(self.source, "void OnBrowserActivated(")
        for body in (created, activated):
            self.assertIn("MaybeShowUpdatePrompt(browser);", body)
            self.assertIn("MaybeStartStartupCheck(browser);", body)

        start = function_body(self.source, "void MaybeStartStartupCheck(")
        self.assertIn("startup_check_started_ = RequestBackgroundCheck();", start)
        self.assertNotIn("startup_check_started_ = true;", start)

        request = function_body(self.source, "bool RequestBackgroundCheck(")
        self.assertIn("check_state_ = CheckState::kBackground;", request)
        self.assertIn("win_sparkle_check_update_without_ui();", request)
        self.assertIn("return true;", request)

    def test_startup_retry_waits_for_a_native_browser_window(self):
        prompt = function_body(self.source, "void MaybeShowUpdatePrompt(")
        self.assertIn(
            "if (!legacy_browser || !legacy_browser->window())", prompt
        )
        self.assertIn("startup prompt waiting for a native window", prompt)
        self.assertLess(
            prompt.index("if (!legacy_browser || !legacy_browser->window())"),
            prompt.index(
                "local_state->SetString(prefs::kFocusUpdaterSuppressedSession"
            ),
        )

        startup_check = function_body(
            self.source, "void MaybeStartStartupCheck("
        )
        self.assertIn("browser->GetBrowserForMigrationOnly()", startup_check)
        self.assertIn("!legacy_browser || !legacy_browser->window()", startup_check)

        created = function_body(self.source, "void OnBrowserCreated(")
        self.assertIn("ScheduleStartupUiRetry();", created)
        schedule = function_body(self.source, "void ScheduleStartupUiRetry(")
        self.assertIn("base::Milliseconds(100)", schedule)
        self.assertIn("RetryStartupUi", schedule)
        retry = function_body(self.source, "void RetryStartupUi(")
        self.assertIn("startup_ui_retry_count_++ >= 50", retry)
        self.assertIn("MaybeShowUpdatePrompt(browser);", retry)
        self.assertIn("MaybeStartStartupCheck(browser);", retry)
        self.assertIn("prompt_visible_ || startup_check_started_", retry)

    def test_startup_paths_use_the_last_active_browser(self):
        start = function_body(self.source, "void Start(Profile* initial_profile)")
        record = function_body(self.source, "bool RecordDiscoveredVersion(")
        retry = function_body(self.source, "void RetryStartupUi(")
        for body in (start, record, retry):
            self.assertIn("GetLastActiveBrowser()", body)
            self.assertNotIn("GetActiveBrowser()", body)

    def test_native_hourly_gate_is_disabled_and_focus_owns_timer(self):
        apply_state = function_body(self.source, "void ApplyUpdaterState(")
        self.assertGreaterEqual(
            apply_state.count("win_sparkle_set_automatic_check_for_updates(0)"),
            2,
        )
        self.assertIn("StartPeriodicCheckTimer();", apply_state)
        timer = function_body(self.source, "void StartPeriodicCheckTimer(")
        self.assertIn("WINSPARKLE_CHECK_INTERVAL", timer)
        self.assertIn("RequestPeriodicBackgroundCheck", timer)

    def test_discovery_can_offer_in_the_same_session(self):
        record = function_body(self.source, "bool RecordDiscoveredVersion(")
        self.assertIn("if (offer_immediately)", record)
        self.assertIn("kFocusUpdaterSuppressedSession", record)
        self.assertIn(
            "MaybeShowUpdatePrompt(browsers->GetLastActiveBrowser());", record
        )

        found = function_body(self.source, "void OnUpdateFound(")
        self.assertIn("/*offer_immediately=*/!run_interactive", found)
        background_tail = found.split("check_state_ = CheckState::kIdle;", 1)[1]
        self.assertNotIn("PostStatus(VersionUpdater::UPDATING)", background_tail)

    def test_about_and_prompt_checks_do_not_leave_duplicate_ui(self):
        about = function_body(self.source, "void RequestInteractiveCheckFromAbout(")
        self.assertIn("dismiss_custom_prompt=*/true", about)
        request = function_body(self.source, "void RequestInteractiveCheck(")
        self.assertIn("update_prompt_widget_->Close();", request)

        result = function_body(self.source, "void OnUpdatePromptResult(")
        self.assertIn("dismiss_custom_prompt=*/false", result)
        self.assertIn("queued_interactive_check_timer_", self.source)
        self.assertIn("RunQueuedInteractiveCheck", self.source)

    def test_prompt_model_keeps_all_three_exact_version_actions(self):
        prompt = function_body(
            self.source,
            "FocusUpdatePrompt(std::string version,",
        )
        for resource_id in (
            "IDS_FOCUS_UPDATE_PROMPT_UPDATE_NOW",
            "IDS_FOCUS_UPDATE_PROMPT_REMIND_LATER",
            "IDS_FOCUS_UPDATE_PROMPT_SKIP_VERSION",
            "IDS_FOCUS_UPDATE_PROMPT_BODY",
        ):
            self.assertIn(resource_id, prompt)
        self.assertIn("base::UTF8ToUTF16(version_)", prompt)

        result = function_body(self.source, "void OnUpdatePromptResult(")
        for action in (
            "UpdatePromptResult::kUpdateNow",
            "UpdatePromptResult::kRemindLater",
            "UpdatePromptResult::kSkipVersion",
        ):
            self.assertIn(action, result)
        self.assertIn("if (available == version)", result)
        self.assertIn("kFocusUpdaterSkippedVersion", result)

    def test_callback_failures_release_the_controller_state(self):
        callback = function_body(self.source, "void __cdecl OnDidFindUpdate(")
        self.assertIn("PostUpdateError", callback)
        self.assertIn("return;", callback)
        error = function_body(
            self.source,
            "void OnUpdateError(const std::u16string& message,",
        )
        self.assertIn("RuntimeAcceptsCallback(generation)", error)
        self.assertIn("FinishUpdateCheckWithError(message);", error)
        finish = function_body(self.source, "void FinishUpdateCheckWithError(")
        self.assertIn("check_state_ = CheckState::kIdle;", finish)

    def test_shutdown_hook_cleans_up_the_native_runtime_once(self):
        self.assertIn(
            '#include "chrome/browser/lifetime/termination_notification.h"',
            self.source,
        )
        start = function_body(self.source, "void Start(Profile* initial_profile)")
        self.assertIn("AddAppTerminatingCallback", start)
        self.assertIn("OnAppTerminating", start)
        self.assertIn(
            "base::CallbackListSubscription app_terminating_subscription_;",
            self.source,
        )

        terminating = function_body(self.source, "void OnAppTerminating(")
        self.assertIn("shutdown_started_ = true;", terminating)
        self.assertIn("StopUpdaterRuntime();", terminating)

        stop = function_body(self.source, "void StopUpdaterRuntime(")
        self.assertEqual(stop.count("win_sparkle_cleanup();"), 1)
        self.assertLess(
            stop.index("initialized_ = false;"),
            stop.index("win_sparkle_cleanup();"),
        )
        self.assertIn("if (!was_initialized)", stop)

    def test_disabling_update_fetch_stops_and_invalidates_runtime(self):
        apply_state = function_body(self.source, "void ApplyUpdaterState(")
        disabled = apply_state.split("if (!enabled)", 1)[1].split("}", 1)[0]
        self.assertIn("StopUpdaterRuntime();", disabled)

        stop = function_body(self.source, "void StopUpdaterRuntime(")
        self.assertLess(
            stop.index("g_updater_callbacks_enabled.store(false"),
            stop.index("win_sparkle_cleanup();"),
        )
        self.assertIn("g_updater_callback_generation.fetch_add", stop)
        self.assertIn("weak_factory_.InvalidateWeakPtrs();", stop)
        self.assertIn("queued_interactive_check_timer_.Stop();", stop)

    def test_late_callbacks_are_generation_gated(self):
        self.assertIn("UpdaterCallbacksEnabledForGeneration", self.source)
        for signature in (
            "void __cdecl OnDidFindUpdate(",
            "void __cdecl OnDidNotFindUpdate(",
            "void __cdecl OnUpdateError(",
            "void __cdecl OnDownloadProgress(",
        ):
            body = function_body(self.source, signature)
            self.assertIn("UpdaterCallbacksEnabledForGeneration(generation)", body)

        for signature in (
            "void OnUpdateFound(",
            "void OnNoUpdateAvailable(",
            "void OnUpdateError(const std::u16string& message,",
        ):
            body = function_body(self.source, signature)
            self.assertIn("RuntimeAcceptsCallback(generation)", body)

    def test_background_error_preserves_pending_interactive_check(self):
        finish = function_body(self.source, "void FinishUpdateCheckWithError(")
        self.assertIn("check_state_ == CheckState::kBackground", finish)
        self.assertIn("interactive_check_pending_", finish)
        self.assertIn("QueueInteractiveCheckAfterWorkerExit();", finish)
        queue = function_body(
            self.source, "void QueueInteractiveCheckAfterWorkerExit("
        )
        self.assertIn("queued_interactive_check_timer_.Start", queue)
        self.assertIn("RunQueuedInteractiveCheck", queue)

    def test_cached_offer_requires_a_successful_current_feed_discovery(self):
        prompt = function_body(self.source, "void MaybeShowUpdatePrompt(")
        self.assertIn("!current_feed_discovery_succeeded_", prompt)

        record = function_body(self.source, "bool RecordDiscoveredVersion(")
        self.assertIn("IsAvailableVersionNewerThanCurrent(version)", record)
        self.assertIn("current_feed_discovery_succeeded_ = true;", record)
        self.assertLess(
            record.index("current_feed_discovery_succeeded_ = true;"),
            record.index("MaybeShowUpdatePrompt"),
        )

        background = function_body(self.source, "bool RequestBackgroundCheck(")
        interactive = function_body(self.source, "void StartInteractiveCheck(")
        for body in (background, interactive):
            self.assertIn("current_feed_discovery_succeeded_ = false;", body)

    def test_temporary_no_update_keeps_exact_version_skip(self):
        clear = function_body(self.source, "void ClearDiscoveredVersion(")
        self.assertIn("kFocusUpdaterAvailableVersion", clear)
        self.assertIn("kFocusUpdaterSuppressedSession", clear)
        self.assertNotIn("kFocusUpdaterSkippedVersion", clear)


if __name__ == "__main__":
    unittest.main()
