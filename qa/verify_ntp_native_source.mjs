#!/usr/bin/env node

import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const sourceRoot = path.resolve(
    process.argv[2] || path.join(scriptDir, '..', 'build', 'src'));

const read = relativePath => fs.readFileSync(
    path.join(sourceRoot, ...relativePath.split('/')), 'utf8');

const source = {
  prefNames: read('chrome/browser/new_tab_page/ntp_pref_names.h'),
  ntpUi: read('chrome/browser/ui/webui/new_tab_page/new_tab_page_ui.cc'),
  ntpAppCss: read('chrome/browser/resources/new_tab_page/app.css'),
  ntpDocument: read(
      'chrome/browser/resources/new_tab_page/new_tab_page.html'),
  mostVisitedMojom:
      read('ui/webui/resources/cr_components/most_visited/most_visited.mojom'),
  mostVisitedHandler: read(
      'chrome/browser/ui/webui/cr_components/most_visited/most_visited_handler.cc'),
  mostVisitedHandlerTest: read(
      'chrome/browser/ui/webui/cr_components/most_visited/most_visited_handler_unittest.cc'),
  mostVisitedTs:
      read('ui/webui/resources/cr_components/most_visited/most_visited.ts'),
  customizeMojom: read(
      'chrome/browser/ui/webui/side_panel/customize_chrome/customize_chrome.mojom'),
  customizeHandler: read(
      'chrome/browser/ui/webui/side_panel/customize_chrome/customize_chrome_page_handler.cc'),
  customizeHandlerHeader: read(
      'chrome/browser/ui/webui/side_panel/customize_chrome/customize_chrome_page_handler.h'),
  customizeHandlerTest: read(
      'chrome/browser/ui/webui/side_panel/customize_chrome/customize_chrome_page_handler_unittest.cc'),
  customizeUi: read(
      'chrome/browser/ui/webui/side_panel/customize_chrome/customize_chrome_ui.cc'),
  shortcutsHtml: read(
      'chrome/browser/resources/side_panel/customize_chrome/shortcuts.html.ts'),
  shortcutsTs: read(
      'chrome/browser/resources/side_panel/customize_chrome/shortcuts.ts'),
  sidePanelController: read(
      'chrome/browser/ui/views/side_panel/customize_chrome/side_panel_controller_views.cc'),
  sidePanelControllerHeader: read(
      'chrome/browser/ui/views/side_panel/customize_chrome/side_panel_controller_views.h'),
  sidePanelBrowserTest: read(
      'chrome/browser/ui/webui/side_panel/customize_chrome/customize_chrome_browsertest.cc'),
  customizeChromeShell: read(
      'chrome/browser/resources/side_panel/customize_chrome/customize_chrome.html'),
  customizeCategories: read(
      'chrome/browser/resources/side_panel/customize_chrome/categories.css'),
};

