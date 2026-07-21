<script lang="ts">
    import { s } from "../lib/strings";
    import { applyFocusSettings } from "../lib/browser";
    import {
        currentPage,
        goToShortcutsSetup,
        nextPage,
        smoothAnimations,
    } from "../lib/onboarding-flow";

    import FocusLogo from "../icons/FocusLogo.svelte";
    import IconCheck from "../icons/tabler/IconCheck.svelte";
    import IconArrowRight from "../icons/tabler/IconArrowRight.svelte";
    import Spinner from "../components/Spinner.svelte";

    let presetWorking = $state(false);
    let presetError = $state("");

    const useFocusPreset = async () => {
        presetWorking = true;
        presetError = "";
        try {
            $smoothAnimations = true;
            await applyFocusSettings({
                quietNotifications: true,
                minimalInterface: true,
                smoothAnimations: true,
                locationBarStyle: "centered",
            });
            goToShortcutsSetup();
        } catch {
            presetError = s.navigation.error;
        } finally {
            presetWorking = false;
        }
    }

    const visible = $derived($currentPage === "Welcome");
</script>

<div
    id="welcome-page"
    class="onboarding-page"
    class:visible
>
    <div id="welcome-shell">
        <div id="welcome-mark" aria-hidden="true">
            <FocusLogo />
        </div>

        <div id="welcome-copy">
            <div id="welcome-eyebrow">
                <span class="status-dot"></span>
                {s.welcome.eyebrow}
            </div>
            <h1 id="welcome-greet">{s.welcome.greeting}</h1>
            <p id="welcome-body">{s.welcome.body}</p>
        </div>

        <div id="focus-signals" aria-label={s.welcome.featuresLabel}>
            <span><IconCheck /> {s.welcome.featureQuiet}</span>
            <span><IconCheck /> {s.welcome.featureClean}</span>
            <span><IconCheck /> {s.welcome.featureIntent}</span>
        </div>

        <div id="welcome-buttons" class="action-buttons">
            <button class="preset" disabled={presetWorking} onclick={useFocusPreset}>
                {#if presetWorking}
                    <Spinner />
                {:else}
                    <IconCheck />
                {/if}
                {s.button.useDefaults}
            </button>
            <button class="primary" disabled={presetWorking} onclick={nextPage}>
                {s.button.configure}
                <IconArrowRight />
            </button>
        </div>

        {#if presetError}
            <p id="welcome-error" role="alert">{presetError}</p>
        {/if}

        <p id="welcome-footer">{s.welcome.footer}</p>
    </div>
</div>

<style>
    #welcome-page {
        visibility: hidden;
        padding: 24px;
        box-sizing: border-box;
        overflow-x: hidden;
        overflow-y: auto;
        text-align: center;

        &.visible {
            visibility: visible;
        }

        &:not(.visible) {
            animation: page-out 0.2s;
            animation-fill-mode: forwards;
        }
    }

    #welcome-shell {
        width: min(880px, calc(100vw - 48px));
        display: flex;
        flex-direction: column;
        align-items: center;
        gap: 28px;
        position: relative;
        margin-block: auto;
        padding-block: 24px;
        box-sizing: border-box;
    }

    #welcome-mark {
        width: 86px;
        height: 86px;
        display: grid;
        place-items: center;
        background: transparent;
        border: 0;
        box-shadow: none;
    }

    #welcome-mark :global(svg) {
        height: 54px;
        width: 54px;
    }

    #welcome-copy {
        display: flex;
        flex-direction: column;
        align-items: center;
        gap: 16px;
        max-width: 820px;
    }

    #welcome-eyebrow {
        display: inline-flex;
        align-items: center;
        gap: 9px;
        color: #d4d4d8;
        font-size: 13px;
        line-height: 1;
        letter-spacing: 0.12em;
        text-transform: uppercase;
    }

    .status-dot {
        width: 7px;
        height: 7px;
        border-radius: 50%;
        background: #f4f4f5;
        box-shadow: 0 0 18px rgba(255, 255, 255, 0.8);
    }

    #welcome-greet {
        max-width: 820px;
        font-size: clamp(52px, 7vw, 82px);
        line-height: 0.98;
        letter-spacing: -0.065em;
        text-wrap: balance;
    }

    #welcome-body {
        max-width: 660px;
        font-size: 20px;
        line-height: 1.5;
        text-wrap: balance;
    }

    #focus-signals {
        display: flex;
        justify-content: center;
        flex-wrap: wrap;
        gap: 10px;
    }

    #focus-signals span {
        display: inline-flex;
        align-items: center;
        gap: 7px;
        padding: 9px 13px;
        border-radius: 999px;
        color: #d4d4d8;
        background: rgba(255, 255, 255, 0.04);
        border: 1px solid rgba(255, 255, 255, 0.08);
        font-size: 13px;
    }

    #focus-signals :global(svg) {
        width: 15px;
        height: 15px;
    }

    #welcome-buttons {
        margin-top: 4px;
    }

    #welcome-buttons button {
        min-height: 48px;
        padding-inline: 22px;
        border-radius: 999px;
    }

    #welcome-buttons .preset {
        color: #e4e4e7;
        background: rgba(255, 255, 255, 0.055);
    }

    #welcome-footer {
        color: #71717a;
        font-size: 12px;
    }

    #welcome-error {
        max-width: 520px;
        box-sizing: border-box;
        padding: 9px 13px;
        color: #f4f4f5;
        font-size: 12.5px;
        line-height: 1.35;
        border: 1px solid rgba(255, 255, 255, 0.12);
        border-radius: 12px;
        background: rgba(24, 24, 27, 0.94);
    }

    .visible {
        #welcome-mark {
            animation: intro-logo 0.55s 0.15s both;
        }

        #welcome-eyebrow,
        #welcome-greet,
        #welcome-body,
        #focus-signals,
        #welcome-buttons,
        #welcome-error,
        #welcome-footer {
            animation: intro-in 0.4s both;
        }

        #welcome-eyebrow { animation-delay: 0.35s; }
        #welcome-greet { animation-delay: 0.42s; }
        #welcome-body { animation-delay: 0.5s; }
        #focus-signals { animation-delay: 0.58s; }
        #welcome-buttons { animation-delay: 0.66s; }
        #welcome-error { animation-delay: 0s; }
        #welcome-footer { animation-delay: 0.74s; }
    }

    @media (max-width: 640px) {
        #welcome-shell {
            gap: 20px;
        }

        #welcome-mark {
            width: 72px;
            height: 72px;
            border-radius: 20px;
        }

        #welcome-mark :global(svg) {
            width: 44px;
            height: 44px;
        }

        #welcome-greet {
            font-size: clamp(44px, 13vw, 64px);
        }

        #welcome-body {
            font-size: 17px;
        }

        #focus-signals {
            display: none;
        }

        #welcome-buttons {
            width: min(420px, 100%);
            flex-direction: column-reverse;
        }

        #welcome-buttons button {
            width: 100%;
            justify-content: center;
        }
    }

    @media (max-height: 720px) {
        #welcome-shell {
            gap: 18px;
            margin-block: 0;
        }

        #welcome-mark {
            width: 68px;
            height: 68px;
        }

        #welcome-mark :global(svg) {
            width: 42px;
            height: 42px;
        }

        #welcome-greet {
            font-size: clamp(42px, 8vh, 62px);
        }

        #welcome-body {
            font-size: 17px;
        }
    }

    @media (prefers-reduced-motion) {
        #welcome-page.visible * {
            animation-delay: 0s;
            animation-duration: 0.15s;
        }
    }
</style>
