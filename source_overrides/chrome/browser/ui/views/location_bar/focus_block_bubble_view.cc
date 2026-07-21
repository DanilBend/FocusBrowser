// Copyright 2026 The Focus Browser Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#include "chrome/browser/ui/views/location_bar/focus_block_bubble_view.h"

#include <cstdint>
#include <memory>
#include <string_view>
#include <utility>

#include "base/functional/bind.h"
#include "base/i18n/number_formatting.h"
#include "base/strings/utf_string_conversions.h"
#include "base/time/time.h"
#include "chrome/browser/focus_block/focus_block_service.h"
#include "chrome/browser/focus_block/focus_block_service_factory.h"
#include "chrome/browser/ui/browser.h"
#include "chrome/browser/ui/tabs/tab_strip_model.h"
#include "content/public/browser/web_contents.h"
#include "ui/base/l10n/l10n_util.h"
#include "ui/base/metadata/metadata_impl_macros.h"
#include "ui/base/mojom/dialog_button.mojom.h"
#include "ui/color/color_id.h"
#include "ui/gfx/font.h"
#include "ui/gfx/geometry/insets.h"
#include "ui/views/accessibility/view_accessibility.h"
#include "ui/views/background.h"
#include "ui/views/border.h"
#include "ui/views/controls/button/toggle_button.h"
#include "ui/views/controls/label.h"
#include "ui/views/layout/box_layout.h"
#include "ui/views/layout/flex_layout.h"
#include "ui/views/view.h"
#include "ui/views/view_class_properties.h"
#include "ui/views/widget/widget.h"

namespace {

constexpr int kBubbleWidth = 360;
constexpr int kCardRadius = 12;
constexpr int kSectionSpacing = 10;

bool UseRussianFocusUi() {
  const std::string locale =
      l10n_util::GetApplicationLocale(std::string(), false);
  return locale == "ru" || locale.starts_with("ru-") ||
         locale.starts_with("ru_");
}

std::u16string FocusText(std::u16string_view english,
                         std::u16string_view russian) {
  return std::u16string(UseRussianFocusUi() ? russian : english);
}

base::WeakPtr<focus_block::FocusBlockService> GetFocusBlockService(
    Browser* browser) {
  auto* service = browser
                      ? focus_block::FocusBlockServiceFactory::GetForProfile(
                            browser->profile())
                      : nullptr;
  return service ? service->GetWeakPtr()
                 : base::WeakPtr<focus_block::FocusBlockService>();
}

void ApplyMonochromeToggleColors(views::ToggleButton* toggle) {
  toggle->SetTrackOnColor(ui::kColorSysOnSurface);
  toggle->SetThumbOnColor(ui::kColorSysSurface);
  toggle->SetTrackOffColor(ui::kColorSysSurfaceVariant);
  toggle->SetThumbOffColor(ui::kColorSysOnSurfaceVariant);
}

std::unique_ptr<views::View> CreateToggleRow(
    std::u16string title,
    std::u16string subtitle,
    views::Button::PressedCallback callback,
    raw_ptr<views::ToggleButton>* toggle_out) {
  auto row = std::make_unique<views::View>();
  row->SetBackground(
      views::CreateRoundedRectBackground(ui::kColorSysSurface4, kCardRadius));
  row->SetBorder(views::CreateEmptyBorder(gfx::Insets::VH(10, 12)));

  auto* row_layout =
      row->SetLayoutManager(std::make_unique<views::FlexLayout>());
  row_layout->SetOrientation(views::LayoutOrientation::kHorizontal)
      .SetCrossAxisAlignment(views::LayoutAlignment::kCenter);

  auto text = std::make_unique<views::View>();
  text->SetLayoutManager(std::make_unique<views::BoxLayout>(
      views::BoxLayout::Orientation::kVertical, gfx::Insets(), 2));

  auto title_label = std::make_unique<views::Label>(std::move(title));
  title_label->SetHorizontalAlignment(gfx::ALIGN_LEFT);
  title_label->SetTextStyle(views::style::STYLE_PRIMARY);
  text->AddChildView(std::move(title_label));

  auto subtitle_label = std::make_unique<views::Label>(std::move(subtitle));
  subtitle_label->SetHorizontalAlignment(gfx::ALIGN_LEFT);
  subtitle_label->SetTextStyle(views::style::STYLE_SECONDARY);
  subtitle_label->SetEnabledColor(ui::kColorSysOnSurfaceVariant);
  text->AddChildView(std::move(subtitle_label));

  text->SetProperty(
      views::kFlexBehaviorKey,
      views::FlexSpecification(views::LayoutOrientation::kHorizontal,
                               views::MinimumFlexSizeRule::kScaleToZero,
                               views::MaximumFlexSizeRule::kUnbounded));
  row->AddChildView(std::move(text));

  auto toggle = std::make_unique<views::ToggleButton>(std::move(callback));
  toggle->SetProperty(views::kMarginsKey, gfx::Insets::TLBR(0, 12, 0, 0));
  ApplyMonochromeToggleColors(toggle.get());
  *toggle_out = row->AddChildView(std::move(toggle));
  return row;
}

std::unique_ptr<views::View> CreateCounterCard(
    std::u16string title,
    raw_ptr<views::Label>* value_out) {
  auto card = std::make_unique<views::View>();
  card->SetBackground(
      views::CreateRoundedRectBackground(ui::kColorSysSurface4, kCardRadius));
  card->SetBorder(views::CreateEmptyBorder(gfx::Insets::VH(10, 12)));
  card->SetLayoutManager(std::make_unique<views::BoxLayout>(
      views::BoxLayout::Orientation::kVertical, gfx::Insets(), 2));

  auto value = std::make_unique<views::Label>(u"0");
  value->SetHorizontalAlignment(gfx::ALIGN_LEFT);
  value->SetFontList(value->font_list().DeriveWithSizeDelta(4).DeriveWithWeight(
      gfx::Font::Weight::MEDIUM));
  *value_out = card->AddChildView(std::move(value));

  auto title_label = std::make_unique<views::Label>(std::move(title));
  title_label->SetHorizontalAlignment(gfx::ALIGN_LEFT);
  title_label->SetTextStyle(views::style::STYLE_SECONDARY);
  title_label->SetEnabledColor(ui::kColorSysOnSurfaceVariant);
  card->AddChildView(std::move(title_label));

  card->SetProperty(
      views::kFlexBehaviorKey,
      views::FlexSpecification(views::LayoutOrientation::kHorizontal,
                               views::MinimumFlexSizeRule::kScaleToZero,
                               views::MaximumFlexSizeRule::kUnbounded)
          .WithWeight(1));
  return card;
}

}  // namespace

