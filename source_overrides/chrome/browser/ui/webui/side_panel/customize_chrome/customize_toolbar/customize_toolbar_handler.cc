// Copyright 2024 The Chromium Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#include "chrome/browser/ui/webui/side_panel/customize_chrome/customize_toolbar/customize_toolbar_handler.h"

#include "base/feature_list.h"
#include "base/metrics/user_metrics.h"
#include "base/strings/strcat.h"
#include "chrome/app/vector_icons/vector_icons.h"
#include "chrome/browser/profiles/profile.h"
#include "chrome/browser/ui/actions/chrome_action_id.h"
#include "chrome/browser/ui/browser_actions.h"
#include "chrome/browser/ui/browser_window/public/browser_window_interface.h"
#include "chrome/browser/ui/tab_search_feature.h"
#include "chrome/browser/ui/tabs/features.h"
#include "chrome/browser/ui/toolbar/pinned_toolbar/pinned_toolbar_actions_model.h"
#include "chrome/browser/ui/ui_features.h"
#include "chrome/browser/ui/views/frame/browser_view.h"
#include "chrome/browser/ui/webui/side_panel/customize_chrome/customize_toolbar/customize_toolbar.mojom.h"
#include "chrome/browser/ui/webui/util/image_util.h"
#include "chrome/browser/ui/webui/webui_embedding_context.h"
#include "chrome/common/chrome_features.h"
#include "chrome/common/pref_names.h"
#include "chrome/grit/branded_strings.h"
#include "chrome/grit/generated_resources.h"
#include "components/contextual_tasks/public/features.h"
#include "components/focus_services/pref_names.h"
#include "components/prefs/pref_service.h"
#include "components/strings/grit/components_strings.h"
#include "components/vector_icons/vector_icons.h"
#include "mojo/public/cpp/bindings/pending_receiver.h"
#include "mojo/public/cpp/bindings/receiver.h"
#include "mojo/public/cpp/bindings/remote.h"
#include "ui/actions/actions.h"
#include "ui/base/l10n/l10n_util.h"
#include "ui/base/models/image_model.h"
#include "ui/base/ui_base_features.h"
#include "ui/display/screen.h"
#include "ui/gfx/vector_icon_types.h"
#include "ui/views/vector_icons.h"

