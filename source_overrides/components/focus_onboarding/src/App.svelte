<script lang="ts">
    import * as pages from "./pages";
    import * as Browser from "./lib/browser";
    import { currentPage, flow } from "./lib/onboarding-flow";
    import { setupMotionPreference } from "./lib/motion";

    import SetupShell from "./components/SetupShell.svelte";

    const OUT_ANIMATION_DURATION = 200;

    type Page = typeof flow[number];
    const WelcomePage = pages.Welcome;

    const initialMount = Object.fromEntries(
        flow.map(p => [p, false])
    ) as Record<Page, boolean>;

    Browser.setup();
    setupMotionPreference();

    let mounted = $state(initialMount);
    let prevPage: Page | null = null;

    $effect(() => {
        const page = $currentPage;
        mounted[page] = true;

        if (prevPage && prevPage !== page) {
            const toUnmount = prevPage;
            setTimeout(() => {
                if ($currentPage !== toUnmount) {
                    mounted[toUnmount] = false;
                }
            }, OUT_ANIMATION_DURATION);
        }

        prevPage = page;
    });
</script>

<main>
    {#if $currentPage === "Welcome"}
        {#if mounted.Welcome}
            <WelcomePage />
        {/if}
    {:else}
        <SetupShell>
            {#each flow as page (page)}
                {#if page !== "Welcome" && mounted[page]}
                    {@const Component = pages[page]}
                    <Component />
                {/if}
            {/each}
        </SetupShell>
    {/if}
</main>

<style>
    main {
        width: 100%;
        height: 100%;
        position: relative;
        display: grid;
        place-items: center;
    }
</style>