// static
void FocusBlockBubbleView::ShowBubble(Browser* browser,
                                      views::View* anchor_view) {
  if (!browser || !anchor_view || !anchor_view->GetVisible()) {
    return;
  }

  // BubbleDialogDelegate stores the active dialog on its anchor. Using that
  // per-view state keeps independent browser windows independent while still
  // making a second click on the same shield close its bubble.
  if (views::DialogDelegate* existing_bubble =
          anchor_view->GetProperty(views::kAnchoredDialogKey)) {
    if (views::Widget* widget = existing_bubble->GetWidget()) {
      widget->Close();
    }
    return;
  }

  content::WebContents* web_contents =
      browser->tab_strip_model()->GetActiveWebContents();
  auto* bubble = new FocusBlockBubbleView(browser, anchor_view, web_contents);
  views::BubbleDialogDelegateView::CreateBubble(bubble);
  bubble->ShowForReason(LocationBarBubbleDelegateView::USER_GESTURE);
}

FocusBlockBubbleView::FocusBlockBubbleView(Browser* browser,
                                           views::View* anchor_view,
                                           content::WebContents* web_contents)
    : LocationBarBubbleDelegateView(anchor_view, web_contents),
      service_(GetFocusBlockService(browser)),
      page_url_(web_contents ? web_contents->GetLastCommittedURL() : GURL()) {
  SetButtons(static_cast<int>(ui::mojom::DialogButton::kNone));
  SetShowCloseButton(true);
  SetTitle(u"FocusBlock");
  set_fixed_width(kBubbleWidth);
  set_margins(gfx::Insets::TLBR(12, 16, 16, 16));
  SetCloseOnMainFrameOriginNavigation(true);
}

FocusBlockBubbleView::~FocusBlockBubbleView() = default;

std::u16string FocusBlockBubbleView::GetAccessibleWindowTitle() const {
  return u"FocusBlock";
}

