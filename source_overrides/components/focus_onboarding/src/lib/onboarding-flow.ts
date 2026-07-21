import { SvelteSet } from "svelte/reactivity";
import { derived, get, writable } from "svelte/store";

import {
    canBeDefaultBrowser,
    defaultBrowserAvailabilityState,
    isDefaultBrowser,
} from "./browser";

export const flow = [
    "Welcome",
    "FocusSetup",
    "AppearanceSetup",
    "SearchEngine",
    "DataImport",
    "PasswordManager",
    "DefaultBrowser",
    "ShortcutsSetup"
] as const;

const index = writable(0);

export const currentPage = derived(
    index,
    $index => flow[$index]
);

const shouldSkip = (page: typeof flow[number]): boolean => {
    switch (page) {
        case 'PasswordManager':
            // Focus Password Manager is built in and does not depend on the
            // extension store or any Focus network service.
            return false;
        case 'DefaultBrowser':
            // Fail closed until the native handler confirms that this install
            // can safely open the OS default-browser flow. We also skip on a
            // request error or when the browser is already the default.
            return get(defaultBrowserAvailabilityState) !== "ready"
                || !get(canBeDefaultBrowser)
                || get(isDefaultBrowser);
        default:
            return false;
    }
}

const getPageNumber = (current: number, direction = 1) => {
    let next = current + direction;

    while (
        next >= 0 &&
        next < flow.length &&
        shouldSkip(flow[next])
    ) {
        next += direction;
    }

    return Math.max(0, Math.min(next, flow.length - 1));
}

export const nextPage = () => {
    index.update((current) => getPageNumber(current));
}

export const previousPage = () => {
    index.update((current) => getPageNumber(current, -1));
}

export const goToShortcutsSetup = () => {
    index.set(flow.indexOf("ShortcutsSetup"));
}

export const userChoseFocusAsDefault = writable(false);
export const quietNotifications = writable(true);
export const minimalInterface = writable(true);
export const smoothAnimations = writable(true);
export const locationBarStyle = writable<"full" | "centered" | "minimal">("centered");
export const selectedProfiles = writable(new SvelteSet<number>());
export const previouslyImportedProfiles = writable(new SvelteSet<number>());
export const selectedShortcuts = writable(new SvelteSet<string>());
