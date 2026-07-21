// Copyright 2025 The Focus Authors
// You can use, redistribute, and/or modify this source code under
// the terms of the GPL-3.0 license that can be found in the LICENSE file.

import '../controls/settings_toggle_button.js';
import '../settings_page/settings_section.js';

import {PrefsMixin} from '/shared/settings/prefs/prefs_mixin.js';
import {PolymerElement} from 'chrome://resources/polymer/v3_0/polymer/polymer_bundled.min.js';

import {getTemplate} from './appearance_behavior_page.html.js';

const AppearanceBehaviorPageElementBase = PrefsMixin(PolymerElement);

export class AppearanceBehaviorPageElement extends
    AppearanceBehaviorPageElementBase {
  static get is() {
    return 'settings-appearance-behavior-page';
  }

  static get template() {
    return getTemplate();
  }
}

declare global {
  interface HTMLElementTagNameMap {
    'settings-appearance-behavior-page': AppearanceBehaviorPageElement;
  }
}

customElements.define(AppearanceBehaviorPageElement.is, AppearanceBehaviorPageElement);
