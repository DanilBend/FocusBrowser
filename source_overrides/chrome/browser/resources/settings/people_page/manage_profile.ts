// Copyright 2015 The Chromium Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

/**
 * @fileoverview
 * 'settings-manage-profile' is the settings subpage containing controls to
 * edit a profile's name, icon, and desktop shortcut.
 */
import 'chrome://resources/cr_components/theme_color_picker/theme_color_picker.js';
import 'chrome://resources/cr_elements/cr_button/cr_button.js';
import 'chrome://resources/cr_elements/cr_dialog/cr_dialog.js';
import 'chrome://resources/cr_elements/cr_icon/cr_icon.js';
import 'chrome://resources/cr_elements/cr_input/cr_input.js';
import 'chrome://resources/cr_elements/cr_profile_avatar_selector/cr_profile_avatar_selector.js';
import 'chrome://resources/cr_elements/cr_shared_style.css.js';
import 'chrome://resources/cr_elements/cr_toggle/cr_toggle.js';
import 'chrome://resources/cr_elements/cr_tooltip/cr_tooltip.js';
import '../settings_page/settings_subpage.js';
import '../settings_shared.css.js';

import type {ProfileInfo} from '/shared/settings/people_page/profile_info_browser_proxy.js';
import {ProfileInfoBrowserProxyImpl} from '/shared/settings/people_page/profile_info_browser_proxy.js';
import {CrDialogElement} from 'chrome://resources/cr_elements/cr_dialog/cr_dialog.js';
import type {CrInputElement} from 'chrome://resources/cr_elements/cr_input/cr_input.js';
import type {AvatarIcon} from 'chrome://resources/cr_elements/cr_profile_avatar_selector/cr_profile_avatar_selector.js';
import {WebUiListenerMixin} from 'chrome://resources/cr_elements/web_ui_listener_mixin.js';
import {PolymerElement} from 'chrome://resources/polymer/v3_0/polymer/polymer_bundled.min.js';

import {loadTimeData} from '../i18n_setup.js';
import {routes} from '../route.js';
import type {Route} from '../router.js';
import {RouteObserverMixin, Router} from '../router.js';
import {SettingsViewMixin} from '../settings_page/settings_view_mixin.js';

import {getTemplate} from './manage_profile.html.js';
import type {ManageProfileBrowserProxy} from './manage_profile_browser_proxy.js';
import {ManageProfileBrowserProxyImpl, ProfileShortcutStatus} from './manage_profile_browser_proxy.js';

const SettingsManageProfileElementBase =
    SettingsViewMixin(RouteObserverMixin(WebUiListenerMixin(PolymerElement)));

export interface SettingsManageProfileElement {
  $: {
    nameInput: CrInputElement,
    fileTooLargeDialog: CrDialogElement,
    avatarErrorDialog: CrDialogElement,
    clearAvatarDialog: CrDialogElement,
  };
}

