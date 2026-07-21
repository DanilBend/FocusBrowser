// Copyright 2026 The Focus Authors
// You can use, redistribute, and/or modify this source code under
// the terms of the GPL-3.0 license that can be found in the LICENSE file.

import 'chrome://resources/cr_elements/action_link.css.js';
import 'chrome://resources/cr_elements/cr_button/cr_button.js';
import 'chrome://resources/cr_elements/cr_dialog/cr_dialog.js';
import 'chrome://resources/cr_elements/cr_icon_button/cr_icon_button.js';
import 'chrome://resources/js/action_link.js';
import '../settings_page/settings_subpage.js';
import '../settings_shared.css.js';

import {PolymerElement} from 'chrome://resources/polymer/v3_0/polymer/polymer_bundled.min.js';

import {BrowserShortcutAssignmentResult, BrowserShortcutsHandlerFactory, BrowserShortcutsHandlerRemote, BrowserShortcutsPageCallbackRouter} from '../browser_shortcuts.mojom-webui.js';
import type {BrowserShortcutAccelerator, BrowserShortcutCommand} from '../browser_shortcuts.mojom-webui.js';
import {loadTimeData} from '../i18n_setup.js';

import {getTemplate} from './browser_shortcuts_page.html.js';
import {SystemPageBrowserProxyImpl} from './system_page_browser_proxy.js';

type Command = BrowserShortcutCommand;
type Accelerator = BrowserShortcutAccelerator;

interface DialogState {
  command: Command;
}

function isModifierKey(e: KeyboardEvent): boolean {
  return e.key === 'Alt' || e.key === 'AltGraph' || e.key === 'Control' ||
      e.key === 'Meta' || e.key === 'Shift';
}

export class SettingsBrowserShortcutsPageElement extends PolymerElement {
  static get is() {
    return 'settings-browser-shortcuts-page';
  }

  static get template() {
    return getTemplate();
  }

  static get properties() {
    return {
      routePath: String,
      commands_: {
        type: Array,
        value: () => [],
      },
      filteredCommands_: {
        type: Array,
        value: () => [],
      },
      commandsLoaded_: {
        type: Boolean,
        value: false,
      },
      hasModifiedCommands_: {
        type: Boolean,
        value: false,
      },
      loadingRows_: {
        type: Array,
        value: () => Array.from({length: 10}, (_, index) => index),
      },
      searchTerm_: {
        type: String,
        value: '',
        observer: 'onSearchTermChanged_',
      },
      showCaptureDialog_: {
        type: Boolean,
        value: false,
      },
      showResetDialog_: {
        type: Boolean,
        value: false,
      },
      capturedAccelerator_: String,
      capturedDisplayText_: String,
      captureMessage_: String,
      canSaveCaptured_: {
        type: Boolean,
        value: false,
      },
    };
  }

  declare routePath: string;
  declare private commands_: Command[];
  declare private filteredCommands_: Command[];
  declare private commandsLoaded_: boolean;
  declare private hasModifiedCommands_: boolean;
  declare private loadingRows_: number[];
  declare private searchTerm_: string;
  declare private showCaptureDialog_: boolean;
  declare private showResetDialog_: boolean;
  declare private capturedAccelerator_: string;
  declare private capturedDisplayText_: string;
  declare private captureMessage_: string;
  declare private canSaveCaptured_: boolean;

  private handler_: BrowserShortcutsHandlerRemote =
      new BrowserShortcutsHandlerRemote();
  private callbackRouter_: BrowserShortcutsPageCallbackRouter =
      new BrowserShortcutsPageCallbackRouter();
  private systemBrowserProxy_ = SystemPageBrowserProxyImpl.getInstance();
  private dialogState_: DialogState|null = null;
  private keyDisplayRequestId_ = 0;

  override connectedCallback() {
    super.connectedCallback();

    BrowserShortcutsHandlerFactory.getRemote().createBrowserShortcutsHandler(
        this.callbackRouter_.$.bindNewPipeAndPassRemote(),
        this.handler_.$.bindNewPipeAndPassReceiver());
    this.callbackRouter_.changed.addListener(event => {
      Object.values(event.addedOrUpdated)
          .forEach((command: Command) => this.upsertCommand_(command));
      event.removed.forEach(commandId => {
        this.commands_ =
            this.commands_.filter(command => command.commandId !== commandId);
      });
      this.sortAndFilter_();
    });
    this.handler_.getCommands().then(({commands}) => {
      this.commands_ = commands;
      this.commandsLoaded_ = true;
      this.sortAndFilter_();
    });
  }

