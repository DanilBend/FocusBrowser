import * as cr from "../cr";
import { readonly, writable } from "svelte/store";
import { searchEngineConfig } from "../search-engine-config";

const browser = cr.SearchEnginesBrowserProxyImpl.getInstance();
const _searchEngines = writable<cr.SearchEngine[]>([]);
const _searchEnginesState = writable<"loading" | "ready" | "error">("loading");

const engineOrder = Object.fromEntries(
    Object.keys(searchEngineConfig).map((key, i) => [key, i])
);

// use the order defined in searchEngineConfig and
// push custom engines (not in config) to the end
const engineIndex = (keyword: string) =>
    engineOrder[keyword.split(".")[0]] ?? Infinity;

const sortEngines = (engines: cr.SearchEngine[]) => {
    return engines.sort((a, b) =>
        engineIndex(a.keyword) - engineIndex(b.keyword)
    );
};

export const setup = () => {
    browser.getSearchEnginesList().then(
        engines => {
            _searchEngines.set(sortEngines(engines.defaults));
            _searchEnginesState.set("ready");
        },
        () => _searchEnginesState.set("error"),
    );
}

cr.addWebUiListener(
    'search-engines-changed',
    (state: cr.SearchEnginesInfo) => {
        _searchEngines.set(sortEngines(state.defaults));
        _searchEnginesState.set("ready");
    }
);

export const searchEngines = readonly(_searchEngines);
export const searchEnginesState = readonly(_searchEnginesState);

export const setDefaultEngine = (browserIndex: number) => {
    browser.setDefaultSearchEngine(
        browserIndex,
        cr.ChoiceMadeLocation.CHOICE_SCREEN,
        null
    );
}
