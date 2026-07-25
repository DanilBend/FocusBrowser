// Copyright 2026 The Focus Browser Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#include "chrome/browser/ui/views/toolbar/focus_youtube_bubble_view.h"

#include <array>
#include <memory>
#include <optional>
#include <string_view>
#include <utility>
#include <vector>

#include "base/functional/bind.h"
#include "base/location.h"
#include "base/memory/scoped_refptr.h"
#include "base/strings/string_number_conversions.h"
#include "base/task/sequenced_task_runner.h"
#include "base/time/time.h"
#include "chrome/browser/profiles/profile.h"
#include "chrome/browser/ui/browser.h"
#include "chrome/browser/ui/tabs/tab_strip_model.h"
#include "components/focus_services/extension_ids.h"
#include "content/public/browser/navigation_handle.h"
#include "content/public/browser/web_contents.h"
#include "extensions/browser/api/storage/storage_area_namespace.h"
#include "extensions/browser/api/storage/storage_frontend.h"
#include "extensions/browser/extension_registry.h"
#include "extensions/common/extension.h"
#include "ui/base/l10n/l10n_util.h"
#include "ui/base/metadata/metadata_impl_macros.h"
#include "ui/base/mojom/dialog_button.mojom.h"
#include "ui/base/ui_base_types.h"
#include "ui/color/color_id.h"
#include "ui/gfx/geometry/insets.h"
#include "ui/gfx/text_constants.h"
#include "ui/views/accessibility/view_accessibility.h"
#include "ui/views/background.h"
#include "ui/views/border.h"
#include "ui/views/controls/button/md_text_button.h"
#include "ui/views/controls/button/toggle_button.h"
#include "ui/views/controls/label.h"
#include "ui/views/controls/tabbed_pane/tabbed_pane.h"
#include "ui/views/layout/box_layout.h"
#include "ui/views/layout/flex_layout.h"
#include "ui/views/view.h"
#include "ui/views/view_class_properties.h"
#include "ui/views/widget/widget.h"
#include "url/url_constants.h"