namespace {

bool UseRussianFocusUi() {
  const std::string locale =
      l10n_util::GetApplicationLocale(std::string(), false);
  return locale == "ru" || locale.starts_with("ru-") ||
         locale.starts_with("ru_");
}

std::optional<side_panel::customize_chrome::mojom::ActionId>
MojoActionForChromeAction(actions::ActionId action_id) {
  switch (action_id) {
    case kActionSidePanelShowBookmarks:
      return side_panel::customize_chrome::mojom::ActionId::kShowBookmarks;
    case kActionSidePanelShowHistoryCluster:
      return side_panel::customize_chrome::mojom::ActionId::kShowHistoryCluster;
    case kActionHome:
      return side_panel::customize_chrome::mojom::ActionId::kHome;
    case kActionForward:
      return side_panel::customize_chrome::mojom::ActionId::kForward;
    case kActionBack:
      return side_panel::customize_chrome::mojom::ActionId::kBack;
    case kActionReload:
      return side_panel::customize_chrome::mojom::ActionId::kReload;
    case kActionToggleCollapseVertical:
      return side_panel::customize_chrome::mojom::ActionId::
          kVerticalTabsCollapse;
    case kActionNewTab:
      return side_panel::customize_chrome::mojom::ActionId::kDynamicNewTab;
    case kActionAvatar:
      return side_panel::customize_chrome::mojom::ActionId::kAvatar;
    case kActionExtensions:
      return side_panel::customize_chrome::mojom::ActionId::kExtensions;
    case kActionShowAppMenu:
      return side_panel::customize_chrome::mojom::ActionId::kMenu;
    case kActionMediaControls:
      return side_panel::customize_chrome::mojom::ActionId::kMediaControls;
    case kActionNewIncognitoWindow:
      return side_panel::customize_chrome::mojom::ActionId::kNewIncognitoWindow;
    case kActionShowDownloads:
      return side_panel::customize_chrome::mojom::ActionId::kShowDownloads;
    case kActionClearBrowsingData:
      return side_panel::customize_chrome::mojom::ActionId::kClearBrowsingData;
    case kActionPrint:
      return side_panel::customize_chrome::mojom::ActionId::kPrint;
    case kActionShowTranslate:
      return side_panel::customize_chrome::mojom::ActionId::kShowTranslate;
    case kActionSendTabToSelf:
      return side_panel::customize_chrome::mojom::ActionId::kSendTabToSelf;
    case kActionQrCodeGenerator:
      return side_panel::customize_chrome::mojom::ActionId::kQrCodeGenerator;
    case kActionRouteMedia:
      return side_panel::customize_chrome::mojom::ActionId::kRouteMedia;
    case kActionTaskManager:
      return side_panel::customize_chrome::mojom::ActionId::kTaskManager;
    case kActionDevTools:
      return side_panel::customize_chrome::mojom::ActionId::kDevTools;
    case kActionShowChromeLabs:
      return side_panel::customize_chrome::mojom::ActionId::kShowChromeLabs;
    case kActionCopyUrl:
      return side_panel::customize_chrome::mojom::ActionId::kCopyLink;
    case kActionTabSearch:
      return side_panel::customize_chrome::mojom::ActionId::kTabSearch;
    case kActionSplitTab:
      return side_panel::customize_chrome::mojom::ActionId::kSplitTab;
    case kActionSidePanelShowContextualTasks:
      return side_panel::customize_chrome::mojom::ActionId::kContextualTasks;
    case kActionSidePanelShowTabsFromOtherDevices:
      return side_panel::customize_chrome::mojom::ActionId::
          kShowTabsFromOtherDevices;
    default:
      return std::nullopt;
  }
}

std::optional<actions::ActionId> ChromeActionForMojoAction(
    side_panel::customize_chrome::mojom::ActionId action_id) {
  switch (action_id) {
    case side_panel::customize_chrome::mojom::ActionId::kShowBookmarks:
      return kActionSidePanelShowBookmarks;
    case side_panel::customize_chrome::mojom::ActionId::kShowHistoryCluster:
      return kActionSidePanelShowHistoryCluster;
    case side_panel::customize_chrome::mojom::ActionId::kHome:
      return kActionHome;
    case side_panel::customize_chrome::mojom::ActionId::kForward:
      return kActionForward;
    case side_panel::customize_chrome::mojom::ActionId::kBack:
      return kActionBack;
    case side_panel::customize_chrome::mojom::ActionId::kReload:
      return kActionReload;
    case side_panel::customize_chrome::mojom::ActionId::kVerticalTabsCollapse:
      return kActionToggleCollapseVertical;
    case side_panel::customize_chrome::mojom::ActionId::kDynamicNewTab:
      return kActionNewTab;
    case side_panel::customize_chrome::mojom::ActionId::kAvatar:
      return kActionAvatar;
    case side_panel::customize_chrome::mojom::ActionId::kExtensions:
      return kActionExtensions;
    case side_panel::customize_chrome::mojom::ActionId::kMenu:
      return kActionShowAppMenu;
    case side_panel::customize_chrome::mojom::ActionId::kMediaControls:
      return kActionMediaControls;
    case side_panel::customize_chrome::mojom::ActionId::kNewIncognitoWindow:
      return kActionNewIncognitoWindow;
    case side_panel::customize_chrome::mojom::ActionId::kShowDownloads:
      return kActionShowDownloads;
    case side_panel::customize_chrome::mojom::ActionId::kClearBrowsingData:
      return kActionClearBrowsingData;
    case side_panel::customize_chrome::mojom::ActionId::kPrint:
      return kActionPrint;
    case side_panel::customize_chrome::mojom::ActionId::kShowTranslate:
      return kActionShowTranslate;
    case side_panel::customize_chrome::mojom::ActionId::kSendTabToSelf:
      return kActionSendTabToSelf;
    case side_panel::customize_chrome::mojom::ActionId::kQrCodeGenerator:
      return kActionQrCodeGenerator;
    case side_panel::customize_chrome::mojom::ActionId::kRouteMedia:
      return kActionRouteMedia;
    case side_panel::customize_chrome::mojom::ActionId::kTaskManager:
      return kActionTaskManager;
    case side_panel::customize_chrome::mojom::ActionId::kDevTools:
      return kActionDevTools;
    case side_panel::customize_chrome::mojom::ActionId::kShowChromeLabs:
      return kActionShowChromeLabs;
    case side_panel::customize_chrome::mojom::ActionId::kCopyLink:
      return kActionCopyUrl;
    case side_panel::customize_chrome::mojom::ActionId::kTabSearch:
      return kActionTabSearch;
    case side_panel::customize_chrome::mojom::ActionId::kSplitTab:
      return kActionSplitTab;
    case side_panel::customize_chrome::mojom::ActionId::kContextualTasks:
      return kActionSidePanelShowContextualTasks;
    default:
      return std::nullopt;
  }
}
}  // namespace

