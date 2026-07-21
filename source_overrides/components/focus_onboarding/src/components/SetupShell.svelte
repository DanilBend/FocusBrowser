<script lang="ts">
    import type { Snippet } from "svelte";

    import { s } from "../lib/strings";
    import { currentPage, flow } from "../lib/onboarding-flow";

    import FocusLogo from "../icons/FocusLogo.svelte";
    import IconCheck from "../icons/tabler/IconCheck.svelte";
    import PageNavigation from "./PageNavigation.svelte";

    type Props = {
        children: Snippet;
    };

    const { children }: Props = $props();

    const steps = [
        { id: "FocusSetup", label: () => s.setup.stepFocus },
        { id: "AppearanceSetup", label: () => s.setup.stepAppearance },
        { id: "SearchEngine", label: () => s.setup.stepSearch },
        { id: "DataImport", label: () => s.setup.stepImport },
        { id: "PasswordManager", label: () => s.setup.stepPasswords },
        { id: "DefaultBrowser", label: () => s.setup.stepDefault },
        { id: "ShortcutsSetup", label: () => s.setup.stepShortcuts },
    ] as const;

    const currentIndex = $derived(flow.indexOf($currentPage));
</script>

<div id="setup-shell">
    <aside id="setup-sidebar">
        <div id="setup-brand">
            <span id="setup-logo" aria-hidden="true"><FocusLogo /></span>
            <span id="setup-brand-copy">
                <strong>Focus Browser</strong>
                <small>{s.setup.caption}</small>
            </span>
        </div>

        <ol id="setup-steps" aria-label={s.setup.stepsAria}>
            {#each steps as step, stepIndex}
                {@const flowIndex = flow.indexOf(step.id)}
                <li
                    class:active={$currentPage === step.id}
                    class:complete={flowIndex < currentIndex}
                    aria-current={$currentPage === step.id ? "step" : undefined}
                >
                    <span class="step-mark" aria-hidden="true">
                        {#if flowIndex < currentIndex}
                            <IconCheck />
                        {:else}
                            {stepIndex + 1}
                        {/if}
                    </span>
                    <span class="step-label">{step.label()}</span>
                </li>
            {/each}
        </ol>

        <p id="setup-note">{s.setup.note}</p>
    </aside>

    <section id="setup-stage">
        <div id="setup-pages">
            {@render children()}
        </div>
        <PageNavigation />
    </section>
</div>

<style>
    #setup-shell {
        width: min(1120px, calc(100vw - 64px));
        height: min(800px, calc(100dvh - 48px));
        min-height: 620px;
        display: grid;
        grid-template-columns: 220px minmax(0, 1fr);
        overflow: hidden;
        position: relative;
        border: 1px solid rgba(255, 255, 255, 0.12);
        border-radius: 28px;
        background: rgba(9, 9, 11, 0.96);
        box-shadow: 0 36px 110px rgba(0, 0, 0, 0.56);
        isolation: isolate;
    }

    #setup-sidebar {
        min-width: 0;
        display: flex;
        flex-direction: column;
        gap: 32px;
        padding: 28px 20px 24px;
        border-right: 1px solid rgba(255, 255, 255, 0.09);
        background: linear-gradient(180deg, rgba(255, 255, 255, 0.045), rgba(255, 255, 255, 0.018));
    }

    #setup-brand {
        display: flex;
        align-items: center;
        gap: 12px;
        min-width: 0;
    }

    #setup-logo {
        width: 42px;
        height: 42px;
        flex: 0 0 auto;
        display: grid;
        place-items: center;
        border: 0;
        border-radius: 0;
        background: transparent;
    }

    #setup-logo :global(svg) {
        width: 28px;
        height: 28px;
    }

    #setup-brand-copy {
        min-width: 0;
        display: flex;
        flex-direction: column;
        gap: 3px;
    }

    #setup-brand-copy strong {
        overflow: hidden;
        color: #fafafa;
        font-size: 15px;
        font-weight: 650;
        line-height: 1.1;
        text-overflow: ellipsis;
        white-space: nowrap;
    }

    #setup-brand-copy small {
        color: #8d8d96;
        font-size: 11.5px;
        line-height: 1.2;
    }

    #setup-steps {
        display: flex;
        flex-direction: column;
        gap: 6px;
        margin: 0;
        padding: 0;
        list-style: none;
    }

    #setup-steps li {
        min-width: 0;
        min-height: 44px;
        display: grid;
        grid-template-columns: 28px minmax(0, 1fr);
        align-items: center;
        gap: 10px;
        padding: 7px 9px;
        color: #86868f;
        border: 1px solid transparent;
        border-radius: 12px;
        transition: 160ms color, 160ms background, 160ms border-color;
    }

    #setup-steps li.complete {
        color: #b4b4bc;
    }

    #setup-steps li.active {
        color: #fff;
        border-color: rgba(255, 255, 255, 0.12);
        background: rgba(255, 255, 255, 0.07);
    }

    .step-mark {
        width: 26px;
        height: 26px;
        display: grid;
        place-items: center;
        color: #a1a1aa;
        border: 1px solid rgba(255, 255, 255, 0.11);
        border-radius: 9px;
        background: rgba(255, 255, 255, 0.035);
        font-size: 11px;
        font-weight: 650;
    }

    .complete .step-mark,
    .active .step-mark {
        color: #09090b;
        border-color: #f4f4f5;
        background: #f4f4f5;
    }

    .step-mark :global(svg) {
        width: 14px;
        height: 14px;
        stroke-width: 2.2;
    }

    .step-label {
        min-width: 0;
        font-size: 13px;
        font-weight: 550;
        line-height: 1.2;
    }

    #setup-note {
        margin-top: auto;
        color: #777780;
        font-size: 11.5px;
        line-height: 1.45;
        text-align: left;
    }

    #setup-stage {
        min-width: 0;
        min-height: 0;
        display: grid;
        grid-template-rows: minmax(0, 1fr) auto;
        background: radial-gradient(circle at 54% 0%, rgba(255, 255, 255, 0.055), transparent 38%);
    }

    #setup-pages {
        min-width: 0;
        min-height: 0;
        position: relative;
        overflow: hidden;
    }

    @media (max-width: 900px) {
        #setup-shell {
            width: calc(100vw - 28px);
            height: calc(100dvh - 28px);
            min-height: 0;
            grid-template-columns: 1fr;
            grid-template-rows: 88px minmax(0, 1fr);
            border-radius: 22px;
        }

        #setup-sidebar {
            display: grid;
            grid-template-columns: auto minmax(0, 1fr);
            align-items: center;
            gap: 20px;
            padding: 14px 18px;
            border-right: 0;
            border-bottom: 1px solid rgba(255, 255, 255, 0.09);
        }

        #setup-brand-copy small,
        #setup-note,
        .step-label {
            display: none;
        }

        #setup-steps {
            min-width: 0;
            flex-direction: row;
            justify-content: flex-end;
            gap: 7px;
        }

        #setup-steps li {
            min-height: 0;
            display: block;
            padding: 0;
            border: 0;
            background: none;
        }
    }

    @media (max-width: 560px) {
        #setup-shell {
            width: 100vw;
            height: 100dvh;
            border: 0;
            border-radius: 0;
        }

        #setup-brand-copy {
            display: none;
        }

        #setup-sidebar {
            grid-template-columns: 42px minmax(0, 1fr);
            gap: 12px;
            padding-inline: 14px;
        }

        #setup-steps {
            gap: 5px;
        }

        .step-mark {
            width: 24px;
            height: 24px;
            border-radius: 8px;
        }
    }
</style>