namespace {

constexpr int kBubbleWidth = 420;
constexpr int kCardRadius = 12;
constexpr int kSectionSpacing = 10;
constexpr int kRowSpacing = 6;
constexpr int kFocusYoutubeSchemaVersion = 4;
constexpr int kSettingsLoadMaxAttempts = 40;
constexpr base::TimeDelta kSettingsLoadRetryDelay = base::Milliseconds(50);

struct FeatureSpec {
  size_t group;
  std::string_view key;
  std::string_view companion_key_1;
  std::string_view companion_key_2;
  std::u16string_view english;
  std::u16string_view russian;
};

struct GroupSpec {
  std::u16string_view english;
  std::u16string_view russian;
};

constexpr FeatureSpec Feature(size_t group,
                              std::string_view key,
                              std::u16string_view english,
                              std::u16string_view russian) {
  return {group, key, std::string_view(), std::string_view(), english,
          russian};
}

constexpr FeatureSpec CompositeFeature(size_t group,
                                       std::string_view key,
                                       std::string_view companion_key_1,
                                       std::string_view companion_key_2,
                                       std::u16string_view english,
                                       std::u16string_view russian) {
  return {group, key, companion_key_1, companion_key_2, english, russian};
}

constexpr std::array<GroupSpec, 4> kGroups = {{
    {u"Feed", u"Лента"},
    {u"Player", u"Плеер"},
    {u"Interface", u"Интерфейс"},
    {u"Navigation", u"Навигация"},
}};

// These are the controls exposed by the original Unhook 1.6.9 surface. The
// implementation uses FocusYoutube's existing compatible storage schema; the
// two compound controls write all of their related keys atomically.
constexpr std::array<FeatureSpec, 25> kFeatures = {{
    Feature(0, "remove_homepage", u"Hide homepage feed",
            u"Скрывать ленту на главной"),
    Feature(0, "remove_entire_sidebar", u"Hide video sidebar",
            u"Скрывать боковую колонку видео"),
    Feature(0, "remove_sidebar", u"Hide recommended videos",
            u"Скрывать рекомендованные видео"),
    Feature(0, "remove_chat", u"Hide live chat",
            u"Скрывать чат трансляций"),
    Feature(0, "remove_playlist_panel", u"Hide playlist",
            u"Скрывать плейлист"),
    Feature(0, "remove_all_shorts", u"Hide YouTube Shorts",
            u"Скрывать YouTube Shorts"),

    Feature(1, "remove_end_of_video", u"Hide end-screen videowall",
            u"Скрывать видеостену в конце видео"),
    Feature(1, "remove_info_cards", u"Hide end-screen cards",
            u"Скрывать карточки в конце видео"),
    Feature(1, "remove_mixes", u"Hide Mix radio playlists",
            u"Скрывать миксы и радиоплейлисты"),
    Feature(1, "remove_video_metadata", u"Hide video info",
            u"Скрывать информацию о видео"),
    Feature(1, "disable_autoplay", u"Disable autoplay",
            u"Отключать автовоспроизведение"),
    Feature(1, "disable_annotations", u"Disable annotations",
            u"Отключать аннотации"),

    Feature(2, "remove_comments", u"Hide comments", u"Скрывать комментарии"),
    Feature(2, "remove_comment_profiles", u"Hide profile photos",
            u"Скрывать фотографии профилей"),
    Feature(2, "remove_merch_shelves", u"Hide merch, tickets and offers",
            u"Скрывать товары, билеты и предложения"),
    Feature(2, "remove_menu_buttons", u"Hide video buttons bar",
            u"Скрывать панель кнопок видео"),
    Feature(2, "remove_channel_owner", u"Hide channel",
            u"Скрывать блок канала"),
    Feature(2, "remove_vid_description", u"Hide video description",
            u"Скрывать описание видео"),
    Feature(2, "remove_top_header", u"Hide top header",
            u"Скрывать верхнюю панель YouTube"),

    Feature(3, "remove_notif_bell", u"Hide notification bell",
            u"Скрывать значок уведомлений"),
    Feature(3, "remove_extra_results", u"Hide irrelevant search results",
            u"Скрывать нерелевантные результаты поиска"),
    CompositeFeature(3, "remove_trending_page", "remove_explore_link",
                     "remove_explore_section", u"Hide Explore and Trending",
                     u"Скрывать «Навигатор» и тренды"),
    Feature(3, "remove_more_section", u"Hide More from YouTube",
            u"Скрывать раздел «Другие возможности YouTube»"),
    CompositeFeature(3, "remove_subscriptions_page",
                     "remove_subscriptions_link", "remove_sub_section",
                     u"Hide subscriptions", u"Скрывать подписки"),
    Feature(3, "redirect_to_subs", u"Redirect home to subscriptions",
            u"Открывать подписки вместо главной"),
}};

std::array<std::string_view, 3> StorageKeys(const FeatureSpec& feature) {
  return {feature.key, feature.companion_key_1, feature.companion_key_2};
}

const FeatureSpec* FindFeature(std::string_view key) {
  for (const FeatureSpec& feature : kFeatures) {
    if (feature.key == key) {
      return &feature;
    }
  }
  return nullptr;
}

bool IsFeatureEnabled(const base::DictValue& values,
                      const FeatureSpec& feature) {
  for (std::string_view key : StorageKeys(feature)) {
    if (!key.empty() && !values.FindBool(key).value_or(false)) {
      return false;
    }
  }
  return true;
}

bool UseRussianFocusUi() {
  const std::string locale =
      l10n_util::GetApplicationLocale(std::string(), false);
  return locale == "ru" || locale.starts_with("ru-") ||
         locale.starts_with("ru_");
}

bool IsFocusYoutubeUrl(const GURL& url) {
  return url.SchemeIs(url::kHttpsScheme) && url.DomainIs("youtube.com");
}

std::u16string FocusText(std::u16string_view english,
                         std::u16string_view russian) {
  return std::u16string(UseRussianFocusUi() ? russian : english);
}

void ApplyMonochromeToggleColors(views::ToggleButton* toggle) {
  toggle->SetTrackOnColor(ui::kColorSysOnSurface);
  toggle->SetThumbOnColor(ui::kColorSysSurface);
  toggle->SetTrackOffColor(ui::kColorSysSurfaceVariant);
  toggle->SetThumbOffColor(ui::kColorSysOnSurfaceVariant);
}

scoped_refptr<const extensions::Extension> GetFocusYoutubeExtension(
    Browser* browser) {
  if (!browser) {
    return nullptr;
  }
  auto* registry = extensions::ExtensionRegistry::Get(browser->profile());
  const extensions::Extension* extension =
      registry ? registry->enabled_extensions().GetByID(
                     focus::kFocusYoutubeComponentId)
               : nullptr;
  return extension ? base::WrapRefCounted(extension) : nullptr;
}

std::vector<std::string> FeatureKeys() {
  std::vector<std::string> keys;
  keys.reserve(kFeatures.size() + 7);
  keys.emplace_back("global_enable");
  for (const FeatureSpec& feature : kFeatures) {
    for (std::string_view key : StorageKeys(feature)) {
      if (!key.empty()) {
        keys.emplace_back(key);
      }
    }
  }
  return keys;
}

base::DictValue ResetValues() {
  base::DictValue values;
  values.Set("global_enable", true);
  values.Set("schedule", false);
  values.Set("nextTimedChange", false);
  values.Set("nextTimedValue", true);
  values.Set("only_show_playlists", false);
  values.Set("focus_youtube_schema_version", kFocusYoutubeSchemaVersion);
  for (const FeatureSpec& feature : kFeatures) {
    for (std::string_view key : StorageKeys(feature)) {
      if (!key.empty()) {
        values.Set(key, false);
      }
    }
  }
  return values;
}

}  // namespace

