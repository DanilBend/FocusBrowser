<script lang="ts">
    import { s } from "../lib/strings";
    import type { BrowserProfile } from "../lib/cr";
    import {
        selectedProfiles,
        previouslyImportedProfiles,
    } from "../lib/onboarding-flow";

    import CircleCheckbox from "./CircleCheckbox.svelte";

    type Props = {
        profile: BrowserProfile;
    };

    const { profile }: Props = $props();

    const selected = $derived($selectedProfiles.has(profile.index));
    const disabled = $derived($previouslyImportedProfiles.has(profile.index));

    const toggle = () => {
        if (!selected) {
            $selectedProfiles.add(profile.index);
        } else {
            $selectedProfiles.delete(profile.index);
        }
    };

    const dataList = () => {
        const options = [];

        if (profile.favorites) options.push(s.dataImport.bookmarks);
        if (profile.history) options.push(s.dataImport.history);
        if (profile.passwords) options.push(s.dataImport.passwords);
        if (profile.autofillFormData) options.push(s.dataImport.autofill);
        if (profile.search) options.push(s.dataImport.searchEngine);
        if (profile.extensions) options.push(s.dataImport.extensions);

        return options.join(", ");
    };
</script>

<button
    class="big"
    onclick={toggle}
    class:selected
    aria-pressed={selected}
    {disabled}
>
    <CircleCheckbox checked={selected || disabled} />
    <div class="text">
        <h4>{profile.profileName || s.dataImport.defaultProfile}</h4>
        <p>{dataList()}</p>
        {#if disabled}
            <span class="imported">{s.dataImport.imported}</span>
        {/if}
    </div>
</button>

<style>
    button.big {
        gap: 16px;
        width: 100%;
        max-width: none;
        border-radius: 19px;
        padding: 12px 18px;

        &:first-child:not(:only-child) {
            border-bottom-left-radius: 10px;
            border-bottom-right-radius: 10px;
        }

        &:last-child:not(:only-child) {
            border-top-left-radius: 10px;
            border-top-right-radius: 10px;
        }

        &:not(:only-child, :last-child, :first-child) {
            border-radius: 10px;
        }
    }

    .text {
        min-width: 0;
        display: flex;
        flex-direction: column;
        gap: 2px;
    }

    h4 {
        font-size: 15px;
        line-height: 1.25;
    }

    p {
        overflow: hidden;
        font-size: 13px;
        line-height: 1.4;
        text-overflow: ellipsis;
    }

    .imported {
        color: #a1a1aa;
        font-size: 12px;
    }
</style>
