<script lang="ts">
    import { s } from "../lib/strings";
    import type { BrowserProfile } from "../lib/cr";
    import {
        currentPage,
        previouslyImportedProfiles,
        selectedProfiles,
    } from "../lib/onboarding-flow";
    import {
        importableProfiles,
        importProfile,
        importProfilesState,
        importTasksForProfile,
    } from "../lib/browser";
    import { browserIcons } from "../lib/brand-icons";

    import PageHeader from "../components/PageHeader.svelte";
    import ProfileImportOption from "../components/ProfileImportOption.svelte";

    import IconWorld from "../icons/tabler/IconWorld.svelte";
    import IconTransferIn from "../icons/tabler/IconTransferIn.svelte";

    const visible = $derived($currentPage === "DataImport");

    const quickChromeProfiles = $derived(
        $importableProfiles.filter(profile => profile.name === "Google Chrome")
    );
    const quickAlreadyImported = $derived(
        quickChromeProfiles.length > 0 &&
        quickChromeProfiles.every(
            profile => $previouslyImportedProfiles.has(profile.index),
        )
    );
    let quickState = $state<"idle" | "working" | "done" | "failed">("idle");

    const quickImportChrome = async () => {
        if (quickChromeProfiles.length === 0 || quickAlreadyImported || quickState === "working") return;
        quickState = "working";
        try {
            const pending = quickChromeProfiles.filter(
                profile => !$previouslyImportedProfiles.has(profile.index),
            );
            const results = await Promise.allSettled(pending.map(profile =>
                importProfile(profile.index, importTasksForProfile(profile))
            ));

            let failed = false;
            results.forEach((result, resultIndex) => {
                const profileIndex = pending[resultIndex].index;
                if (result.status === "fulfilled") {
                    $previouslyImportedProfiles.add(profileIndex);
                    $selectedProfiles.delete(profileIndex);
                } else {
                    failed = true;
                }
            });
            if (failed) {
                throw new Error("one or more Chrome profile imports failed");
            }
            quickState = "done";
        } catch {
            quickState = "failed";
        }
    };

    const groupedProfiles = $derived(
        $importableProfiles.reduce(
            (acc, profile) => {
                (acc[profile.name] ??= []).push(profile);
                return acc;
            },
            {} as Record<string, BrowserProfile[]>
        )
    );

    const sorted = $derived(
        Object.entries(groupedProfiles).sort(([a], [b]) => a.localeCompare(b))
    );

    const browserIconMap = {
        Arc: "arc",
        Brave: "brave",
        Chromium: "chromium",
        Dia: "dia",
        Opera: "opera",
        Safari: "safari",
        Vivaldi: "vivaldi",
        Yandex: "yandex",
        Zen: "zen",

        "Google Chrome": "chrome",
        "Google Chrome Beta": "chrome_beta",
        "Google Chrome Canary": "chrome_canary",
        "Google Chrome Dev": "chrome_dev",
        "Microsoft Edge": "edge",
        "Mozilla Firefox": "firefox",
        "NAVER Whale": "whale",
        "Perplexity Comet": "comet",
    } as const;
</script>

