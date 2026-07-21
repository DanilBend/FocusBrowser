<!-- Copyright 2026 The Focus Browser Authors
     You can use, redistribute, and/or modify this source code under
     the terms of the GPL-3.0 license that can be found in the LICENSE file. -->

<script lang="ts">
    import { SvelteSet } from "svelte/reactivity";
    import { s } from "../lib/strings";
    import { currentPage, selectedShortcuts } from "../lib/onboarding-flow";

    import PageHeader from "../components/PageHeader.svelte";
    import IconCheck from "../icons/tabler/IconCheck.svelte";
    import IconGridDots from "../icons/tabler/IconGridDots.svelte";

    import anthropicIcon from "../icons/brands/anthropic.svg?url";
    import githubIcon from "../icons/brands/github.svg?url";
    import githubCopilotIcon from "../icons/brands/githubcopilot.svg?url";
    import gmailIcon from "../icons/brands/gmail.svg?url";
    import googleCalendarIcon from "../icons/brands/googlecalendar.svg?url";
    import googleDriveIcon from "../icons/brands/googledrive.svg?url";
    import googleGeminiIcon from "../icons/brands/googlegemini.svg?url";
    import instagramIcon from "../icons/brands/instagram.svg?url";
    import notionIcon from "../icons/brands/notion.svg?url";
    import openAiIcon from "../icons/brands/openai.svg?url";
    import perplexityIcon from "../icons/brands/perplexity.svg?url";
    import stackOverflowIcon from "../icons/brands/stackoverflow.svg?url";
    import telegramIcon from "../icons/brands/telegram.svg?url";
    import tiktokIcon from "../icons/brands/tiktok.svg?url";
    import youtubeIcon from "../icons/brands/youtube.svg?url";

    const visible = $derived($currentPage === "ShortcutsSetup");
    const MAX_SHORTCUTS = 10;
    const copy = $derived({
        title: s.shortcuts.title,
        subtitle: s.shortcuts.subtitle,
        social: s.shortcuts.social,
        coding: s.shortcuts.coding,
        ai: s.shortcuts.ai,
        productivity: s.shortcuts.productivity,
        empty: s.shortcuts.empty,
        selected: (count: number) => `${s.shortcuts.selected} ${count} из ${MAX_SHORTCUTS}`,
        limit: s.shortcuts.limit,
    });

    type Shortcut = {
        id: string;
        name: string;
        icon: string;
    };

    type Category = {
        label: () => string;
        shortcuts: Shortcut[];
    };

    const categories: Category[] = [
        {
            label: () => copy.social,
            shortcuts: [
                { id: "youtube", name: "YouTube", icon: youtubeIcon },
                { id: "tiktok", name: "TikTok", icon: tiktokIcon },
                { id: "instagram", name: "Instagram", icon: instagramIcon },
                { id: "telegram", name: "Telegram", icon: telegramIcon },
            ],
        },
        {
            label: () => copy.coding,
            shortcuts: [
                { id: "codex", name: "Codex", icon: openAiIcon },
                { id: "claude", name: "Claude", icon: anthropicIcon },
                { id: "github", name: "GitHub", icon: githubIcon },
                { id: "stackoverflow", name: "Stack Overflow", icon: stackOverflowIcon },
            ],
        },
        {
            label: () => copy.ai,
            shortcuts: [
                { id: "chatgpt", name: "ChatGPT", icon: openAiIcon },
                { id: "gemini", name: "Gemini", icon: googleGeminiIcon },
                { id: "perplexity", name: "Perplexity", icon: perplexityIcon },
                { id: "copilot", name: "Copilot", icon: githubCopilotIcon },
            ],
        },
        {
            label: () => copy.productivity,
            shortcuts: [
                { id: "gmail", name: "Gmail", icon: gmailIcon },
                { id: "drive", name: "Google Drive", icon: googleDriveIcon },
                { id: "notion", name: "Notion", icon: notionIcon },
                { id: "calendar", name: "Calendar", icon: googleCalendarIcon },
            ],
        },
    ];

    let hitLimit = $state(false);
    let brokenIcons = $state(new SvelteSet<string>());

    const toggle = (id: string) => {
        if ($selectedShortcuts.has(id)) {
            $selectedShortcuts.delete(id);
            hitLimit = false;
            return;
        }

        if ($selectedShortcuts.size >= MAX_SHORTCUTS) {
            hitLimit = true;
            return;
        }

        $selectedShortcuts.add(id);
        hitLimit = false;
    };
</script>