CustomizeToolbarHandler::CustomizeToolbarHandler(
    mojo::PendingReceiver<
        side_panel::customize_chrome::mojom::CustomizeToolbarHandler> handler,
    mojo::PendingRemote<
        side_panel::customize_chrome::mojom::CustomizeToolbarClient> client,
    content::WebContents* web_contents)
    : client_(std::move(client)),
      receiver_(this, std::move(handler)),
      web_contents_(web_contents),
      model_(PinnedToolbarActionsModel::Get(
          Profile::FromBrowserContext(web_contents_->GetBrowserContext()))) {
  model_observation_.Observe(model_);
  pref_change_registrar_.Init(prefs());

  pref_change_registrar_.Add(
      prefs::kShowHomeButton,
      base::BindRepeating(&CustomizeToolbarHandler::OnActionPinnedChanged,
                          base::Unretained(this), kActionHome,
                          prefs::kShowHomeButton));
  pref_change_registrar_.Add(
      prefs::kShowForwardButton,
      base::BindRepeating(&CustomizeToolbarHandler::OnActionPinnedChanged,
                          base::Unretained(this), kActionForward,
                          prefs::kShowForwardButton));
  pref_change_registrar_.Add(
      prefs::kPinSplitTabButton,
      base::BindRepeating(&CustomizeToolbarHandler::OnActionPinnedChanged,
                          base::Unretained(this), kActionSplitTab,
                          prefs::kPinSplitTabButton));
  pref_change_registrar_.Add(
      prefs::kShowBackButton,
      base::BindRepeating(&CustomizeToolbarHandler::OnActionPinnedChanged,
                          base::Unretained(this), kActionBack,
                          prefs::kShowBackButton));
  pref_change_registrar_.Add(
      prefs::kShowReloadButton,
      base::BindRepeating(&CustomizeToolbarHandler::OnActionPinnedChanged,
                          base::Unretained(this), kActionReload,
                          prefs::kShowReloadButton));
  pref_change_registrar_.Add(
      prefs::kShowVerticalTabsCollapseButton,
      base::BindRepeating(&CustomizeToolbarHandler::OnActionPinnedChanged,
                          base::Unretained(this), kActionToggleCollapseVertical,
                          prefs::kShowVerticalTabsCollapseButton));
  pref_change_registrar_.Add(
      prefs::kShowDynamicNewTabButton,
      base::BindRepeating(&CustomizeToolbarHandler::OnActionPinnedChanged,
                          base::Unretained(this), kActionNewTab,
                          prefs::kShowDynamicNewTabButton));
  pref_change_registrar_.Add(
      prefs::kShowAvatarButton,
      base::BindRepeating(&CustomizeToolbarHandler::OnActionPinnedChanged,
                          base::Unretained(this), kActionAvatar,
                          prefs::kShowAvatarButton));
  pref_change_registrar_.Add(
      prefs::kShowExtensionsButton,
      base::BindRepeating(&CustomizeToolbarHandler::OnActionPinnedChanged,
                          base::Unretained(this), kActionExtensions,
                          prefs::kShowExtensionsButton));
  pref_change_registrar_.Add(
      prefs::kShowFocusBlockButton,
      base::BindRepeating(
          &CustomizeToolbarHandler::OnBrowserOwnedActionPinnedChanged,
          base::Unretained(this),
          side_panel::customize_chrome::mojom::ActionId::kFocusBlock,
          prefs::kShowFocusBlockButton));
  pref_change_registrar_.Add(
      prefs::kShowFocusYoutubeButton,
      base::BindRepeating(
          &CustomizeToolbarHandler::OnBrowserOwnedActionPinnedChanged,
          base::Unretained(this),
          side_panel::customize_chrome::mojom::ActionId::kFocusYoutube,
          prefs::kShowFocusYoutubeButton));
  pref_change_registrar_.Add(
      prefs::kShowMenuButton,
      base::BindRepeating(&CustomizeToolbarHandler::OnActionPinnedChanged,
                          base::Unretained(this), kActionShowAppMenu,
                          prefs::kShowMenuButton));
  pref_change_registrar_.Add(
      prefs::kShowMediaButton,
      base::BindRepeating(&CustomizeToolbarHandler::OnActionPinnedChanged,
                          base::Unretained(this), kActionMediaControls,
                          prefs::kShowMediaButton));
}