// static
void FocusYoutubeBubbleView::ShowBubble(Browser* browser,
                                        views::View* anchor_view) {
  if (!browser || !anchor_view || !anchor_view->GetVisible()) {
    return;
  }

  if (views::DialogDelegate* existing_bubble =
          anchor_view->GetProperty(views::kAnchoredDialogKey)) {
    if (views::Widget* widget = existing_bubble->GetWidget()) {
      widget->Close();
    }
    return;
  }

  content::WebContents* web_contents =
      browser->tab_strip_model()->GetActiveWebContents();
  auto* bubble = new FocusYoutubeBubbleView(browser, anchor_view, web_contents);
  views::BubbleDialogDelegateView::CreateBubble(bubble);
  bubble->ShowForReason(LocationBarBubbleDelegateView::USER_GESTURE);
}

FocusYoutubeBubbleView::FocusYoutubeBubbleView(
    Browser* browser,
    views::View* anchor_view,
    content::WebContents* web_contents)
    : LocationBarBubbleDelegateView(anchor_view, web_contents),
      browser_(browser),
      extension_(GetFocusYoutubeExtension(browser)) {
  SetButtons(static_cast<int>(ui::mojom::DialogButton::kNone));
  SetShowCloseButton(true);
  SetTitle(u"FocusYoutube");
  set_fixed_width(kBubbleWidth);
  set_margins(gfx::Insets::TLBR(12, 16, 14, 16));
}

FocusYoutubeBubbleView::~FocusYoutubeBubbleView() = default;

std::u16string FocusYoutubeBubbleView::GetAccessibleWindowTitle() const {
  return u"FocusYoutube";
}