  private upsertCommand_(command: Command) {
    const index = this.commands_.findIndex(
        existing => existing.commandId === command.commandId);
    if (index === -1) {
      this.push('commands_', command);
      return;
    }
    this.splice('commands_', index, 1, command);
  }

  private sortAndFilter_() {
    this.hasModifiedCommands_ =
        this.commands_.some(command => command.modified);

    const commands =
        [...this.commands_].sort((a, b) => a.name.localeCompare(b.name));
    const query = this.normalizeSearchText_(this.searchTerm_);
    if (!query) {
      this.filteredCommands_ = commands;
      return;
    }

    this.filteredCommands_ =
        commands
            .map(command => ({
                   command,
                   rank: this.getMatchRank_(command, query),
                 }))
            .filter(match => match.rank !== null)
            .sort((a, b) => {
              if (a.rank !== b.rank) {
                return a.rank! - b.rank!;
              }
              return a.command.name.localeCompare(b.command.name);
            })
            .map(match => match.command);
  }

  private onSearchTermChanged_() {
    this.sortAndFilter_();
  }

  private normalizeSearchText_(text: string): string {
    return text.trim().toLocaleLowerCase().replace(/\s+/g, '');
  }

  private getMatchRank_(command: Command, query: string): number|null {
    const ranks =
        [
          command.name,
          ...command.accelerators.flatMap(
              accelerator =>
                  [accelerator.displayText, accelerator.serializedAccelerator]),
        ].map(field => this.normalizeSearchText_(field))
            .flatMap(field => {
              const index = field.indexOf(query);
              const rank = Math.min(index, 1) + Number(field !== query);
              return index === -1 ? [] : [rank];
            });

    return ranks.length ? Math.min(...ranks) : null;
  }

  private showNoResults_(commandsLoaded: boolean, filteredLength: number):
      boolean {
    return commandsLoaded && filteredLength === 0;
  }

  private onAddShortcutClick_(e: Event) {
    const command = this.getCommandFromEvent_(e);
    if (!command) {
      return;
    }
    this.dialogState_ = {
      command,
    };
    this.capturedAccelerator_ = '';
    this.capturedDisplayText_ = '';
    this.captureMessage_ = '';
    this.canSaveCaptured_ = false;
    this.keyDisplayRequestId_++;
    this.showCaptureDialog_ = true;
    queueMicrotask(() => {
      this.shadowRoot!.querySelector<HTMLElement>('#captureTarget')?.focus();
    });
  }

  private onCaptureKeyDown_(e: KeyboardEvent) {
    e.preventDefault();
    e.stopPropagation();

    if (e.code === 'Escape' && !e.altKey && !e.ctrlKey && !e.metaKey &&
        !e.shiftKey) {
      this.closeDialogs_();
      return;
    }

    if (e.code === 'Enter' && this.canSaveCaptured_) {
      this.onSaveShortcutClick_();
      return;
    }

    if (!e.code || isModifierKey(e)) {
      this.capturedAccelerator_ = '';
      this.capturedDisplayText_ = '';
      this.canSaveCaptured_ = false;
      this.captureMessage_ = loadTimeData.getString('shortcutsNeedKey');
      this.keyDisplayRequestId_++;
      return;
    }

    const parts: string[] = [];
    if (e.ctrlKey) {
      parts.push('Control');
    }
    if (e.altKey) {
      parts.push('Alt');
    }
    if (e.shiftKey) {
      parts.push('Shift');
    }
    if (e.metaKey) {
      parts.push('Meta');
    }
    parts.push(e.code);
    this.capturedAccelerator_ = parts.join('+');
    const requestAccelerator = this.capturedAccelerator_;
    const requestId = ++this.keyDisplayRequestId_;
    this.handler_.getAcceleratorDisplayText(requestAccelerator)
        .then(({displayText}) => {
          if (requestId !== this.keyDisplayRequestId_ ||
              requestAccelerator !== this.capturedAccelerator_) {
            return;
          }
          this.capturedDisplayText_ = displayText || this.capturedAccelerator_;
          this.updateCaptureValidation_();
        });
  }

