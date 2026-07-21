// Copyright 2026 The Focus Browser Authors
// You can use, redistribute, and/or modify this source code under
// the terms of the GPL-3.0 license that can be found in the LICENSE file.

#ifndef CHROME_BROWSER_UI_WEBUI_MEDITATION_MEDITATION_UI_H_
#define CHROME_BROWSER_UI_WEBUI_MEDITATION_MEDITATION_UI_H_

#include "chrome/common/webui_url_constants.h"
#include "content/public/browser/web_ui_controller.h"
#include "content/public/browser/webui_config.h"

namespace meditation {

class MeditationUI : public content::WebUIController {
 public:
  explicit MeditationUI(content::WebUI* web_ui);
  MeditationUI(const MeditationUI&) = delete;
  MeditationUI& operator=(const MeditationUI&) = delete;
  ~MeditationUI() override;
};

class MeditationUIConfig
    : public content::DefaultWebUIConfig<MeditationUI> {
 public:
  MeditationUIConfig()
      : DefaultWebUIConfig(content::kChromeUIScheme,
                           chrome::kChromeUIMeditationHost) {}
};

}  // namespace meditation

#endif  // CHROME_BROWSER_UI_WEBUI_MEDITATION_MEDITATION_UI_H_