void FocusYoutubeBubbleView::Init() {
  SetLayoutManager(std::make_unique<views::BoxLayout>(
      views::BoxLayout::Orientation::kVertical, gfx::Insets(),
      kSectionSpacing));

  AddChildView(CreateMasterRow());

  auto tabs = std::make_unique<views::TabbedPane>(
      views::TabbedPane::Orientation::kHorizontal,
      views::TabbedPane::TabStripStyle::kBorder);
  tabs->SetDrawTabDivider(false);

  for (size_t group_index = 0; group_index < kGroups.size(); ++group_index) {
    auto group = std::make_unique<views::View>();
    group->SetLayoutManager(std::make_unique<views::BoxLayout>(
        views::BoxLayout::Orientation::kVertical, gfx::Insets::TLBR(8, 0, 0, 0),
        kRowSpacing));
    for (const FeatureSpec& feature : kFeatures) {
      if (feature.group != group_index) {
        continue;
      }
      group->AddChildView(
          CreateFeatureRow(std::string(feature.key),
                           FocusText(feature.english, feature.russian)));
    }
    tabs->AddTab(
        FocusText(kGroups[group_index].english, kGroups[group_index].russian),
        std::move(group));
  }
  AddChildView(std::move(tabs));

  auto footer = std::make_unique<views::View>();
  auto* footer_layout =
      footer->SetLayoutManager(std::make_unique<views::FlexLayout>());
  footer_layout->SetOrientation(views::LayoutOrientation::kHorizontal)
      .SetCrossAxisAlignment(views::LayoutAlignment::kCenter);

  auto status = std::make_unique<views::Label>();
  status->SetHorizontalAlignment(gfx::ALIGN_LEFT);
  status->SetTextStyle(views::style::STYLE_SECONDARY);
  status->SetEnabledColor(ui::kColorSysOnSurfaceVariant);
  status->SetProperty(
      views::kFlexBehaviorKey,
      views::FlexSpecification(views::LayoutOrientation::kHorizontal,
                               views::MinimumFlexSizeRule::kScaleToZero,
                               views::MaximumFlexSizeRule::kUnbounded));
  status_label_ = footer->AddChildView(std::move(status));

  auto reset = std::make_unique<views::MdTextButton>(
      base::BindRepeating(&FocusYoutubeBubbleView::OnResetPressed,
                          base::Unretained(this)),
      FocusText(u"Reset", u"Сбросить"));
  reset->SetStyle(ui::ButtonStyle::kText);
  reset->SetCustomPadding(gfx::Insets::VH(5, 10));
  reset->GetViewAccessibility().SetName(
      FocusText(u"Reset all FocusYoutube settings",
                u"Сбросить все настройки FocusYoutube"));
  reset_button_ = footer->AddChildView(std::move(reset));
  AddChildView(std::move(footer));

  SetControlsEnabled(false);
  RefreshStatus();
  LoadSettings();
}

void FocusYoutubeBubbleView::DidFinishNavigation(
    content::NavigationHandle* navigation_handle) {
  if (!navigation_handle->IsInPrimaryMainFrame() ||
      !navigation_handle->HasCommitted()) {
    return;
  }

  // The contextual toolbar button is deliberately exposed from the pending
  // YouTube URL. The first commit can therefore arrive just after the click;
  // treating the NTP -> YouTube commit as a generic origin change used to
  // destroy the newly-created bubble before its first accessibility frame.
  // Keep the surface alive for exact supported YouTube hosts, but still close
  // it as soon as the tab commits a navigation outside that context.
  if (!IsFocusYoutubeUrl(navigation_handle->GetURL())) {
    CloseBubble();
  }
}

std::unique_ptr<views::View> FocusYoutubeBubbleView::CreateMasterRow() {
  auto row = std::make_unique<views::View>();
  row->SetBackground(
      views::CreateRoundedRectBackground(ui::kColorSysSurface4, kCardRadius));
  row->SetBorder(views::CreateEmptyBorder(gfx::Insets::VH(10, 12)));
  auto* layout = row->SetLayoutManager(std::make_unique<views::FlexLayout>());
  layout->SetOrientation(views::LayoutOrientation::kHorizontal)
      .SetCrossAxisAlignment(views::LayoutAlignment::kCenter);

  auto text = std::make_unique<views::View>();
  text->SetLayoutManager(std::make_unique<views::BoxLayout>(
      views::BoxLayout::Orientation::kVertical, gfx::Insets(), 2));

  auto title = std::make_unique<views::Label>(
      FocusText(u"FocusYoutube is active", u"FocusYoutube активен"));
  title->SetHorizontalAlignment(gfx::ALIGN_LEFT);
  title->SetTextStyle(views::style::STYLE_PRIMARY);
  master_title_label_ = text->AddChildView(std::move(title));

  auto subtitle = std::make_unique<views::Label>(
      FocusText(u"Enable only the features you need",
                u"Включайте только нужные функции"));
  subtitle->SetHorizontalAlignment(gfx::ALIGN_LEFT);
  subtitle->SetTextStyle(views::style::STYLE_SECONDARY);
  subtitle->SetEnabledColor(ui::kColorSysOnSurfaceVariant);
  subtitle->SetElideBehavior(gfx::ELIDE_TAIL);
  text->AddChildView(std::move(subtitle));

  text->SetProperty(
      views::kFlexBehaviorKey,
      views::FlexSpecification(views::LayoutOrientation::kHorizontal,
                               views::MinimumFlexSizeRule::kScaleToZero,
                               views::MaximumFlexSizeRule::kUnbounded));
  row->AddChildView(std::move(text));

  auto toggle = std::make_unique<views::ToggleButton>(base::BindRepeating(
      &FocusYoutubeBubbleView::OnMasterTogglePressed, base::Unretained(this)));
  toggle->SetProperty(views::kMarginsKey, gfx::Insets::TLBR(0, 12, 0, 0));
  toggle->GetViewAccessibility().SetName(
      FocusText(u"Enable FocusYoutube", u"Включить FocusYoutube"));
  ApplyMonochromeToggleColors(toggle.get());
  master_toggle_ = row->AddChildView(std::move(toggle));
  return row;
}

