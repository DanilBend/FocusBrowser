<!-- Copyright 2026 The Focus Browser Authors
     You can use, redistribute, and/or modify this source code under
     the terms of the GPL-3.0 license that can be found in the LICENSE file. -->

<script lang="ts">
    import { s } from "../lib/strings";
    import {
        currentPage,
        locationBarStyle,
    } from "../lib/onboarding-flow";

    import PageHeader from "../components/PageHeader.svelte";
    import IconCheck from "../icons/tabler/IconCheck.svelte";
    import IconSearch from "../icons/tabler/IconSearch.svelte";

    const visible = $derived($currentPage === "AppearanceSetup");

    const barOptions = [
        {
            value: "full",
            label: () => s.appearance.barFull,
            desc: () => s.appearance.barFullDesc,
        },
        {
            value: "centered",
            label: () => s.appearance.barCentered,
            desc: () => s.appearance.barCenteredDesc,
        },
        {
            value: "minimal",
            label: () => s.appearance.barMinimal,
            desc: () => s.appearance.barMinimalDesc,
        },
    ] as const;
</script>

<div id="appearance-page" class="onboarding-page" class:visible>
    <div id="appearance-container" class="scrollable-page">
        <PageHeader
            title={s.appearance.title}
            subtitle={s.appearance.subtitle}
            Icon={IconSearch}
        />

        <div class="page-content appearance-content">
            <section>
                <h3>{s.appearance.barTitle}</h3>
                <div class="option-grid bar-grid">
                    {#each barOptions as option}
                        <button
                            class="appearance-option"
                            class:selected={$locationBarStyle === option.value}
                            aria-pressed={$locationBarStyle === option.value}
                            onclick={() => $locationBarStyle = option.value}
                        >
                            <span class="bar-preview {option.value}">
                                <i></i>
                            </span>
                            <span class="option-copy">
                                <strong>{option.label()}</strong>
                                <small>{option.desc()}</small>
                            </span>
                            <span class="check"><IconCheck /></span>
                        </button>
                    {/each}
                </div>
            </section>

            <p class="appearance-note">{s.appearance.note}</p>
        </div>
    </div>
</div>

<style>
    #appearance-page {
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

    #appearance-container {
        width: 100%;
    }

    .appearance-content {
        width: 100%;
        gap: 20px;
    }

    section {
        display: flex;
        flex-direction: column;
        gap: 13px;
        width: 100%;
    }

    h3 {
        color: #b8b8c0;
        font-size: 13px;
        font-weight: 600;
        letter-spacing: 0.04em;
        text-transform: uppercase;
    }

    .option-grid {
        display: grid;
        gap: 10px;
        width: 100%;
    }

    .bar-grid {
        grid-template-columns: repeat(3, minmax(0, 1fr));
    }

    .appearance-option {
        min-width: 0;
        min-height: 220px;
        padding: 15px;
        display: grid;
        grid-template-columns: 1fr auto;
        grid-template-rows: auto 1fr;
        align-items: end;
        gap: 14px 10px;
        text-align: left;
        color: #d4d4d8;
        background: rgba(255, 255, 255, 0.035);
        border: 1px solid rgba(255, 255, 255, 0.08);
        border-radius: 18px;
        box-shadow: none;

        &:hover {
            background: rgba(255, 255, 255, 0.065);
        }

        &.selected {
            color: #fff;
            background: rgba(255, 255, 255, 0.085);
            border-color: rgba(255, 255, 255, 0.4);
        }
    }

    .option-copy {
        min-width: 0;
        display: flex;
        flex-direction: column;
        gap: 5px;
    }

    .option-copy strong {
        color: #f4f4f5;
        font-size: 15px;
        font-weight: 650;
        line-height: 1.25;
    }

    .option-copy small {
        color: #92929c;
        font-size: 12.5px;
        line-height: 1.4;
    }

    .check {
        width: 20px;
        height: 20px;
        display: grid;
        place-items: center;
        border-radius: 7px;
        color: transparent;
        background: rgba(255, 255, 255, 0.06);
    }

    .selected .check {
        color: #09090b;
        background: #f4f4f5;
    }

    .check :global(svg) {
        width: 13px;
        height: 13px;
    }

    .bar-preview {
        grid-column: 1 / -1;
        height: 108px;
        position: relative;
        overflow: hidden;
        border-radius: 13px;
        background: #09090b;
        border: 1px solid rgba(255, 255, 255, 0.12);
    }

    .bar-preview i {
        position: absolute;
        display: block;
    }

    .bar-preview::before {
        content: "";
        position: absolute;
        inset: 10px 10px auto;
        height: 13px;
        border-radius: 6px;
        background: linear-gradient(90deg, #f4f4f5 0 22%, #52525b 22% 36%, transparent 36%);
        opacity: 0.9;
    }

    .bar-preview i {
        top: 39px;
        height: 31px;
        left: 12px;
        right: 12px;
        border-radius: 999px;
        background: #27272a;
        border: 1px solid rgba(255, 255, 255, 0.14);
    }

    .bar-preview::after {
        content: "";
        position: absolute;
        left: 10px;
        right: 10px;
        bottom: 10px;
        height: 20px;
        border-radius: 7px;
        background: #151518;
    }

    .bar-preview.centered i {
        left: 18%;
        right: 18%;
    }

    .bar-preview.minimal i {
        left: 29%;
        right: 29%;
        background: #18181b;
    }

    .appearance-note {
        color: #90909a;
        font-size: 13px;
        line-height: 1.45;
        text-align: left;
    }

    @media (max-width: 760px) {
        .bar-grid { grid-template-columns: 1fr; }

        .appearance-option {
            min-height: 190px;
        }
    }
</style>
