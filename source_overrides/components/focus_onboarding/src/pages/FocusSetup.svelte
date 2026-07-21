<!-- Copyright 2026 The Focus Browser Authors
     You can use, redistribute, and/or modify this source code under
     the terms of the GPL-3.0 license that can be found in the LICENSE file. -->

<script lang="ts">
    import { s } from "../lib/strings";
    import {
        currentPage,
        minimalInterface,
        quietNotifications,
        smoothAnimations,
    } from "../lib/onboarding-flow";

    import PageHeader from "../components/PageHeader.svelte";
    import IconLockStar from "../icons/tabler/IconLockStar.svelte";
    import IconCheck from "../icons/tabler/IconCheck.svelte";

    const visible = $derived($currentPage === "FocusSetup");
</script>

<div id="focus-page" class="onboarding-page" class:visible>
    <div id="focus-page-container" class="scrollable-page">
        <PageHeader
            title={s.focus.title}
            subtitle={s.focus.subtitle}
            Icon={IconLockStar}
        />

        <div id="focus-options" class="page-content">
            <button
                class="focus-option"
                class:selected={$quietNotifications}
                role="switch"
                aria-checked={$quietNotifications}
                onclick={() => $quietNotifications = !$quietNotifications}
            >
                <span class="option-check"><IconCheck /></span>
                <span class="option-copy">
                    <h4>{s.focus.quietTitle}</h4>
                    <p>{s.focus.quietDesc}</p>
                </span>
            </button>

            <button
                class="focus-option"
                class:selected={$minimalInterface}
                role="switch"
                aria-checked={$minimalInterface}
                onclick={() => $minimalInterface = !$minimalInterface}
            >
                <span class="option-check"><IconCheck /></span>
                <span class="option-copy">
                    <h4>{s.focus.minimalTitle}</h4>
                    <p>{s.focus.minimalDesc}</p>
                </span>
            </button>

            <button
                class="focus-option"
                class:selected={$smoothAnimations}
                role="switch"
                aria-checked={$smoothAnimations}
                onclick={() => $smoothAnimations = !$smoothAnimations}
            >
                <span class="option-check"><IconCheck /></span>
                <span class="option-copy">
                    <h4>{s.focus.motionTitle}</h4>
                    <p>{s.focus.motionDesc}</p>
                </span>
            </button>

            <p id="focus-note">{s.focus.note}</p>
        </div>
    </div>
</div>

<style>
    #focus-page {
        justify-content: flex-start;
        visibility: hidden;

        &.visible {
            visibility: visible;
            animation: page-in 0.3s 0.05s backwards;
        }

        &:not(.visible) {
            animation: page-out 0.2s forwards;
        }
    }

    #focus-page-container {
        width: 100%;
    }

    #focus-options {
        width: 100%;
        max-width: 760px;
        gap: 12px;
    }

    .focus-option {
        width: 100%;
        padding: 20px;
        display: grid;
        grid-template-columns: 38px 1fr;
        align-items: start;
        gap: 15px;
        text-align: left;
        border-radius: 18px;
        color: var(--primary);
        background: rgba(255, 255, 255, 0.035);
        border: 1px solid rgba(255, 255, 255, 0.08);
        box-shadow: none;

        &:hover {
            background: rgba(255, 255, 255, 0.06);
        }

        &.selected {
            background: rgba(255, 255, 255, 0.075);
            border-color: rgba(255, 255, 255, 0.34);
            box-shadow: 0 16px 45px rgba(0, 0, 0, 0.2);
        }
    }

    .option-check {
        width: 36px;
        height: 36px;
        display: grid;
        place-items: center;
        border-radius: 12px;
        color: transparent;
        background: rgba(255, 255, 255, 0.07);
        border: 1px solid rgba(255, 255, 255, 0.11);
        transition: 0.2s color, 0.2s background, 0.2s transform;
    }

    .selected .option-check {
        color: #09090b;
        background: #f4f4f5;
        transform: scale(1.03);
    }

    .option-check :global(svg) {
        width: 18px;
        height: 18px;
    }

    .option-copy {
        display: flex;
        flex-direction: column;
        gap: 7px;
    }

    .option-copy p {
        line-height: 1.45;
    }

    #focus-note {
        margin-top: 8px;
        color: #90909a;
        font-size: 13px;
        text-align: left;
    }
</style>