CustomizeToolbarHandler::~CustomizeToolbarHandler() = default;

void CustomizeToolbarHandler::ListActions(ListActionsCallback callback) {
  std::vector<side_panel::customize_chrome::mojom::ActionPtr> actions;
  const raw_ptr<BrowserWindowInterface> bwi =
      webui::GetBrowserWindowInterface(web_contents_);
  if (!bwi) {
    std::move(callback).Run(std::move(actions));
    return;
  }

  const ui::ColorProvider& provider = web_contents_->GetColorProvider();
  const int icon_color_id = ui::kColorSysOnSurface;
  const float scale_factor =
      display::Screen::Get()
          ->GetDisplayNearestWindow(web_contents_->GetTopLevelNativeWindow())
          .device_scale_factor();

  auto home_action = side_panel::customize_chrome::mojom::Action::New(
      MojoActionForChromeAction(kActionHome).value(),
      base::UTF16ToUTF8(l10n_util::GetStringUTF16(IDS_ACCNAME_HOME)),
      prefs()->GetBoolean(prefs::kShowHomeButton), false,
      side_panel::customize_chrome::mojom::CategoryId::kNavigation,
      GURL(webui::EncodePNGAndMakeDataURI(
          ui::ImageModel::FromVectorIcon(
              features::IsRoundedIconsEnabled()
                  ? kHomeIcon
                  : kNavigateHomeChromeRefreshOldIcon,
              icon_color_id)
              .Rasterize(&provider),
          scale_factor)));

  auto forward_action = side_panel::customize_chrome::mojom::Action::New(
      MojoActionForChromeAction(kActionForward).value(),
      base::UTF16ToUTF8(l10n_util::GetStringUTF16(IDS_ACCNAME_FORWARD)),
      prefs()->GetBoolean(prefs::kShowForwardButton), false,
      side_panel::customize_chrome::mojom::CategoryId::kNavigation,
      GURL(webui::EncodePNGAndMakeDataURI(
          ui::ImageModel::FromVectorIcon(
              features::IsRoundedIconsEnabled()
                  ? vector_icons::kArrowForwardIcon
                  : vector_icons::kForwardArrowChromeRefreshOldIcon,
              icon_color_id)
              .Rasterize(&provider),
          scale_factor)));

  auto back_action = side_panel::customize_chrome::mojom::Action::New(
      MojoActionForChromeAction(kActionBack).value(),
      base::UTF16ToUTF8(l10n_util::GetStringUTF16(IDS_ACCNAME_BACK)),
      prefs()->GetBoolean(prefs::kShowBackButton), false,
      side_panel::customize_chrome::mojom::CategoryId::kNavigation,
      GURL(webui::EncodePNGAndMakeDataURI(
          ui::ImageModel::FromVectorIcon(
              vector_icons::kBackArrowChromeRefreshOldIcon, icon_color_id)
              .Rasterize(&provider),
          scale_factor)));

  auto reload_action = side_panel::customize_chrome::mojom::Action::New(
      MojoActionForChromeAction(kActionReload).value(),
      base::UTF16ToUTF8(l10n_util::GetStringUTF16(IDS_ACCNAME_RELOAD)),
      prefs()->GetBoolean(prefs::kShowReloadButton), false,
      side_panel::customize_chrome::mojom::CategoryId::kNavigation,
      GURL(webui::EncodePNGAndMakeDataURI(
          ui::ImageModel::FromVectorIcon(
              vector_icons::kReloadChromeRefreshOldIcon, icon_color_id)
              .Rasterize(&provider),
          scale_factor)));

  auto vertical_tabs_collapse_action =
      side_panel::customize_chrome::mojom::Action::New(
          MojoActionForChromeAction(kActionToggleCollapseVertical).value(),
          base::UTF16ToUTF8(
              l10n_util::GetStringUTF16(IDS_COLLAPSE_VERTICAL_TABS)),
          prefs()->GetBoolean(prefs::kShowVerticalTabsCollapseButton), false,
          side_panel::customize_chrome::mojom::CategoryId::kNavigation,
          GURL(webui::EncodePNGAndMakeDataURI(
              ui::ImageModel::FromVectorIcon(views::kMenuCloseCustomIcon,
                                             icon_color_id)
                  .Rasterize(&provider),
              scale_factor)));

  auto dynamic_new_tab_action =
      side_panel::customize_chrome::mojom::Action::New(
          MojoActionForChromeAction(kActionNewTab).value(),
          base::UTF16ToUTF8(BrowserActions::GetCleanTitleAndTooltipText(
              l10n_util::GetStringUTF16(IDS_NEW_TAB))),
          prefs()->GetBoolean(prefs::kShowDynamicNewTabButton), false,
          side_panel::customize_chrome::mojom::CategoryId::kNavigation,
          GURL(webui::EncodePNGAndMakeDataURI(
              ui::ImageModel::FromVectorIcon(vector_icons::kAddOldIcon,
                                             icon_color_id)
                  .Rasterize(&provider),
              scale_factor)));

  auto avatar_action = side_panel::customize_chrome::mojom::Action::New(
      MojoActionForChromeAction(kActionAvatar).value(),
      base::UTF16ToUTF8(l10n_util::GetStringUTF16(IDS_PROFILES_MENU_NAME)),
      prefs()->GetBoolean(prefs::kShowAvatarButton), false,
      side_panel::customize_chrome::mojom::CategoryId::kYourChrome,
      GURL(webui::EncodePNGAndMakeDataURI(
          ui::ImageModel::FromVectorIcon(
              vector_icons::kAccountCircleChromeRefreshOldIcon, icon_color_id)
              .Rasterize(&provider),
          scale_factor)));

  auto extensions_action = side_panel::customize_chrome::mojom::Action::New(
      MojoActionForChromeAction(kActionExtensions).value(),
      base::UTF16ToUTF8(l10n_util::GetStringUTF16(IDS_TOOLTIP_EXTENSIONS_BUTTON)),
      prefs()->GetBoolean(prefs::kShowExtensionsButton), false,
      side_panel::customize_chrome::mojom::CategoryId::kYourChrome,
      GURL(webui::EncodePNGAndMakeDataURI(
          ui::ImageModel::FromVectorIcon(
              vector_icons::kExtensionChromeRefreshOldIcon, icon_color_id)
              .Rasterize(&provider),
          scale_factor)));

  auto focus_block_action =
      side_panel::customize_chrome::mojom::Action::New(
          side_panel::customize_chrome::mojom::ActionId::kFocusBlock,
          base::UTF16ToUTF8(UseRussianFocusUi()
                                ? u"FocusBlock — защита от рекламы"
                                : u"FocusBlock — ad protection"),
          prefs()->GetBoolean(prefs::kShowFocusBlockButton), false,
          side_panel::customize_chrome::mojom::CategoryId::kYourChrome,
          GURL(webui::EncodePNGAndMakeDataURI(
              ui::ImageModel::FromVectorIcon(vector_icons::kShieldIcon,
                                             icon_color_id)
                  .Rasterize(&provider),
              scale_factor)));

  auto focus_youtube_action =
      side_panel::customize_chrome::mojom::Action::New(
          side_panel::customize_chrome::mojom::ActionId::kFocusYoutube,
          base::UTF16ToUTF8(UseRussianFocusUi()
                                ? u"FocusYoutube — фокус на видео"
                                : u"FocusYoutube — focus on video"),
          prefs()->GetBoolean(prefs::kShowFocusYoutubeButton), false,
          side_panel::customize_chrome::mojom::CategoryId::kYourChrome,
          GURL(webui::EncodePNGAndMakeDataURI(
              ui::ImageModel::FromVectorIcon(vector_icons::kVideoLibraryIcon,
                                             icon_color_id)
                  .Rasterize(&provider),
              scale_factor)));

  auto menu_action = side_panel::customize_chrome::mojom::Action::New(
      MojoActionForChromeAction(kActionShowAppMenu).value(),
      base::UTF16ToUTF8(l10n_util::GetStringUTF16(IDS_ACCNAME_APP_MENU)),
      prefs()->GetBoolean(prefs::kShowMenuButton), false,
      side_panel::customize_chrome::mojom::CategoryId::kYourChrome,
      GURL(webui::EncodePNGAndMakeDataURI(
          ui::ImageModel::FromVectorIcon(kBrowserToolsOldIcon, icon_color_id)
              .Rasterize(&provider),
          scale_factor)));

  auto media_controls_action = side_panel::customize_chrome::mojom::Action::New(
      MojoActionForChromeAction(kActionMediaControls).value(),
      base::UTF16ToUTF8(l10n_util::GetStringUTF16(
          IDS_OVERFLOW_MENU_ITEM_TEXT_MEDIA_CONTROLS)),
      prefs()->GetBoolean(prefs::kShowMediaButton), false,
      side_panel::customize_chrome::mojom::CategoryId::kYourChrome,
      GURL(webui::EncodePNGAndMakeDataURI(
          ui::ImageModel::FromVectorIcon(
              kMediaToolbarButtonChromeRefreshOldIcon, icon_color_id)
              .Rasterize(&provider),
          scale_factor)));

  actions.push_back(std::move(back_action));
  actions.push_back(std::move(forward_action));
  actions.push_back(std::move(reload_action));
  actions.push_back(std::move(home_action));

  auto split_tab_action = side_panel::customize_chrome::mojom::Action::New(
      MojoActionForChromeAction(kActionSplitTab).value(),
      base::UTF16ToUTF8(l10n_util::GetStringUTF16(IDS_PIN_SPLIT_TAB_TOGGLE)),
      prefs()->GetBoolean(prefs::kPinSplitTabButton), false,
      side_panel::customize_chrome::mojom::CategoryId::kNavigation,
      GURL(webui::EncodePNGAndMakeDataURI(
          ui::ImageModel::FromVectorIcon(features::IsRoundedIconsEnabled()
                                             ? kSplitSceneIcon
                                             : kSplitSceneOldIcon,
                                         icon_color_id)
              .Rasterize(&provider),
          scale_factor)));

  actions.push_back(std::move(split_tab_action));


  actions.push_back(std::move(vertical_tabs_collapse_action));
  actions.push_back(std::move(dynamic_new_tab_action));

  actions.push_back(std::move(focus_block_action));
  actions.push_back(std::move(focus_youtube_action));
  actions.push_back(std::move(extensions_action));
  actions.push_back(std::move(media_controls_action));
  actions.push_back(std::move(avatar_action));
  actions.push_back(std::move(menu_action));

  const auto add_action =
      [&actions, this, &provider, scale_factor, bwi](
          actions::ActionId id,
          side_panel::customize_chrome::mojom::CategoryId category) {
        actions::ActionItem* const scope_action =
            bwi->GetActions()->root_action_item();
        actions::ActionItem* const action_item =
            actions::ActionManager::Get().FindAction(id, scope_action);
        if (!action_item || !action_item->GetVisible()) {
          return;
        }

        if (!action_observations_.contains(id)) {
          action_observations_.emplace(
              id, action_item->AddActionChangedCallback(base::BindRepeating(
                      &CustomizeToolbarHandler::OnActionItemChanged,
                      base::Unretained(this))));
        }

        switch (static_cast<actions::ActionPinnableState>(
            action_item->GetProperty(actions::kActionItemPinnableKey))) {
          case actions::ActionPinnableState::kNotPinnable:
            return;
          case actions::ActionPinnableState::kPinnable:
          case actions::ActionPinnableState::kEnterpriseControlled:
            break;
          default:
            NOTREACHED();
        }

        // If the icon is a vector icon, recolor it to match the spec.
        // Non-vector icons cannot be recolored, but there aren't any of those
        // currently anyways.
        const ui::ImageModel& original_icon = action_item->GetImage();
        const ui::ImageModel recolored_icon =
            original_icon.IsVectorIcon()
                ? ui::ImageModel::FromVectorIcon(
                      *(action_item->GetImage().GetVectorIcon().vector_icon()),
                      icon_color_id,
                      action_item->GetImage().GetVectorIcon().icon_size())
                : original_icon;

        bool has_enterprise_controlled_pinned_state =
            action_item->GetProperty(actions::kActionItemPinnableKey) ==
            std::underlying_type_t<actions::ActionPinnableState>(
                actions::ActionPinnableState::kEnterpriseControlled);
        auto mojo_action = side_panel::customize_chrome::mojom::Action::New(
            MojoActionForChromeAction(id).value(),
            base::UTF16ToUTF8(action_item->GetText()), model_->Contains(id),
            has_enterprise_controlled_pinned_state, category,
            GURL(webui::EncodePNGAndMakeDataURI(
                recolored_icon.Rasterize(&provider), scale_factor)));
        actions.push_back(std::move(mojo_action));
      };

  add_action(kActionSidePanelShowBookmarks,
             side_panel::customize_chrome::mojom::CategoryId::kTools);
  add_action(kActionSidePanelShowHistoryCluster,
             side_panel::customize_chrome::mojom::CategoryId::kTools);
  add_action(kActionShowDownloads,
             side_panel::customize_chrome::mojom::CategoryId::kTools);
  add_action(kActionClearBrowsingData,
             side_panel::customize_chrome::mojom::CategoryId::kTools);
  add_action(kActionNewIncognitoWindow,
             side_panel::customize_chrome::mojom::CategoryId::kTools);

  if (!base::FeatureList::IsEnabled(tabs::kHorizontalTabStripComboButton) &&
      features::HasTabSearchToolbarButton()) {
    add_action(kActionTabSearch,
               side_panel::customize_chrome::mojom::CategoryId::kTools);
  }
  add_action(kActionPrint,
             side_panel::customize_chrome::mojom::CategoryId::kTools);
  add_action(kActionShowTranslate,
             side_panel::customize_chrome::mojom::CategoryId::kTools);
  add_action(kActionQrCodeGenerator,
             side_panel::customize_chrome::mojom::CategoryId::kTools);
  add_action(kActionRouteMedia,
             side_panel::customize_chrome::mojom::CategoryId::kTools);
  add_action(kActionCopyUrl,
             side_panel::customize_chrome::mojom::CategoryId::kTools);
  add_action(kActionSendTabToSelf,
             side_panel::customize_chrome::mojom::CategoryId::kTools);
  add_action(kActionTaskManager,
             side_panel::customize_chrome::mojom::CategoryId::kTools);
  add_action(kActionDevTools,
             side_panel::customize_chrome::mojom::CategoryId::kTools);
  add_action(kActionShowChromeLabs,
             side_panel::customize_chrome::mojom::CategoryId::kTools);

  std::move(callback).Run(std::move(actions));
}