std::unique_ptr<views::View> FocusYoutubeBubbleView::CreateFeatureRow(
    std::string key,
    std::u16string label) {
  auto row = std::make_unique<views::View>();
  row->SetBackground(
      views::CreateRoundedRectBackground(ui::kColorSysSurface4, kCardRadius));
  row->SetBorder(views::CreateEmptyBorder(gfx::Insets::VH(7, 12)));
  auto* layout = row->SetLayoutManager(std::make_unique<views::FlexLayout>());
  layout->SetOrientation(views::LayoutOrientation::kHorizontal)
      .SetCrossAxisAlignment(views::LayoutAlignment::kCenter);

  auto title = std::make_unique<views::Label>(label);
  title->SetHorizontalAlignment(gfx::ALIGN_LEFT);
  title->SetTextStyle(views::style::STYLE_PRIMARY);
  title->SetElideBehavior(gfx::ELIDE_TAIL);
  title->SetProperty(
      views::kFlexBehaviorKey,
      views::FlexSpecification(views::LayoutOrientation::kHorizontal,
                               views::MinimumFlexSizeRule::kScaleToZero,
                               views::MaximumFlexSizeRule::kUnbounded));
  row->AddChildView(std::move(title));

  auto toggle = std::make_unique<views::ToggleButton>(
      base::BindRepeating(&FocusYoutubeBubbleView::OnFeatureTogglePressed,
                          base::Unretained(this), key));
  toggle->SetProperty(views::kMarginsKey, gfx::Insets::TLBR(0, 12, 0, 0));
  toggle->GetViewAccessibility().SetName(label);
  ApplyMonochromeToggleColors(toggle.get());
  feature_state_.emplace(key, false);
  feature_toggles_.emplace(key, row->AddChildView(std::move(toggle)));
  return row;
}

void FocusYoutubeBubbleView::LoadSettings() {
  if (!loading_) {
    return;
  }

  ++settings_load_attempts_;
  // The toolbar is URL-owned and can be clicked before the component
  // extension finishes registering. Reacquire it on every attempt instead of
  // permanently caching the constructor-time miss.
  extension_ = GetFocusYoutubeExtension(browser_);
  auto* storage = browser_
                      ? extensions::StorageFrontend::Get(browser_->profile())
                      : nullptr;
  if (!storage || !extension_) {
    ScheduleSettingsLoadRetry();
    return;
  }

  storage->GetValues(
      extension_, extensions::StorageAreaNamespace::kLocal, FeatureKeys(),
      base::BindOnce(
          [](base::WeakPtr<FocusYoutubeBubbleView> bubble,
             extensions::StorageFrontend::GetResult result) {
            if (!bubble) {
              return;
            }
            const bool success =
                result.status.success && result.data.has_value();
            bubble->OnSettingsLoaded(
                success, success ? std::move(*result.data) : base::DictValue());
          },
          weak_factory_.GetWeakPtr()));
}

