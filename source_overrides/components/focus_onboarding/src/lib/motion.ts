import { get } from "svelte/store";

import { preferences } from "./browser";
import { smoothAnimations } from "./onboarding-flow";

let didSetup = false;

const setDocumentMotionState = (
    enabled: boolean,
    systemPrefersReducedMotion: boolean,
) => {
    document.documentElement.dataset.motion =
        enabled && !systemPrefersReducedMotion ? "on" : "off";
};

export const setupMotionPreference = () => {
    if (didSetup) {
        return;
    }
    didSetup = true;

    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
    const updateDocument = () => {
        setDocumentMotionState(get(smoothAnimations), reducedMotion.matches);
    };

    let initializedFromPreferences = false;
    preferences.subscribe(prefs => {
        const storedValue = prefs["ui.motion_enabled"];
        if (!initializedFromPreferences && typeof storedValue === "boolean") {
            initializedFromPreferences = true;
            smoothAnimations.set(storedValue);
        }
    });

    smoothAnimations.subscribe(updateDocument);
    reducedMotion.addEventListener("change", updateDocument);
    updateDocument();
};