void FocusBlockBubbleView::Init() {
  SetLayoutManager(std::make_unique<views::BoxLayout>(
      views::BoxLayout::Orientation::kVertical, gfx::Insets(),
      kSectionSpacing));

  auto engine_status = std::make_unique<views::Label>();
  engine_status->SetHorizontalAlignment(gfx::ALIGN_LEFT);
  engine_status->SetTextStyle(views::style::STYLE_SECONDARY);
  engine_status->SetEnabledColor(ui::kColorSysOnSurfaceVariant);
  engine_status_label_ = AddChildView(std::move(engine_status));

  auto global_row = CreateToggleRow(
      FocusText(u"Protection across the browser", u"Защита во всём браузере"),
      FocusText(u"Block ads and trackers", u"Блокировать рекламу и трекеры"),
      base::BindRepeating(&FocusBlockBubbleView::OnGlobalTogglePressed,
                          base::Unretained(this)),
      &global_toggle_);
  global_toggle_->GetViewAccessibility().SetName(
      FocusText(u"FocusBlock protection across the browser",
                u"Защита FocusBlock во всём браузере"));
  AddChildView(std::move(global_row));

  auto site_row = CreateToggleRow(
      FocusText(u"Protection on this site", u"Защита на этом сайте"),
      GetSiteDescription(),
      base::BindRepeating(&FocusBlockBubbleView::OnSiteTogglePressed,
                          base::Unretained(this)),
      &site_toggle_);
  site_toggle_->GetViewAccessibility().SetName(
      FocusText(u"FocusBlock protection on the current site",
                u"Защита FocusBlock на текущем сайте"));
  AddChildView(std::move(site_row));

  auto counters = std::make_unique<views::View>();
  auto* counters_layout =
      counters->SetLayoutManager(std::make_unique<views::FlexLayout>());
  counters_layout->SetOrientation(views::LayoutOrientation::kHorizontal)
      .SetCrossAxisAlignment(views::LayoutAlignment::kStretch);
  auto page_counter = CreateCounterCard(
      FocusText(u"On this site", u"На этом сайте"), &page_blocked_value_);
  page_counter->SetProperty(views::kMarginsKey,
                            gfx::Insets::TLBR(0, 0, 0, 4));
  counters->AddChildView(std::move(page_counter));

  auto session_counter = CreateCounterCard(
      FocusText(u"This session", u"За сессию"), &session_blocked_value_);
  session_counter->SetProperty(views::kMarginsKey,
                               gfx::Insets::TLBR(0, 4, 0, 0));
  counters->AddChildView(std::move(session_counter));
  AddChildView(std::move(counters));

  auto footer = std::make_unique<views::Label>(
      u"EasyList + EasyPrivacy  •  adblock-rust 0.13.2");
  footer->SetHorizontalAlignment(gfx::ALIGN_LEFT);
  footer->SetTextStyle(views::style::STYLE_SECONDARY);
  footer->SetEnabledColor(ui::kColorSysOnSurfaceVariant);
  AddChildView(std::move(footer));

  RefreshFromService();
  if (service_) {
    state_refresh_timer_.Start(
        FROM_HERE, base::Seconds(1),
        base::BindRepeating(&FocusBlockBubbleView::RefreshFromService,
                            base::Unretained(this)));
  }
}

void FocusBlockBubbleView::WindowClosing() {
  state_refresh_timer_.Stop();
}

void FocusBlockBubbleView::OnGlobalTogglePressed(const ui::Event&) {
  if (service_) {
    service_->SetEnabled(global_toggle_->GetIsOn());
  }
  RefreshFromService();
}

void FocusBlockBubbleView::OnSiteTogglePressed(const ui::Event&) {
  if (service_ && IsSiteControlAvailable()) {
    service_->SetEnabledForUrl(page_url_, site_toggle_->GetIsOn());
  }
  RefreshFromService();
}

void FocusBlockBubbleView::RefreshFromService() {
  const bool service_available = service_ != nullptr;
  if (!service_available && state_refresh_timer_.IsRunning()) {
    state_refresh_timer_.Stop();
  }
  const bool globally_enabled = service_available && service_->enabled();
  const bool engine_ready = service_available && service_->engine_ready();

  if (!service_available) {
    engine_status_label_->SetText(
        FocusText(u"● Engine unavailable", u"● Движок недоступен"));
  } else if (!globally_enabled) {
    engine_status_label_->SetText(
        FocusText(u"● Protection is off", u"● Защита выключена"));
  } else if (engine_ready) {
    engine_status_label_->SetText(
        FocusText(u"● Engine active", u"● Движок активен"));
  } else {
    engine_status_label_->SetText(
        FocusText(u"● Engine starting…", u"● Движок запускается…"));
  }

  global_toggle_->SetEnabled(service_available);
  global_toggle_->SetIsOn(globally_enabled);

  const bool site_control_available =
      service_available && globally_enabled && IsSiteControlAvailable();
  site_toggle_->SetEnabled(site_control_available);
  site_toggle_->SetIsOn(site_control_available &&
                        service_->IsEnabledForUrl(page_url_));

  RefreshCounters();
}

void FocusBlockBubbleView::RefreshCounters() {
  if (!page_blocked_value_ || !session_blocked_value_) {
    return;
  }

  const uint64_t page_count = service_ && IsSiteControlAvailable()
                                  ? service_->GetBlockedCountForUrl(page_url_)
                                  : 0;
  const uint64_t session_count =
      service_ ? service_->blocked_count_session() : 0;
  page_blocked_value_->SetText(
      base::FormatNumber(static_cast<int64_t>(page_count)));
  session_blocked_value_->SetText(
      base::FormatNumber(static_cast<int64_t>(session_count)));
}

bool FocusBlockBubbleView::IsSiteControlAvailable() const {
  return page_url_.SchemeIsHTTPOrHTTPS() && page_url_.has_host();
}

std::u16string FocusBlockBubbleView::GetSiteDescription() const {
  return IsSiteControlAvailable() ? base::UTF8ToUTF16(page_url_.host())
                                  : FocusText(
                                        u"Unavailable on a system page",
                                        u"Недоступно для системной страницы");
}

BEGIN_METADATA(FocusBlockBubbleView)
END_METADATA
