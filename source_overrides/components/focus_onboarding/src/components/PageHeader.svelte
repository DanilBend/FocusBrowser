<script lang="ts">
    type Props = {
        title: string;
        subtitle: string;
        noTopMargin?: boolean;
        verticalTitle?: boolean;
        Icon: ConstructorOfATypedSvelteComponent;
    };

    const { title, subtitle, noTopMargin, verticalTitle, Icon }: Props = $props();
</script>

<div class="page-header" class:no-top-margin={noTopMargin}>
    <div class="title" class:vertical={verticalTitle}>
        <Icon />
        <h2>{title}</h2>
    </div>
    <p>{subtitle}</p>
</div>

<style>
    .page-header {
        width: 100%;
        display: flex;
        flex-direction: column;
        gap: 9px;
        margin-top: 0;
        text-align: left;

        &.no-top-margin {
            margin-top: 0;
        }

        & p {
            max-width: 700px;
            color: #a7a7b0;
            font-size: 16px;
            line-height: 1.5;
        }

        & .title {
            display: flex;
            align-items: center;
            justify-content: flex-start;
            flex-wrap: nowrap;
            gap: 12px;

            & > :global(svg) {
                width: 30px;
                height: 30px;
                flex: 0 0 auto;
                stroke-width: 1.8px;
            }

            &.vertical {
                flex-direction: row;
            }

            & h2 {
                max-width: 100%;
                color: #fafafa;
                font-size: clamp(28px, 3vw, 34px);
                line-height: 1.12;
                text-wrap: pretty;
            }
        }
    }

    @media (max-width: 640px), (max-height: 720px) {
        .page-header {
            & p {
                font-size: 15px;
                line-height: 1.4;
            }
        }
    }

    @media (max-width: 520px) {
        .page-header .title {
            align-items: flex-start;

            & > :global(svg) {
                width: 26px;
                height: 26px;
            }
        }
    }
</style>