void CustomizeToolbarHandler::ListCategories(ListCategoriesCallback callback) {
  std::vector<side_panel::customize_chrome::mojom::CategoryPtr> categories;

  categories.push_back(side_panel::customize_chrome::mojom::Category::New(
      side_panel::customize_chrome::mojom::CategoryId::kNavigation,
      l10n_util::GetStringUTF8(IDS_NTP_CUSTOMIZE_TOOLBAR_CATEGORY_NAVIGATION)));
  categories.push_back(side_panel::customize_chrome::mojom::Category::New(
      side_panel::customize_chrome::mojom::CategoryId::kYourChrome,
      l10n_util::GetStringUTF8(
          IDS_NTP_CUSTOMIZE_TOOLBAR_CATEGORY_YOUR_CHROME)));
  categories.push_back(side_panel::customize_chrome::mojom::Category::New(
      side_panel::customize_chrome::mojom::CategoryId::kTools,
      l10n_util::GetStringUTF8(
          IDS_NTP_CUSTOMIZE_TOOLBAR_CATEGORY_TOOLS_AND_ACTIONS)));

  std::move(callback).Run(std::move(categories));
}

void CustomizeToolbarHandler::PinAction(
    side_panel::customize_chrome::mojom::ActionId action_id,
    bool pin) {
  if (action_id ==
      side_panel::customize_chrome::mojom::ActionId::kFocusBlock) {
    prefs()->SetBoolean(prefs::kShowFocusBlockButton, pin);
    return;
  }
  if (action_id ==
      side_panel::customize_chrome::mojom::ActionId::kFocusYoutube) {
    prefs()->SetBoolean(prefs::kShowFocusYoutubeButton, pin);
    return;
  }

  const std::optional<actions::ActionId> chrome_action =
      ChromeActionForMojoAction(action_id);
  if (!chrome_action.has_value()) {
    mojo::ReportBadMessage("PinAction called with an unsupported action.");
    return;
  }

  switch (chrome_action.value()) {
    case kActionHome:
      prefs()->SetBoolean(prefs::kShowHomeButton, pin);
      break;
    case kActionForward:
      prefs()->SetBoolean(prefs::kShowForwardButton, pin);
      break;
    case kActionSplitTab:
      prefs()->SetBoolean(prefs::kPinSplitTabButton, pin);
      break;
    case kActionBack:
      prefs()->SetBoolean(prefs::kShowBackButton, pin);
      break;
    case kActionReload:
      prefs()->SetBoolean(prefs::kShowReloadButton, pin);
      break;
    case kActionToggleCollapseVertical:
      prefs()->SetBoolean(prefs::kShowVerticalTabsCollapseButton, pin);
      break;
    case kActionNewTab:
      prefs()->SetBoolean(prefs::kShowDynamicNewTabButton, pin);
      break;
    case kActionAvatar:
      prefs()->SetBoolean(prefs::kShowAvatarButton, pin);
      break;
    case kActionExtensions:
      prefs()->SetBoolean(prefs::kShowExtensionsButton, pin);
      break;
    case kActionShowAppMenu:
      prefs()->SetBoolean(prefs::kShowMenuButton, pin);
      break;
    case kActionMediaControls:
      prefs()->SetBoolean(prefs::kShowMediaButton, pin);
      break;
    default:
      model_->UpdatePinnedState(chrome_action.value(), pin);
      const std::optional<std::string> metrics_name =
          actions::ActionIdMap::ActionIdToString(chrome_action.value());
      CHECK(metrics_name.has_value());
      base::RecordComputedAction(base::StrCat(
          {"Actions.PinnedToolbarButton.", pin ? "Pinned" : "Unpinned",
           ".ByCustomizeChromeSidePanel.", metrics_name.value()}));
      base::RecordComputedAction(base::StrCat({"Actions.PinnedToolbarButton.",
                                               pin ? "Pinned" : "Unpinned",
                                               ".ByCustomizeChromeSidePanel"}));
  }
}

