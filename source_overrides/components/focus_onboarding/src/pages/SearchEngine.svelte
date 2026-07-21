<script lang="ts">
    import { s } from "../lib/strings";
    import { searchEngines, searchEnginesState } from "../lib/browser";
    import { currentPage } from "../lib/onboarding-flow";
    import { searchEngineIcons } from "../lib/brand-icons";

    import PageHeader from "../components/PageHeader.svelte";
    import IconSearch from "../icons/tabler/IconSearch.svelte";
    import SearchEngineItem from "../components/SearchEngineItem.svelte";

    const searchDescs: Record<string, string> = s.searchEngines;

    const iconPath = (engine: string) =>
        searchEngineIcons[engine.toLowerCase()] ?? "";

    const visible = $derived($currentPage === "SearchEngine");
</script>

<div id="search-engines-page" class="onboarding-page" class:visible>
    <div id="search-engines-page-container" class="scrollable-page">
        <PageHeader
            title={s.search.title}
            subtitle={s.search.subtitle}
            Icon={IconSearch}
        />

        <div id="content" class="page-content">
            {#if $searchEnginesState === "loading"}
                <p class="search-state" role="status">{s.search.loading}</p>
            {:else if $searchEnginesState === "error"}
                <p class="search-state error" role="alert">{s.search.error}</p>
            {:else if $searchEngines.length === 0}
                <p class="search-state" role="status">{s.search.none}</p>
            {/if}

            {#each $searchEngines as e}
                {@const engineKey = e.keyword.replace(/[^\x00-\x7F]/g, '').split(".")[0]}
                <SearchEngineItem
                    id={engineKey}
                    name={e.name}
                    desc={searchDescs[engineKey]}
                    iconPath={iconPath(engineKey)}
                    browserId={e.id}
                    isDefault={e.default}
                />
            {/each}
        </div>
    </div>
</div>

<style>
    #search-engines-page {
        justify-content: flex-start;
        visibility: hidden;

        &.visible {
            visibility: visible;
            animation: page-in 0.3s;
            animation-delay: 0.05s;
            animation-fill-mode: backwards;
        }

        &:not(.visible) {
            animation: page-out 0.2s;
            animation-fill-mode: forwards;
        }
    }

    #search-engines-page-container,
    #content {
        width: 100%;
    }

    #content {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        align-items: stretch;
        gap: 12px;
    }

    .search-state {
        width: 100%;
        box-sizing: border-box;
        padding: 18px;
        border-radius: 16px;
        background: rgba(255, 255, 255, 0.035);
        border: 1px solid rgba(255, 255, 255, 0.08);

        &.error {
            color: #f4f4f5;
        }
    }

    @media (max-width: 720px) {
        #content {
            grid-template-columns: 1fr;
        }
    }
</style>
