<script lang="ts">
    import { s } from "../lib/strings";
    import Tooltip from "./Tooltip.svelte";
    import FocusLogo from "../icons/FocusLogo.svelte";
</script>

<div class="focus-partner">
    <Tooltip>
        {#snippet anchor()}
            <div class="focus-partner-badge">
                <FocusLogo />
                {s.partner.title}
            </div>
        {/snippet}
        {#snippet content()}
            {s.partner.tooltip}
        {/snippet}
    </Tooltip>
</div>

<style>
    .focus-partner {
        display: flex;
        position: relative;
        width: fit-content;
        cursor: help;
    }

    .focus-partner-badge {
        display: flex;
        align-items: center;
        gap: 4px;
        font-size: 13.5px;
        font-weight: 500;
        width: fit-content;
        line-height: 0;
        padding: 2px 0;

        & :global(svg) {
            width: 13px;
            height: 13px;
        }
    }
</style>