void CustomizeToolbarHandler::GetIsCustomized(
    GetIsCustomizedCallback callback) {
  std::move(callback).Run(!model_->IsDefault());
}

void CustomizeToolbarHandler::ResetToDefault() {
  model_->ResetToDefault();
}

void CustomizeToolbarHandler::OnActionsChanged() {
  client_->NotifyActionsUpdated();
}

void CustomizeToolbarHandler::OnActionPinnedChanged(actions::ActionId id,
                                                    std::string_view pref) {
  const std::optional<side_panel::customize_chrome::mojom::ActionId>
      mojo_action_id = MojoActionForChromeAction(id);
  if (!mojo_action_id.has_value()) {
    return;
  }

  const bool pinned = prefs()->GetBoolean(pref);
  client_->SetActionPinned(mojo_action_id.value(), pinned);
}

void CustomizeToolbarHandler::OnBrowserOwnedActionPinnedChanged(
    side_panel::customize_chrome::mojom::ActionId id,
    std::string_view pref) {
  client_->SetActionPinned(id, prefs()->GetBoolean(pref));
}

void CustomizeToolbarHandler::OnActionItemChanged() {
  client_->NotifyActionsUpdated();
}

PrefService* CustomizeToolbarHandler::prefs() const {
  return Profile::FromBrowserContext(web_contents_->GetBrowserContext())
      ->GetPrefs();
}
