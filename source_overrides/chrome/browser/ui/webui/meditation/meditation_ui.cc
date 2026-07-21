// Copyright 2026 The Focus Browser Authors
// You can use, redistribute, and/or modify this source code under
// the terms of the GPL-3.0 license that can be found in the LICENSE file.

#include "chrome/browser/ui/webui/meditation/meditation_ui.h"

#include "chrome/browser/profiles/profile.h"
#include "chrome/grit/meditation_resources.h"
#include "chrome/grit/meditation_resources_map.h"
#include "components/focus_services/pref_names.h"
#include "components/prefs/pref_service.h"
#include "content/public/browser/web_ui.h"
#include "content/public/browser/web_ui_data_source.h"
#include "services/network/public/mojom/content_security_policy.mojom.h"
#include "ui/webui/webui_util.h"

namespace meditation {

MeditationUI::MeditationUI(content::WebUI* web_ui)
    : WebUIController(web_ui) {
  Profile* profile = Profile::FromWebUI(web_ui);
  content::WebUIDataSource* source = content::WebUIDataSource::CreateAndAdd(
      profile, chrome::kChromeUIMeditationHost);

  webui::SetupWebUIDataSource(source, kMeditationResources,
                              IDR_MEDITATION_MEDITATION_HTML);
  source->AddBoolean(
      "focusMotionEnabled",
      profile->GetPrefs()->GetBoolean(prefs::kFocusMotionEnabled));

  source->OverrideContentSecurityPolicy(
      network::mojom::CSPDirectiveName::ConnectSrc,
      "connect-src 'self';");
  source->OverrideContentSecurityPolicy(
      network::mojom::CSPDirectiveName::ImgSrc,
      "img-src 'self' data:;");
}

MeditationUI::~MeditationUI() = default;

}  // namespace meditation