<div id="data-import-page" class="onboarding-page" class:visible>
    <div id="data-import-container" class="scrollable-page">
        <PageHeader
            title={s.dataImport.title}
            subtitle={quickChromeProfiles.length > 0
                ? s.dataImport.subtitle
                : s.dataImport.subtitleNoChrome}
            Icon={IconTransferIn}
        />
        <div id="content" class="page-content">
            {#if quickChromeProfiles.length > 0}
                <div class="quick-import-card">
                    <div class="quick-icon">
                        <img src={browserIcons.chrome} alt="" />
                    </div>
                    <div class="quick-copy">
                        <h3>{s.dataImport.chromeQuickTitle}</h3>
                        <p>{s.dataImport.chromeQuickDesc}</p>
                        {#if quickState === "failed"}
                            <span class="quick-error">{s.dataImport.chromeQuickFailed}</span>
                        {/if}
                    </div>
                    <button
                        class="primary quick-button"
                        disabled={quickState === "working" || quickState === "done" || quickAlreadyImported}
                        onclick={quickImportChrome}
                    >
                        {#if quickState === "working"}
                            {s.dataImport.chromeQuickWorking}
                        {:else if quickState === "done" || quickAlreadyImported}
                            {s.dataImport.chromeQuickDone}
                        {:else if quickState === "failed"}
                            {s.dataImport.chromeQuickRetry}
                        {:else}
                            {s.dataImport.chromeQuickButton}
                        {/if}
                    </button>
                </div>
            {/if}

            {#if $importProfilesState === "loading"}
                <p class="empty-state" role="status">{s.dataImport.loading}</p>
            {:else if $importProfilesState === "error"}
                <p class="empty-state error" role="alert">{s.dataImport.loadError}</p>
            {:else if sorted.length === 0}
                <p class="empty-state" role="status">{s.dataImport.none}</p>
            {/if}

            {#each sorted as [browser, profiles]}
                {@const icon = browserIconMap[browser as keyof typeof browserIconMap]}
                {@const url = icon && browserIcons[icon]}
                <section class="browser-group">
                    <div class="browser-header">
                        <div class="browser-icon" aria-hidden="true">
                            {#if url}
                                <img src={url} alt="" />
                            {:else}
                                <IconWorld />
                            {/if}
                        </div>
                        <h4>{browser}</h4>
                    </div>
                    <div class="browser-profiles">
                        {#each profiles as profile}
                            <ProfileImportOption {profile} />
                        {/each}
                    </div>
                </section>
            {/each}
        </div>
    </div>
</div>

<style>
    #data-import-page {
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

    #data-import-container {
        width: 100%;
        max-width: none;
    }

    #content {
        gap: 14px;
        width: 100%;
    }

    .quick-import-card {
        width: 100%;
        box-sizing: border-box;
        display: grid;
        grid-template-columns: 48px minmax(0, 1fr) auto;
        align-items: center;
        gap: 16px;
        padding: 18px;
        margin-bottom: 8px;
        border-radius: 18px;
        background: rgba(255, 255, 255, 0.07);
        border: 1px solid rgba(255, 255, 255, 0.2);
        box-shadow: 0 18px 55px rgba(0, 0, 0, 0.24);
    }

    .quick-icon {
        width: 46px;
        height: 46px;
        display: grid;
        place-items: center;
        border-radius: 14px;
        background: #fff;
    }

    .quick-icon img {
        width: 32px;
        height: 32px;
    }

    .quick-copy {
        display: flex;
        flex-direction: column;
        gap: 4px;
    }

    .empty-state {
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

    .quick-copy h3 {
        font-size: 17px;
        line-height: 1.25;
    }

    .quick-copy p,
    .quick-error {
        color: #a1a1aa;
        font-size: 13px;
        line-height: 1.35;
    }

    .quick-error {
        color: #f4f4f5;
    }

    .quick-button {
        min-width: 118px;
        white-space: nowrap;
    }

    .browser-header {
        display: flex;
        flex-direction: row;
        align-items: center;
        gap: 8px;

        & > :global(svg) {
            stroke-width: 1.5px;
        }

        & .browser-icon {
            width: 28px;
            height: 28px;
            display: flex;
            justify-content: center;
            align-items: center;

            & img {
                width: 28px;
                height: 28px;
            }
        }

        & > h4 {
            font-size: 15px;
            line-height: 1.2;
        }
    }

    .browser-group {
        width: 100%;
        display: flex;
        flex-direction: column;
        gap: 10px;
        padding: 16px;
        border: 1px solid rgba(255, 255, 255, 0.09);
        border-radius: 18px;
        background: rgba(255, 255, 255, 0.025);
    }

    .browser-profiles {
        display: flex;
        flex-direction: column;
        width: 100%;
        gap: 7px;
    }

    @media (max-width: 640px) {
        .quick-import-card {
            grid-template-columns: 42px 1fr;
        }

        .quick-button {
            grid-column: 1 / -1;
            width: 100%;
        }

        .browser-group {
            padding: 13px;
        }
    }
</style>