const checks = {
  prefDeclared: /kNtpAddShortcutVisible\[\][\s\S]*ntp\.add_shortcut_visible/
      .test(source.prefNames),
  prefRegisteredAndReset:
      /RegisterBooleanPref\(ntp_prefs::kNtpAddShortcutVisible, true\)/
          .test(source.ntpUi) &&
      /SetBoolean\(ntp_prefs::kNtpAddShortcutVisible, true\)/
          .test(source.ntpUi),
  customizeMojoRoundTrip:
      /SetAddShortcutVisible\(bool visible\)/.test(source.customizeMojom) &&
      /bool add_shortcut_visible/.test(source.customizeMojom),
  customizeHandlerObservesAndWritesPref:
      /pref_change_registrar_\.Add\([\s\S]*?kNtpAddShortcutVisible[\s\S]*?UpdateMostVisitedSettings/
          .test(source.customizeHandler) &&
      /SetAddShortcutVisible\(bool visible\)[\s\S]*?SetBoolean\([\s\S]*?kNtpAddShortcutVisible, visible\)/
          .test(source.customizeHandler) &&
      /SetAddShortcutVisible\(bool visible\) override/
          .test(source.customizeHandlerHeader),
  customizePageReceivesPref:
      /SetMostVisitedSettings\([\s\S]*?GetBoolean\(ntp_prefs::kNtpAddShortcutVisible\)/
          .test(source.customizeHandler),
  customizeToggleWired:
      /id="addShortcutToggle"/.test(source.shortcutsHtml) &&
      /\?checked="\$\{this\.showAddShortcut_\}"/.test(source.shortcutsHtml) &&
      /handler\.setAddShortcutVisible\(show\)/.test(source.shortcutsTs) &&
      /addShortcutVisible: boolean/.test(source.shortcutsTs) &&
      /addShortcutTitle/.test(source.customizeUi),
  mostVisitedMojoAndRendererWired:
      /bool add_shortcut_visible/.test(source.mostVisitedMojom) &&
      /this\.addShortcutVisible_ = this\.info_\.addShortcutVisible/
          .test(source.mostVisitedTs) &&
      /computeShowAdd_\(\): boolean \{\s*if \(!this\.addShortcutVisible_\)/
          .test(source.mostVisitedTs),
  mostVisitedHandlerObservesAndSendsPref:
      /pref_change_registrar_\.Add\([\s\S]*?kNtpAddShortcutVisible[\s\S]*?UpdateMostVisitedInfo/
          .test(source.mostVisitedHandler) &&
      /add_shortcut_visible\s*=\s*[\s\S]*?GetBoolean\(ntp_prefs::kNtpAddShortcutVisible\)/
          .test(source.mostVisitedHandler),
  addShortcutTestsPresent:
      /AddShortcutVisibilityIsSentToPage/.test(source.mostVisitedHandlerTest) &&
      /EXPECT_FALSE\(info->add_shortcut_visible\)/
          .test(source.mostVisitedHandlerTest) &&
      /SetAddShortcutVisible/.test(source.customizeHandlerTest) &&
      /kNtpAddShortcutVisible/.test(source.customizeHandlerTest),
  closePolicyIsNtpTriggerOnly:
      /close_on_navigation_\s*=\s*trigger\s*==\s*SidePanelOpenTrigger::kNewTabPage/
          .test(source.sidePanelController) &&
      !/close_on_navigation_\s*=\s*trigger\s*==\s*SidePanelOpenTrigger::kAppMenu/
          .test(source.sidePanelController) &&
      /bool close_on_navigation_ = false/
          .test(source.sidePanelControllerHeader),
  ntpPanelClosesAsBackgrounded:
      /close_on_navigation_\s*&&\s*!theme_editable[\s\S]*?IsCustomizeChromeEntryShowing\(\)[\s\S]*?Close\(SidePanelEntryHideReason::kBackgrounded[\s\S]*?close_on_navigation_ = false/
          .test(source.sidePanelController),
  closeAndAppMenuRegressionTestsPresent:
      /CloseNtpCustomizePanelOnNavigation[\s\S]*?SidePanelOpenTrigger::kNewTabPage[\s\S]*?EXPECT_FALSE/
          .test(source.sidePanelBrowserTest) &&
      /DeregisterCustomizeChromeSidePanel[\s\S]*?SidePanelOpenTrigger::kAppMenu[\s\S]*?kChromeUISettingsURL[\s\S]*?EXPECT_TRUE/
          .test(source.sidePanelBrowserTest),
  customizePanelLegacyBlueAliasesNeutralized:
      /--google-blue-500:\s*var\(--focus-panel-tile-accent\)/
          .test(source.customizeChromeShell) &&
      /--google-blue-700:\s*var\(--focus-panel-tile-foreground\)/
          .test(source.customizeChromeShell),
  cornerNtpTileUsesNeutralTokens:
      /#cornerNewTabPageTile\s*\{[\s\S]*?corner-ntp-background/
          .test(source.customizeCategories) &&
      /#foreground\s*\{[\s\S]*?corner-ntp-foreground/
          .test(source.customizeCategories) &&
      /#background\s*\{[\s\S]*?corner-ntp-accent/
          .test(source.customizeCategories) &&
      !/rgb\(\s*211\s*,\s*227\s*,\s*253\s*\)/
          .test(source.customizeCategories),
  fullPageEntryMotionRemoved:
      !/focus-home-enter/.test(source.ntpAppCss) &&
      !/#focusHome\s*\{[^}]*\banimation\s*:/s.test(source.ntpAppCss),
  earlyOpaqueNtpBackground:
      /SetPageBaseBackgroundColor\([\s\S]*?kColorNewTabPageBackground/
          .test(source.ntpUi) &&
      /html\s*\{[\s\S]*?background:\s*\$i18n\{backgroundColor\}/
          .test(source.ntpDocument) &&
      /ntp-app\s*\{[\s\S]*?background:\s*\$i18n\{backgroundColor\}/
          .test(source.ntpDocument) &&
      /#content\s*\{[\s\S]*?background:\s*var\(--focus-ntp-page-background/
          .test(source.ntpAppCss) &&
      !/#content:has\(#searchbox:not\(\[is-dark\]\)\)\s*\{/
          .test(source.ntpAppCss),
  distinctNeutralSearchSurfacePalette:
      /searchbox-background:\s*rgba\(72, 74, 74, 0\.98\)/
          .test(source.ntpAppCss) &&
      /results-background:\s*rgb\(72, 74, 74\)/.test(source.ntpAppCss) &&
      /results-background-hovered:\s*rgb\(79, 81, 81\)/
          .test(source.ntpAppCss) &&
      /results-background-selected:\s*rgb\(86, 88, 88\)/
          .test(source.ntpAppCss) &&
      /results-focus-indicator:\s*rgba\(255, 255, 255, 0\.38\)/
          .test(source.ntpAppCss) &&
      /cr-searchbox-border:\s*1px solid rgba\(255, 255, 255, 0\.12\)/
          .test(source.ntpAppCss) &&
      /cr-searchbox-shadow:\s*0 10px 28px rgba\(0, 0, 0, 0\.20\),\s*0 2px 6px rgba\(0, 0, 0, 0\.12\)/
          .test(source.ntpAppCss) &&
      /searchbox-background:\s*rgba\(255, 255, 255, 0\.99\)/
          .test(source.ntpAppCss) &&
      /results-background-hovered:\s*rgb\(247, 248, 248\)/
          .test(source.ntpAppCss) &&
      /results-background-selected:\s*rgb\(242, 244, 244\)/
          .test(source.ntpAppCss) &&
      /results-focus-indicator:\s*rgba\(38, 41, 41, 0\.38\)/
          .test(source.ntpAppCss) &&
      /cr-searchbox-border:\s*1px solid rgba\(28, 30, 30, 0\.12\)/
          .test(source.ntpAppCss) &&
      /cr-searchbox-shadow:\s*0 10px 28px rgba\(0, 0, 0, 0\.10\),\s*0 2px 6px rgba\(0, 0, 0, 0\.06\)/
          .test(source.ntpAppCss),
};

const failed = Object.entries(checks)
    .filter(([, passed]) => !passed)
    .map(([name]) => name);

assert.deepEqual(failed, [], JSON.stringify({sourceRoot, checks}, null, 2));
console.log(JSON.stringify({sourceRoot, checks}, null, 2));
