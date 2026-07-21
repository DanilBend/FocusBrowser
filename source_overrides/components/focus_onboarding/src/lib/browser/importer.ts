import { derived, readonly, writable } from "svelte/store";
import * as cr from "../cr";

const browser = cr.ImportDataBrowserProxyImpl.getInstance();

const importableProfilesRaw = writable<cr.BrowserProfile[]>([]);
const importProfilesStateRaw = writable<"loading" | "ready" | "error">("loading");
const importInProgressRaw = writable(false);

enum _FocusImportType {
    EXTENSIONS = 'import_dialog_extensions',
};

export const FocusImportType = {
    ...cr.ImportType,
    ..._FocusImportType,
} as const;
export type FocusImportType = _FocusImportType | cr.ImportType;
export type FocusImportTypes = Record<FocusImportType, boolean>;

export const refreshImportableProfiles = async (): Promise<void> => {
    importProfilesStateRaw.set("loading");
    try {
        importableProfilesRaw.set(await browser.initializeImportDialog());
        importProfilesStateRaw.set("ready");
    } catch {
        importableProfilesRaw.set([]);
        importProfilesStateRaw.set("error");
    }
};

export const setup = () => {
    void refreshImportableProfiles();
}

const convertTasks = (tasks: WhatToImport): FocusImportTypes => {
    return {
        import_dialog_autofill_form_data: !!tasks.autofillFormData,
        import_dialog_bookmarks: !!tasks.bookmarks,
        import_dialog_history: !!tasks.history,
        import_dialog_saved_passwords: !!tasks.savedPasswords,
        import_dialog_search_engine: !!tasks.searchEngine,
        import_dialog_extensions: !!tasks.extensions
    };
}

type Action = () => void;
let queue: [number, FocusImportTypes, Action, Action][] = [];

const runNext = () => {
    if (queue.length === 0) {
        return;
    }

    // Preserve the order the user selected profiles in. The native importer
    // only handles one profile at a time, and LIFO made the remaining profiles
    // run backwards after the first import completed.
    const [ index, tasks, resolve, reject ] = queue.shift()!;

    let settled = false;
    let me: cr.WebUiListener;
    const finish = (succeeded: boolean) => {
        if (settled) {
            return;
        }
        settled = true;
        cr.removeWebUiListener(me);

        if (succeeded) {
            resolve();
        } else {
            reject();
        }

        if (queue.length) {
            // Register the next listener after the current WebUI event has
            // finished dispatching, so it cannot consume the previous
            // profile's terminal status.
            queueMicrotask(runNext);
        } else {
            importInProgressRaw.set(false);
            void refreshImportableProfiles();
        }
    };

    me = cr.addWebUiListener('import-data-status-changed', (status: cr.ImportDataStatus) => {
        if (status === cr.ImportDataStatus.FAILED) {
            finish(false);
        } else if (status === cr.ImportDataStatus.SUCCEEDED) {
            finish(true);
        }
    });

    try {
        browser.importData(index, tasks);
    } catch {
        finish(false);
    }
}

export const importableProfiles = derived(importableProfilesRaw, $profiles => {
    return $profiles.filter(p => {
        return !p.name.toLowerCase().includes('html');
    });
});

export const importProfilesState = readonly(importProfilesStateRaw);
export const importInProgress = readonly(importInProgressRaw);

export type WhatToImport = Partial<{
    autofillFormData: boolean,
    bookmarks: boolean,
    history: boolean,
    savedPasswords: boolean,
    searchEngine: boolean,
    extensions: boolean
}>;

export const importTasksForProfile = (
    profile: cr.BrowserProfile,
): WhatToImport => ({
    autofillFormData: profile.autofillFormData,
    bookmarks: profile.favorites,
    history: profile.history,
    savedPasswords: profile.passwords,
    searchEngine: profile.search,
    extensions: profile.extensions,
});

export const importProfile = (index: number, tasks: WhatToImport) => {
    const wrappedTasks = convertTasks(tasks);

    return new Promise<void>((resolve, reject) => {
        importInProgressRaw.set(true);
        if (queue.push([ index, wrappedTasks, resolve, reject ]) === 1) {
            runNext();
        }
    });
}
