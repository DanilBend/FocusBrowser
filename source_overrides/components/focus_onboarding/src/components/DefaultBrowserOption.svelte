<script lang="ts">
    import type { Writable } from "svelte/store";

    type Props = {
        title: string;
        desc: string;
        state: Writable<boolean>;
        stateOption: boolean;
        Icon: ConstructorOfATypedSvelteComponent;
    };

    const { title, desc, state, stateOption, Icon }: Props = $props();
</script>

<button
    class="big"
    class:selected={$state === stateOption}
    aria-pressed={$state === stateOption}
    onclick={() => {
        $state = stateOption;
    }}
>
    <div class="icon-container" aria-hidden="true">
        <Icon />
    </div>
    <div class="text">
        <h4>{title}</h4>
        <p>{desc}</p>
    </div>
</button>

<style>
    button {
        width: 100%;
        gap: 18px;
        max-width: none;
    }

    .text {
        min-width: 0;
        display: flex;
        flex-direction: column;
        gap: 5px;
        text-align: left;
    }

    .icon-container {
        display: flex;

        & > :global(svg) {
            width: 32px;
            height: 32px;
        }
    }
</style>