<div id="shortcuts-page" class="onboarding-page" class:visible>
    <div id="shortcuts-container" class="scrollable-page">
        <PageHeader
            title={copy.title}
            subtitle={copy.subtitle}
            Icon={IconGridDots}
        />

        <div class="page-content shortcuts-content">
            <div class="categories">
                {#each categories as category}
                    <section>
                        <h3>{category.label()}</h3>
                        <div class="shortcut-grid">
                            {#each category.shortcuts as shortcut}
                                <button
                                    class="shortcut"
                                    class:selected={$selectedShortcuts.has(shortcut.id)}
                                    aria-pressed={$selectedShortcuts.has(shortcut.id)}
                                    onclick={() => toggle(shortcut.id)}
                                >
                                    <span class="mark" aria-hidden="true">
                                        {#if brokenIcons.has(shortcut.id)}
                                            <span class="logo-fallback">{shortcut.name.slice(0, 1)}</span>
                                        {:else}
                                            <img
                                                src={shortcut.icon}
                                                alt=""
                                                onerror={() => brokenIcons.add(shortcut.id)}
                                            />
                                        {/if}
                                    </span>
                                    <span class="name">{shortcut.name}</span>
                                    <span class="check"><IconCheck /></span>
                                </button>
                            {/each}
                        </div>
                    </section>
                {/each}
            </div>

            <p class:error={hitLimit} class="selection-status" aria-live="polite">
                {#if hitLimit}
                    {copy.limit}
                {:else if $selectedShortcuts.size === 0}
                    {copy.empty}
                {:else}
                    {copy.selected($selectedShortcuts.size)}
                {/if}
            </p>
        </div>
    </div>
</div>

<style>
    #shortcuts-page {
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

    #shortcuts-container {
        width: 100%;
        max-width: none;
        max-height: none;
        overflow-x: hidden;
        overflow-y: auto;
    }

    .shortcuts-content {
        width: 100%;
        gap: 16px;
    }

    .categories {
        width: 100%;
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 14px;
    }

    section {
        min-width: 0;
        display: flex;
        flex-direction: column;
        gap: 9px;
        padding: 14px;
        border-radius: 18px;
        background: rgba(255, 255, 255, 0.025);
        border: 1px solid rgba(255, 255, 255, 0.075);
    }

    h3 {
        color: #b8b8c0;
        font-size: 13px;
        font-weight: 650;
        letter-spacing: 0.035em;
        text-align: left;
    }

    .shortcut-grid {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 8px;
    }

    .shortcut {
        min-width: 0;
        min-height: 48px;
        padding: 8px 9px;
        display: grid;
        grid-template-columns: 30px minmax(0, 1fr) 18px;
        align-items: center;
        gap: 8px;
        color: #d4d4d8;
        text-align: left;
        background: rgba(255, 255, 255, 0.035);
        border: 1px solid rgba(255, 255, 255, 0.07);
        border-radius: 12px;
        box-shadow: none;

        &:hover {
            background: rgba(255, 255, 255, 0.065);
        }

        &.selected {
            color: #fff;
            background: rgba(255, 255, 255, 0.095);
            border-color: rgba(255, 255, 255, 0.42);
        }
    }

    .mark {
        width: 30px;
        height: 30px;
        display: grid;
        place-items: center;
        border-radius: 9px;
        background: #09090b;
        border: 1px solid rgba(255, 255, 255, 0.16);
    }

    .mark img {
        width: 19px;
        height: 19px;
        display: block;
        object-fit: contain;
    }

    .logo-fallback {
        color: #fff;
        font-size: 15px;
        font-weight: 700;
        line-height: 1;
    }

    .name {
        min-width: 0;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
        font-size: 12.5px;
        font-weight: 600;
    }

    .check {
        width: 18px;
        height: 18px;
        display: grid;
        place-items: center;
        border-radius: 6px;
        color: transparent;
        background: rgba(255, 255, 255, 0.06);
    }

    .selected .check {
        color: #09090b;
        background: #f4f4f5;
    }

    .check :global(svg) {
        width: 12px;
        height: 12px;
    }

    .selection-status {
        min-height: 20px;
        color: #a1a1aa;
        font-size: 13px;
        text-align: left;

        &.error {
            color: #fafafa;
        }
    }

    @media (max-width: 720px) {
        .categories {
            grid-template-columns: 1fr;
        }
    }

    @media (max-height: 760px) and (min-width: 721px) {
        .shortcuts-content {
            margin-top: 18px;
        }

        section {
            padding: 11px;
        }
    }
</style>