  private updateCaptureValidation_() {
    const conflict = this.findConflict_(this.capturedAccelerator_);
    this.canSaveCaptured_ = !!this.capturedAccelerator_ && !conflict?.locked;
    if (conflict?.locked) {
      this.captureMessage_ =
          loadTimeData.getStringF('shortcutsSystemConflict', conflict.name);
    } else if (conflict) {
      this.captureMessage_ =
          loadTimeData.getStringF('shortcutsWillOverride', conflict.name);
    } else {
      this.captureMessage_ = '';
    }
  }

  private findConflict_(serializedAccelerator: string):
      {locked: boolean, name: string}|null {
    if (!this.dialogState_) {
      return null;
    }
    for (const command of this.commands_) {
      for (const accelerator of command.accelerators) {
        if (accelerator.serializedAccelerator === serializedAccelerator &&
            command.commandId !== this.dialogState_.command.commandId) {
          return {locked: !accelerator.userModifiable, name: command.name};
        }
      }
    }
    return null;
  }

  private onCancelDialogClick_() {
    this.closeDialogs_();
  }

  private async onSaveShortcutClick_() {
    if (!this.dialogState_ || !this.canSaveCaptured_) {
      return;
    }
    const {response} = await this.handler_.assignAcceleratorToCommand(
        this.dialogState_.command.commandId, this.capturedAccelerator_);
    if (response.result === BrowserShortcutAssignmentResult.kSuccess) {
      this.closeDialogs_();
      return;
    }
    this.captureMessage_ =
        response.result === BrowserShortcutAssignmentResult.kShortcutLocked ?
        loadTimeData.getStringF(
            'shortcutsSystemConflict', response.conflictingCommandName) :
        loadTimeData.getString('shortcutsInvalid');
  }

  private closeDialogs_() {
    this.showCaptureDialog_ = false;
    this.showResetDialog_ = false;
    this.dialogState_ = null;
    this.keyDisplayRequestId_++;
  }

  private onRemoveShortcutClick_(e: Event) {
    const target = e.currentTarget as HTMLElement;
    const commandId = Number(target.dataset['commandId']);
    const serializedAccelerator = target.dataset['serializedAccelerator'];
    if (!Number.isFinite(commandId) || !serializedAccelerator) {
      return;
    }
    this.handler_.unassignAcceleratorFromCommand(
        commandId, serializedAccelerator);
  }

  private onResetCommandClick_(e: Event) {
    const command = this.getCommandFromEvent_(e);
    if (!command) {
      return;
    }
    this.handler_.resetAcceleratorsForCommand(command.commandId);
  }

  private onResetAllClick_() {
    if (!this.hasModifiedCommands_) {
      return;
    }
    this.showResetDialog_ = true;
  }

  private onResetDialogConfirm_() {
    this.handler_.resetAccelerators();
    this.showResetDialog_ = false;
  }

  private onMacSystemShortcutsLinkClick_(e: Event) {
    e.preventDefault();
    this.systemBrowserProxy_.openKeyboardShortcutsSettings();
  }

  private getCapturedDisplayText_(displayText: string): string {
    return displayText || loadTimeData.getString('shortcutsPressShortcut');
  }

  private getRemoveLabel_(commandName: string, shortcut: string): string {
    return loadTimeData.getStringF(
        'shortcutsRemoveA11yLabel', shortcut, commandName);
  }

  private getCommandFromEvent_(e: Event): Command|null {
    const target = e.currentTarget as HTMLElement;
    const commandId = Number(target.dataset['commandId']);
    if (!Number.isFinite(commandId)) {
      return null;
    }
    return this.commands_.find(command => command.commandId === commandId) ||
        null;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    'settings-browser-shortcuts-page': SettingsBrowserShortcutsPageElement;
  }
}

customElements.define(
    SettingsBrowserShortcutsPageElement.is,
    SettingsBrowserShortcutsPageElement);
