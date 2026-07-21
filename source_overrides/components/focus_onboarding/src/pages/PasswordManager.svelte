<script lang="ts">
    import { s } from "../lib/strings";
    import { currentPage } from "../lib/onboarding-flow";

    import PageHeader from "../components/PageHeader.svelte";

    import IconCheck from "../icons/tabler/IconCheck.svelte";
    import IconKey from "../icons/tabler/IconKey.svelte";
    import IconLockStar from "../icons/tabler/IconLockStar.svelte";
    import IconTransferIn from "../icons/tabler/IconTransferIn.svelte";

    const visible = $derived($currentPage === "PasswordManager");

    const openManager = () => {
        window.open("chrome://password-manager/passwords", "_blank");
    };

    const openImport = () => {
        window.open("chrome://password-manager/settings", "_blank");
    };
</script>

<div id="password-manager-page" class="onboarding-page" class:visible>
    <div id="password-manager-page-container" class="scrollable-page">
        <PageHeader
            title={s.password.title}
            subtitle={s.password.subtitle}
            Icon={IconLockStar}
        />

        <div id="content" class="page-content password-content">
            <section class="manager-card">
                <div class="manager-mark" aria-hidden="true">
                    <IconKey />
                </div>

                <div class="manager-main">
                    <div class="feature-list">
                        <div class="feature">
                            <span class="check"><IconCheck /></span>
                            <div>
                                <h3>{s.password.localTitle}</h3>
                                <p>{s.password.localDesc}</p>
                            </div>
                        </div>
                        <div class="feature">
                            <span class="check"><IconCheck /></span>
                            <div>
                                <h3>{s.password.fillTitle}</h3>
                                <p>{s.password.fillDesc}</p>
                            </div>
                        </div>
                        <div class="feature">
                            <span class="check"><IconCheck /></span>
                            <div>
                                <h3>{s.password.passkeysTitle}</h3>
                                <p>{s.password.passkeysDesc}</p>
                            </div>
                        </div>
                    </div>

                    <div class="actions">
                        <button class="primary" onclick={openManager}>
                            <IconKey />
                            {s.password.openManager}
                        </button>
                        <button onclick={openImport}>
                            <IconTransferIn />
                            {s.password.openImport}
                        </button>
                    </div>
                </div>
            </section>

            <p class="note">{s.password.note}</p>
        </div>
    </div>
</div>

<style>
    #password-manager-page {
        justify-content: flex-start;
        visibility: hidden;

        &.visible {
            visibility: visible;
            animation: page-in 0.3s;
            animation-delay: 0.05s;
            animation-fill-mode: backwards;
        }

        &:not(.visible) {
            animation: page-out 0.2s;
            animation-fill-mode: forwards;
        }
    }

    #password-manager-page-container {
        width: 100%;
        max-width: none;
    }

    .password-content {
        width: 100%;
        gap: 14px;
    }

    .manager-card {
        width: 100%;
        box-sizing: border-box;
        display: grid;
        grid-template-columns: 76px minmax(0, 1fr);
        align-items: start;
        gap: 22px;
        padding: 22px;
        border-radius: 20px;
        text-align: left;
        background: rgba(255, 255, 255, 0.045);
        border: 1px solid rgba(255, 255, 255, 0.13);
        box-shadow: 0 22px 70px rgba(0, 0, 0, 0.28);
    }

    .manager-mark {
        width: 76px;
        height: 76px;
        display: grid;
        place-items: center;
        border-radius: 20px;
        color: #09090b;
        background: #f4f4f5;
    }

    .manager-mark :global(svg) {
        width: 40px;
        height: 40px;
        stroke-width: 1.6;
    }

    .manager-main {
        min-width: 0;
        display: flex;
        flex-direction: column;
        gap: 20px;
    }

    .feature-list {
        display: flex;
        flex-direction: column;
        gap: 18px;
    }

    .feature {
        display: grid;
        grid-template-columns: 22px 1fr;
        gap: 11px;
    }

    .feature h3 {
        color: #fafafa;
        font-size: 15.5px;
        line-height: 1.25;
    }

    .feature p,
    .note {
        color: #a1a1aa;
        font-size: 13.5px;
        line-height: 1.45;
    }

    .check {
        width: 20px;
        height: 20px;
        display: grid;
        place-items: center;
        border-radius: 7px;
        color: #09090b;
        background: #f4f4f5;
    }

    .check :global(svg) {
        width: 13px;
        height: 13px;
        stroke-width: 2.4;
    }

    .actions {
        display: flex;
        justify-content: flex-start;
        flex-wrap: wrap;
        gap: 10px;
        padding-top: 0;
    }

    .actions button {
        min-height: 44px;
        padding-inline: 17px;
        border-radius: 12px;
    }

    .actions :global(svg) {
        width: 17px;
        height: 17px;
    }

    .note {
        color: #90909a;
        text-align: left;
    }

    @media screen and (max-width: 620px) {
        .manager-card {
            grid-template-columns: 1fr;
            padding: 19px;
        }

        .manager-mark {
            width: 70px;
            height: 70px;
            margin-inline: auto;
            border-radius: 19px;
            margin-inline: 0;
        }

        .manager-mark :global(svg) {
            width: 38px;
            height: 38px;
        }

        .actions {
            flex-direction: column;
        }

        .actions button {
            width: 100%;
            justify-content: center;
        }
    }
</style>
