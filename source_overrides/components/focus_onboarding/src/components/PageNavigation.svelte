<script lang="ts">
    import { s } from "../lib/strings";
    import {
        applyFocusSettings,
        askToBeDefault,
        importInProgress,
        importProfilesState,
        importableProfiles,
        importProfile,
        importTasksForProfile,
        setNtpShortcuts,
        setPref,
        searchEnginesState,
    } from "../lib/browser";
    import {
        flow,
        nextPage,
        previousPage,
        currentPage,
        userChoseFocusAsDefault,
        quietNotifications,
        minimalInterface,
        smoothAnimations,
        locationBarStyle,
        selectedProfiles,
        previouslyImportedProfiles,
        selectedShortcuts,
    } from "../lib/onboarding-flow";

    import Spinner from "./Spinner.svelte";

    import IconArrowLeft from "../icons/tabler/IconArrowLeft.svelte";
    import IconArrowRight from "../icons/tabler/IconArrowRight.svelte";

    const visible = $derived($currentPage !== "Welcome");

    let working = $state(false);
    let errorMessage = $state("");
    const navigationDisabled = $derived(
        working ||
        ($currentPage === "DataImport" &&
            ($importInProgress || $importProfilesState === "loading")) ||
        ($currentPage === "SearchEngine" && $searchEnginesState === "loading"),
    );

    const nextLabel = $derived(
        $currentPage === "ShortcutsSetup"
            ? ($selectedShortcuts.size === 0
                ? s.button.skip
                : s.button.start)
            : s.button.next
    );

    const doNext = async (): Promise<boolean> => {
        switch ($currentPage) {
        case "AppearanceSetup":
            await applyFocusSettings({
                quietNotifications: $quietNotifications,
                minimalInterface: $minimalInterface,
                smoothAnimations: $smoothAnimations,
                locationBarStyle: $locationBarStyle,
            });
            break;

        case "DefaultBrowser":
            if ($userChoseFocusAsDefault && !askToBeDefault()) {
                throw new Error("default browser request is not available");
            }
            break;

        case "DataImport":
            const selected = [...$selectedProfiles];
            const results = await Promise.allSettled(selected.map(index => {
                const profile = $importableProfiles.find(
                    candidate => candidate.index === index,
                );
                if (!profile) {
                    return Promise.reject(new Error("profile is no longer available"));
                }
                return importProfile(index, importTasksForProfile(profile));
            }));

            let failed = false;
            results.forEach((result, resultIndex) => {
                const profileIndex = selected[resultIndex];
                if (result.status === "fulfilled") {
                    $previouslyImportedProfiles.add(profileIndex);
                    $selectedProfiles.delete(profileIndex);
                } else {
                    failed = true;
                }
            });
            if (failed) {
                throw new Error("one or more profile imports failed");
            }
            break;

        case "ShortcutsSetup":
            await setNtpShortcuts([...$selectedShortcuts]);
            await setPref('completed_onboarding', true);
            window.open("chrome://newtab", "_self");
            return false;
        }

        return true;
    };

    const next = () => {
        working = true;
        errorMessage = "";
        doNext()
            .then((shouldAdvance) => {
                if (shouldAdvance) {
                    nextPage();
                }
            })
            .catch(() => {
                errorMessage = s.navigation.error;
            })
            .finally(() => {
                working = false;
            });
    }

    const back = () => {
        errorMessage = "";
        previousPage();
    };

    const stepNumber = $derived(flow.indexOf($currentPage));
    const stepCount = flow.length - 1;
</script>

<div id="setup-navigation" class:visible aria-busy={navigationDisabled}>
    {#if errorMessage}
        <p id="setup-error" role="alert">{errorMessage}</p>
    {/if}
    <div id="setup-buttons">
        <button class="back" disabled={navigationDisabled} onclick={back}>
            <IconArrowLeft />
            {s.button.back}
        </button>
        <span id="setup-progress">
            {s.navigation.step} {stepNumber} {s.navigation.of} {stepCount}
        </span>
        <button disabled={navigationDisabled} class="primary" onclick={next}>
            {nextLabel}
            {#if working}
                <Spinner />
            {:else}
                <IconArrowRight />
            {/if}
        </button>
    </div>
</div>

<style>
    #setup-navigation {
        width: 100%;
        position: relative;
        z-index: 2;
        display: flex;
        flex-direction: column;
        gap: 0;
        visibility: hidden;
        border-top: 1px solid rgba(255, 255, 255, 0.09);
        background: rgba(9, 9, 11, 0.9);
        backdrop-filter: blur(18px);

        &.visible {
            visibility: visible;
            animation: footer-in 0.24s 0.12s backwards;
        }
    }

    #setup-buttons {
        min-height: 76px;
        display: grid;
        grid-template-columns: 1fr auto 1fr;
        align-items: center;
        gap: 18px;
        padding: 14px 40px;
    }

    #setup-buttons button {
        min-height: 44px;
        border-radius: 999px;
        white-space: nowrap;
    }

    #setup-buttons .back {
        justify-self: start;
    }

    #setup-buttons .primary {
        justify-self: end;
    }

    #setup-progress {
        color: #85858e;
        font-size: 12.5px;
        font-weight: 550;
        white-space: nowrap;
    }

    #setup-error {
        width: 100%;
        padding: 9px 40px;
        color: #f4f4f5;
        font-size: 12.5px;
        line-height: 1.35;
        border-bottom: 1px solid rgba(255, 255, 255, 0.09);
        background: rgba(34, 34, 38, 0.92);
        text-align: center;
    }

    @keyframes footer-in {
        from {
            opacity: 0;
            translate: 0 12px;
        }
    }

    @media (prefers-reduced-motion) {
        #setup-navigation.visible {
            animation: none;
        }
    }

    @media (max-width: 640px) {
        #setup-buttons {
            min-height: 72px;
            grid-template-columns: 1fr 1fr;
            gap: 10px;
            padding: 12px 16px;
        }

        #setup-buttons button {
            width: 100%;
            justify-content: center;
            padding-inline: 14px;
        }

        #setup-progress {
            display: none;
        }

        #setup-error {
            padding-inline: 16px;
        }
    }
</style>