export class SettingsManageProfileElement extends
    SettingsManageProfileElementBase {
  static get is() {
    return 'settings-manage-profile';
  }

  static get template() {
    return getTemplate();
  }

  static get properties() {
    return {
      /**
       * The newly selected avatar. Defaults to null, populated only if the user
       * manually changes the avatar selection. The observer ensures that the
       * changes are propagated to the C++.
       */
      profileAvatar_: {
        type: Object,
        observer: 'profileAvatarChanged_',
      },

      /**
       * The current profile name.
       */
      profileName_: String,

      /**
       * True if the current profile has a shortcut.
       */
      hasProfileShortcut_: Boolean,

      /**
       * True if the current profile has a custom avatar. This is used to
       * determine whether to show the "Clear custom avatar" button.
       */
      hasCustomAvatar_: Boolean,

      /**
       * The available icons for selection.
       */
      availableIcons: {
        type: Array,
        value() {
          return [];
        },
      },

      /**
       * True if the profile shortcuts feature is enabled.
       */
      isProfileShortcutSettingVisible_: Boolean,

      hasEnterpriseLabel_: {
        type: Boolean,
        value() {
          return loadTimeData.getBoolean('hasEnterpriseLabel');
        },
      },


      /**
       * TODO(dpapad): Move this back to the HTML file when the Polymer2 version
       * of the code is deleted. Because of "\" being a special character in a
       * JS string, can't satisfy both Polymer2 and Polymer3 at the same time
       * from the HTML file.
       */
      pattern_: {
        type: String,
        value: '.*\\S.*',
      },
    };
  }

  declare private profileAvatar_: AvatarIcon;
  declare private profileName_: string;
  declare private hasProfileShortcut_: boolean;
  declare availableIcons: AvatarIcon[];
  declare private hasCustomAvatar_: boolean;
  declare private isProfileShortcutSettingVisible_: boolean;
  declare private hasEnterpriseLabel_: boolean;
  declare private pattern_: string;
  private browserProxy_: ManageProfileBrowserProxy =
      ManageProfileBrowserProxyImpl.getInstance();

  override connectedCallback() {
    super.connectedCallback();

    const setIcons = (icons: AvatarIcon[]) => {
      this.availableIcons = icons;
      this.hasCustomAvatar_ = icons.some(icon => icon.isCustomAvatar);
    };

    this.addWebUiListener('available-icons-changed', setIcons);
    this.browserProxy_.getAvailableIcons().then(setIcons);

    this.addWebUiListener(
        'custom-avatar-error', () => this.$.avatarErrorDialog.showModal());

    ProfileInfoBrowserProxyImpl.getInstance().getProfileInfo().then(
        this.onProfileInfoChanged_.bind(this));
    this.addWebUiListener(
        'profile-info-changed', this.onProfileInfoChanged_.bind(this));

    this.addEventListener(
        'custom-avatar-requested', (avatarRequestEvent: Event) => {
          avatarRequestEvent.preventDefault();
          this.onChooseCustomAvatarFile_();
        });

    this.addEventListener('custom-avatar-deleted', () => {
      this.$.clearAvatarDialog.showModal();
    });
  }

  override currentRouteChanged(newRoute: Route, oldRoute?: Route) {
    super.currentRouteChanged(newRoute, oldRoute);

    if (Router.getInstance().getCurrentRoute() === routes.MANAGE_PROFILE) {
      if (this.profileName_) {
        this.$.nameInput.value = this.profileName_;
      }
      if (loadTimeData.getBoolean('profileShortcutsEnabled')) {
        this.browserProxy_.getProfileShortcutStatus().then(status => {
          if (status ===
              ProfileShortcutStatus.PROFILE_SHORTCUT_SETTING_HIDDEN) {
            this.isProfileShortcutSettingVisible_ = false;
            return;
          }

          this.isProfileShortcutSettingVisible_ = true;
          this.hasProfileShortcut_ =
              status === ProfileShortcutStatus.PROFILE_SHORTCUT_FOUND;
        });
      }
    }
  }

  private onProfileInfoChanged_(info: ProfileInfo) {
    this.profileName_ = info.name;
  }

  private onChooseCustomAvatarFile_() {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = 'image/png,image/jpeg,image/jpg';
    input.onchange = () => {
      const file = input.files?.[0];
      if (!file) {
        this.$.avatarErrorDialog.showModal();
        return;
      }
      if (file.size > 30 * 1024 * 1024) {
        // The file is too large. Show an error and return early.
        this.$.fileTooLargeDialog.showModal();
        return;
      }
      const reader = new FileReader();
      reader.onload = () => {
        const dataUrl = reader.result;
        if (!dataUrl || typeof dataUrl !== 'string') {
          this.$.avatarErrorDialog.showModal();
          return;
        }
        this.browserProxy_.setCustomAvatarFromFile(dataUrl);
      };
      reader.readAsDataURL(file);
    };
    input.click();
  }

  private onClearAvatarCancel_() {
    this.$.clearAvatarDialog.cancel();
  }

  private onClearCustomAvatar_() {
    this.browserProxy_.clearCustomAvatar();
    this.$.clearAvatarDialog.close();
  }

  private onCloseAvatarErrorDialog_() {
    this.$.avatarErrorDialog.close();
  }

  private onCloseFileTooLargeDialog_() {
    this.$.fileTooLargeDialog.close();
  }

  /**
   * Handler for when the profile name field is changed, then blurred.
   */
  private onNameInputChange_(event: Event) {
    const target = event.target as CrInputElement;
    if (target.invalid) {
      return;
    }

    this.browserProxy_.setProfileName(target.value);
  }

  /**
   * Handler for profile name keydowns.
   */
  private onNameInputKeydown_(event: KeyboardEvent) {
    if (event.key === 'Escape') {
      const target = event.target as CrInputElement;
      target.value = this.profileName_;
      target.blur();
    }
  }

  /**
   * Handler for when the profile avatar is changed by the user.
   */
  private profileAvatarChanged_() {
    if (this.profileAvatar_ === null) {
      return;
    }

    if (this.profileAvatar_.isGaiaAvatar) {
      this.browserProxy_.setProfileIconToGaiaAvatar();
    } else if (
        this.profileAvatar_.isCustomAvatar &&
        !this.profileAvatar_.isPlaceholder) {
      this.browserProxy_.setProfileIconToCustomAvatar();
    } else {
      this.browserProxy_.setProfileIconToDefaultAvatar(
          this.profileAvatar_.index);
    }
  }

  /**
   * Handler for when the profile shortcut toggle is changed.
   */
  private onHasProfileShortcutChange_() {
    if (this.hasProfileShortcut_) {
      this.browserProxy_.addProfileShortcut();
    } else {
      this.browserProxy_.removeProfileShortcut();
    }
  }

  // SettingsViewMixin implementation.
  override focusBackButton() {
    this.shadowRoot!.querySelector('settings-subpage')!.focusBackButton();
  }
}

declare global {
  interface HTMLElementTagNameMap {
    'settings-manage-profile': SettingsManageProfileElement;
  }
}

customElements.define(
    SettingsManageProfileElement.is, SettingsManageProfileElement);