void FocusYoutubeBubbleView::ScheduleSettingsLoadRetry() {
  if (!loading_) {
    return;
  }
  if (settings_load_attempts_ >= kSettingsLoadMaxAttempts) {
    loading_ = false;
    storage_error_ = true;
    SetControlsEnabled(false);
    RefreshStatus();
    return;
  }

  // Keep the native surface in its explicit loading state while the
  // component and its storage namespace become available. A weak pointer
  // makes closing the bubble cancel the remaining bounded retries.
  base::SequencedTaskRunner::GetCurrentDefault()->PostDelayedTask(
      FROM_HERE,
      base::BindOnce(&FocusYoutubeBubbleView::LoadSettings,
                     weak_factory_.GetWeakPtr()),
      kSettingsLoadRetryDelay);
}

void FocusYoutubeBubbleView::OnSettingsLoaded(bool success,
                                              base::DictValue values) {
  if (!success) {
    ScheduleSettingsLoadRetry();
    return;
  }

  loading_ = false;
  storage_error_ = false;
  global_enabled_ = values.FindBool("global_enable").value_or(true);
  master_toggle_->SetIsOn(global_enabled_);
  for (auto& [key, toggle] : feature_toggles_) {
    const FeatureSpec* feature = FindFeature(key);
    const bool enabled = feature && IsFeatureEnabled(values, *feature);
    feature_state_[key] = enabled;
    toggle->SetIsOn(enabled);
  }
  SetControlsEnabled(true);
  RefreshStatus();
}

void FocusYoutubeBubbleView::OnMasterTogglePressed(const ui::Event&) {
  if (loading_ || !master_toggle_) {
    return;
  }
  const bool previous_value = global_enabled_;
  const bool requested_value = master_toggle_->GetIsOn();
  storage_error_ = false;
  SetControlsEnabled(false);

  base::DictValue values;
  values.Set("global_enable", requested_value);
  // A direct browser-owned choice must win over automation left by an older
  // FocusYoutube UI. Keep the change atomic so the service worker never sees
  // the new master value alongside an active legacy schedule or timer.
  values.Set("schedule", false);
  values.Set("nextTimedChange", false);
  values.Set("nextTimedValue", true);
  auto* storage = extensions::StorageFrontend::Get(browser_->profile());
  storage->Set(extension_, extensions::StorageAreaNamespace::kLocal,
               std::move(values),
               base::BindOnce(
                   [](base::WeakPtr<FocusYoutubeBubbleView> bubble,
                      std::string key, bool previous, bool requested,
                      extensions::StorageFrontend::ResultStatus result) {
                     if (bubble) {
                       bubble->OnSettingWritten(std::move(key), previous,
                                                requested, result.success);
                     }
                   },
                   weak_factory_.GetWeakPtr(), std::string("global_enable"),
                   previous_value, requested_value));
}

void FocusYoutubeBubbleView::OnFeatureTogglePressed(std::string key,
                                                    const ui::Event&) {
  if (loading_) {
    return;
  }
  auto toggle_it = feature_toggles_.find(key);
  auto state_it = feature_state_.find(key);
  if (toggle_it == feature_toggles_.end() || state_it == feature_state_.end()) {
    return;
  }

  const bool previous_value = state_it->second;
  const bool requested_value = toggle_it->second->GetIsOn();
  storage_error_ = false;
  SetControlsEnabled(false);

  const FeatureSpec* feature = FindFeature(key);
  if (!feature) {
    toggle_it->second->SetIsOn(previous_value);
    return;
  }

  base::DictValue values;
  for (std::string_view storage_key : StorageKeys(*feature)) {
    if (!storage_key.empty()) {
      values.Set(storage_key, requested_value);
    }
  }
  auto* storage = extensions::StorageFrontend::Get(browser_->profile());
  storage->Set(
      extension_, extensions::StorageAreaNamespace::kLocal, std::move(values),
      base::BindOnce(
          [](base::WeakPtr<FocusYoutubeBubbleView> bubble,
             std::string written_key, bool previous, bool requested,
             extensions::StorageFrontend::ResultStatus result) {
            if (bubble) {
              bubble->OnSettingWritten(std::move(written_key), previous,
                                       requested, result.success);
            }
          },
          weak_factory_.GetWeakPtr(), key, previous_value, requested_value));
}

