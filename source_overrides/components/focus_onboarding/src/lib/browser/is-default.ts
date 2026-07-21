import { get, readonly, writable } from "svelte/store";
import * as cr from "../cr";

export type DefaultBrowserAvailabilityState = "loading" | "ready" | "error";

const _defaultBrowser = writable(false);
const _canBeDefaultBrowser = writable(false);
const _availabilityState =
    writable<DefaultBrowserAvailabilityState>("loading");

const browser = cr.DefaultBrowserBrowserProxyImpl.getInstance();
let defaultBrowserRequestStarted = false;

const applyDefaultBrowserState = (state: cr.DefaultBrowserInfo) => {
    _defaultBrowser.set(state.isDefault === true);
    _canBeDefaultBrowser.set(
        state.canBeDefault === true
        && state.isDisabledByPolicy !== true
        && state.isUnknownError !== true,
    );
    _availabilityState.set("ready");
};

const markDefaultBrowserStateUnavailable = () => {
    _defaultBrowser.set(false);
    _canBeDefaultBrowser.set(false);
    _availabilityState.set("error");
};

export const setup = () => {
    let receivedLiveState = false;
    browser.requestDefaultBrowserState()
        .then(state => {
            if (!receivedLiveState) {
                applyDefaultBrowserState(state);
            }
        })
        .catch(() => {
            if (!receivedLiveState) {
                markDefaultBrowserStateUnavailable();
            }
        });

    cr.addWebUiListener(
        'browser-default-state-changed',
        (state: cr.DefaultBrowserInfo) => {
            receivedLiveState = true;
            defaultBrowserRequestStarted = false;
            applyDefaultBrowserState(state);
        },
    );
}

export const askToBeDefault = (): boolean => {
    if (defaultBrowserRequestStarted) {
        return true;
    }

    if (
        get(_availabilityState) !== "ready"
        || !get(_canBeDefaultBrowser)
        || get(_defaultBrowser)
    ) {
        return false;
    }

    defaultBrowserRequestStarted = true;
    try {
        browser.setAsDefaultBrowser(/*pin=*/false);
        return true;
    } catch {
        defaultBrowserRequestStarted = false;
        markDefaultBrowserStateUnavailable();
        return false;
    }
}

export const isDefaultBrowser = readonly(_defaultBrowser);
export const canBeDefaultBrowser = readonly(_canBeDefaultBrowser);
export const defaultBrowserAvailabilityState = readonly(_availabilityState);