void FocusYoutubeBubbleView::OnSettingWritten(std::string key,
                                              bool previous_value,
                                              bool requested_value,
                                              bool success) {
  if (key == "global_enable") {
    global_enabled_ = success ? requested_value : previous_value;
    master_toggle_->SetIsOn(global_enabled_);
  } else if (auto toggle_it = feature_toggles_.find(key);
             toggle_it != feature_toggles_.end()) {
    feature_state_[key] = success ? requested_value : previous_value;
    toggle_it->second->SetIsOn(feature_state_[key]);
  }
  storage_error_ = !success;
  SetControlsEnabled(true);
  RefreshStatus();
}

void FocusYoutubeBubbleView::OnResetPressed(const ui::Event&) {
  if (loading_) {
    return;
  }
  storage_error_ = false;
  SetControlsEnabled(false);

  auto* storage = extensions::StorageFrontend::Get(browser_->profile());
  // Clear the whole component namespace first. This removes legacy options
  // that are intentionally absent from the compact native surface, instead of
  // leaving hidden rules active with no way for the user to turn them off.
  storage->Clear(extension_, extensions::StorageAreaNamespace::kLocal,
                 base::BindOnce(
                     [](base::WeakPtr<FocusYoutubeBubbleView> bubble,
                        extensions::StorageFrontend::ResultStatus result) {
                       if (bubble) {
                         bubble->OnResetStorageCleared(result.success);
                       }
                     },
                     weak_factory_.GetWeakPtr()));
}

void FocusYoutubeBubbleView::OnResetStorageCleared(bool success) {
  if (!success) {
    OnResetWritten(false);
    return;
  }

  auto* storage = extensions::StorageFrontend::Get(browser_->profile());
  storage->Set(extension_, extensions::StorageAreaNamespace::kLocal,
               ResetValues(),
               base::BindOnce(
                   [](base::WeakPtr<FocusYoutubeBubbleView> bubble,
                      extensions::StorageFrontend::ResultStatus result) {
                     if (bubble) {
                       bubble->OnResetWritten(result.success);
                     }
                   },
                   weak_factory_.GetWeakPtr()));
}

void FocusYoutubeBubbleView::OnResetWritten(bool success) {
  if (success) {
    global_enabled_ = true;
    master_toggle_->SetIsOn(true);
    for (auto& [key, value] : feature_state_) {
      value = false;
      feature_toggles_[key]->SetIsOn(false);
    }
  }
  storage_error_ = !success;
  SetControlsEnabled(true);
  RefreshStatus();
}

void FocusYoutubeBubbleView::SetControlsEnabled(bool enabled) {
  if (master_toggle_) {
    master_toggle_->SetEnabled(enabled);
  }
  for (const auto& feature : feature_toggles_) {
    feature.second->SetEnabled(enabled);
  }
  if (reset_button_) {
    reset_button_->SetEnabled(enabled);
  }
}

void FocusYoutubeBubbleView::RefreshStatus() {
  if (!status_label_) {
    return;
  }
  if (loading_) {
    status_label_->SetText(
        FocusText(u"Loading settings…", u"Загрузка настроек…"));
    return;
  }
  if (storage_error_) {
    status_label_->SetText(
        FocusText(u"Settings are unavailable", u"Настройки недоступны"));
    return;
  }

  size_t enabled_count = 0;
  for (const auto& feature : feature_state_) {
    enabled_count += feature.second ? 1 : 0;
  }
  const std::u16string count = base::NumberToString16(enabled_count) + u" / " +
                               base::NumberToString16(kFeatures.size());
  status_label_->SetText(
      global_enabled_
          ? FocusText(u"Enabled: ", u"Включено: ") + count
          : FocusText(u"Paused · selected: ", u"Пауза · выбрано: ") + count);
  master_title_label_->SetText(
      global_enabled_
          ? FocusText(u"FocusYoutube is active", u"FocusYoutube активен")
          : FocusText(u"FocusYoutube is paused", u"FocusYoutube на паузе"));
  // Keep recovery available even when the twenty visible switches are off:
  // Reset also removes settings left by older, larger FocusYoutube surfaces.
  reset_button_->SetEnabled(true);
}

BEGIN_METADATA(FocusYoutubeBubbleView)
END_METADATA
