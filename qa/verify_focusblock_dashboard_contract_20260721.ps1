param(
    [string]$SourceRoot,
    [string]$ActiveRoot
)

$RepositoryRoot = Split-Path -Parent $PSScriptRoot
if (-not $SourceRoot) {
    $SourceRoot = Join-Path $RepositoryRoot 'source_overrides\third_party\ublock'
}
if (-not $ActiveRoot) {
    $ActiveRoot = Join-Path $RepositoryRoot 'build\src\third_party\ublock'
}

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$failures = [System.Collections.Generic.List[string]]::new()
$pages = @(
    'dashboard.html',
    'settings.html',
    '3p-filters.html',
    '1p-filters.html',
    'dyna-rules.html',
    'whitelist.html',
    'support.html',
    'about.html',
    'advanced-settings.html',
    'no-dashboard.html'
)

$expectedIds = @{
    'dashboard.html' = @('dashboard-nav', 'iframe', 'unsavedWarning')
    'settings.html' = @('export', 'import', 'localData', 'preset-accent-colors', 'reset', 'restoreFilePicker', 'settingsLastBackupPrompt', 'settingsLastRestorePrompt', 'storageUsed')
    '3p-filters.html' = @('actions', 'autoUpdate', 'buttonApply', 'buttonUpdate', 'cloudWidget', 'ignoreGenericCosmeticFilters', 'lists', 'listsOfBlockedHostsPrompt', 'parseCosmeticFilters', 'suspendUntilListsAreLoaded', 'templates')
    '1p-filters.html' = @('cloudWidget', 'enableMyFilters', 'exportUserFiltersToFile', 'importFilePicker', 'importUserFiltersFromFile', 'trustMyFilters', 'userFilters', 'userFiltersApply', 'userFiltersRevert')
    'dyna-rules.html' = @('cloudWidget', 'commitButton', 'diff', 'diffCollapse', 'editSaveButton', 'exportButton', 'importButton', 'importFilePicker', 'revertButton', 'ruleFilter', 'templates')
    'whitelist.html' = @('cloudWidget', 'exportWhitelistToFile', 'importFilePicker', 'importWhitelistFromFile', 'whitelist', 'whitelistApply', 'whitelistRevert')
    'support.html' = @('bugReport', 'filterReport', 'isNSFW', 'moreButton', 'selectAllButton', 'showSupportInfo', 'supportData')
    'about.html' = @('aboutNameVer')
    'advanced-settings.html' = @('advancedSettings', 'advancedSettingsApply')
    'no-dashboard.html' = @()
}

$localeKeys = @(
    'focusDashboardTagline',
    'focusDashboardBuiltIn',
    'focusDashboardNavigation',
    'focusDashboardEngineDocs',
    'focusDashboardStatus',
    'focusDashboardSettingsDescription',
    'focusDashboardFilterListsDescription',
    'focusDashboardMyFiltersDescription',
    'focusDashboardRulesDescription',
    'focusDashboardTrustedSitesDescription',
    'focusDashboardSupportDescription',
    'focusDashboardAboutDescription',
    'focusDashboardAdvancedDescription',
    'focusDashboardAboutIntro',
    'focusThemeAuto',
    'focusThemeLight',
    'focusThemeDark',
    'noDashboardNotice'
)

function Add-Failure([string]$Message) {
    $failures.Add($Message)
}

function Get-UniqueHtmlIds([string]$Text) {
    return @(
        [regex]::Matches($Text, '\bid\s*=\s*["'']([^"'']+)["'']') |
            ForEach-Object { $_.Groups[1].Value } |
            Sort-Object -Unique
    )
}

function Get-VisibleHtmlText([string]$Text) {
    $textOnly = [regex]::Replace($Text, '<!--[\s\S]*?-->', '')
    $textOnly = [regex]::Replace(
        $textOnly,
        '<(?:script|style)\b[^>]*>[\s\S]*?</(?:script|style)>',
        '',
        [System.Text.RegularExpressions.RegexOptions]::IgnoreCase
    )
    $textOnly = [regex]::Replace(
        $textOnly,
        '\b(?:href|src|data-url)\s*=\s*(?:"[^"]*"|''[^'']*'')',
        '',
        [System.Text.RegularExpressions.RegexOptions]::IgnoreCase
    )
    return [regex]::Replace($textOnly, '<[^>]+>', ' ')
}

function Get-RelativeLuminance([string]$HexColor) {
    $hex = $HexColor.TrimStart('#')
    $channels = @(0, 2, 4 | ForEach-Object {
        [Convert]::ToInt32($hex.Substring($_, 2), 16) / 255.0
    })
    $linear = @($channels | ForEach-Object {
        if ($_ -le 0.04045) {
            $_ / 12.92
        } else {
            [Math]::Pow(($_ + 0.055) / 1.055, 2.4)
        }
    })
    return 0.2126 * $linear[0] + 0.7152 * $linear[1] + 0.0722 * $linear[2]
}

function Get-ContrastRatio([string]$Foreground, [string]$Background) {
    $first = Get-RelativeLuminance $Foreground
    $second = Get-RelativeLuminance $Background
    $lighter = [Math]::Max($first, $second)
    $darker = [Math]::Min($first, $second)
    return ($lighter + 0.05) / ($darker + 0.05)
}

function Get-CssVariable([string]$Block, [string]$Name) {
    $match = [regex]::Match($Block, "--$([regex]::Escape($Name)):\s*(#[0-9a-fA-F]{6})")
    if ($match.Success) { return $match.Groups[1].Value }
    return ''
}

function Assert-Root([string]$Root) {
    if (-not (Test-Path -LiteralPath $Root -PathType Container)) {
        Add-Failure "Missing uBlock root: $Root"
        return
    }

    $focusCss = Join-Path $Root 'css\focus-dashboard.css'
    if (-not (Test-Path -LiteralPath $focusCss -PathType Leaf)) {
        Add-Failure "Missing Focus dashboard stylesheet: $focusCss"
    } elseif ((Get-Item -LiteralPath $focusCss).Length -lt 10000) {
        Add-Failure "Focus dashboard stylesheet is unexpectedly small: $focusCss"
    } else {
        $focusCssText = Get-Content -LiteralPath $focusCss -Raw -Encoding UTF8
        $lightBlock = [regex]::Match($focusCssText, '(?s):root\s*\{(?<body>.*?)\}').Groups['body'].Value
        $darkBlock = [regex]::Match($focusCssText, '(?s):root\.dark\s*\{(?<body>.*?)\}').Groups['body'].Value
        foreach ($theme in @(
            @{ Name = 'light'; Block = $lightBlock },
            @{ Name = 'dark'; Block = $darkBlock }
        )) {
            $canvas = Get-CssVariable $theme.Block 'focus-canvas'
            $surface = Get-CssVariable $theme.Block 'focus-surface'
            $subtle = Get-CssVariable $theme.Block 'focus-text-subtle'
            $border = Get-CssVariable $theme.Block 'focus-border-strong'
            if (
                $canvas -eq '' -or $surface -eq '' -or
                $subtle -eq '' -or $border -eq ''
            ) {
                Add-Failure "$focusCss has incomplete $($theme.Name) contrast tokens"
                continue
            }
            if ((Get-ContrastRatio $subtle $canvas) -lt 4.5) {
                Add-Failure "$focusCss $($theme.Name) subtle text is below WCAG AA contrast"
            }
            if ((Get-ContrastRatio $border $surface) -lt 3.0) {
                Add-Failure "$focusCss $($theme.Name) interactive border is below 3:1 contrast"
            }
        }
        if ($focusCssText -match 'left:\s*264px|top:\s*86px') {
            Add-Failure "$focusCss retains physical or hard-coded warning offsets"
        }
        if (
            $focusCssText -notmatch 'body\.focusEditorPane\s*\{[\s\S]*?overflow-y:\s*auto' -or
            $focusCssText -notmatch '#unsavedWarning\s*\{[\s\S]*?inset-inline:\s*0'
        ) {
            Add-Failure "$focusCss is missing zoom scrolling or logical warning positioning"
        }
    }

    $referencedLocaleKeys = [System.Collections.Generic.HashSet[string]]::new()

    foreach ($page in $pages) {
        $path = Join-Path $Root $page
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            Add-Failure "Missing page: $path"
            continue
        }

        $text = Get-Content -LiteralPath $path -Raw -Encoding UTF8
        foreach ($keyMatch in [regex]::Matches($text, 'data-i18n(?:-title)?="([^"]+)"')) {
            $null = $referencedLocaleKeys.Add($keyMatch.Groups[1].Value)
        }
        foreach ($keyMatch in [regex]::Matches($text, 'aria-label="([A-Za-z][A-Za-z0-9_]*)"')) {
            $null = $referencedLocaleKeys.Add($keyMatch.Groups[1].Value)
        }
        if ($text -notmatch '<html\b[^>]*\blang="ru"') {
            Add-Failure "$path must declare the Russian shell language before runtime localization"
        }
        foreach ($linkMatch in [regex]::Matches($text, '<link\b[^>]*\bhref="([^"]+)"')) {
            $href = $linkMatch.Groups[1].Value
            if ($href -match '^(?:https?:|data:|#)') {
                continue
            }
            $resourcePath = Join-Path $Root ($href -replace '/', '\')
            if (-not (Test-Path -LiteralPath $resourcePath -PathType Leaf)) {
                Add-Failure "$path references missing resource $href"
            }
        }
        $cssRefCount = [regex]::Matches($text, 'css/focus-dashboard\.css').Count
        if ($cssRefCount -ne 1) {
            Add-Failure "$path must reference css/focus-dashboard.css exactly once (found $cssRefCount)"
        }

        $actualIds = @(Get-UniqueHtmlIds $text)
        $wantedIds = @($expectedIds[$page] | Sort-Object -Unique)
        $idDiff = @(Compare-Object -ReferenceObject $wantedIds -DifferenceObject $actualIds)
        if ($idDiff.Count -ne 0) {
            Add-Failure "$path changed its functional ID contract: $($idDiff | Out-String)"
        }

        $duplicateIds = @(
            [regex]::Matches($text, '\bid\s*=\s*["'']([^"'']+)["'']') |
                ForEach-Object { $_.Groups[1].Value } |
                Group-Object |
                Where-Object Count -gt 1
        )
        if ($duplicateIds.Count -ne 0) {
            Add-Failure "$path contains duplicate IDs: $($duplicateIds.Name -join ', ')"
        }
    }

    $dashboardPath = Join-Path $Root 'dashboard.html'
    $dashboard = Get-Content -LiteralPath $dashboardPath -Raw -Encoding UTF8
    $tabMatches = @([regex]::Matches($dashboard, '<button\b(?=[^>]*\bclass="[^"]*\btabButton\b[^"]*")(?<attrs>[^>]*)>(?<inner>[\s\S]*?)</button>'))
    if ($tabMatches.Count -ne 8) {
        Add-Failure "$dashboardPath must contain 8 dashboard tab buttons (found $($tabMatches.Count))"
    }
    foreach ($match in $tabMatches) {
        if ($match.Groups['inner'].Value.Trim().Length -ne 0) {
            Add-Failure "$dashboardPath tab buttons must remain childless because dashboard.js reads event.target"
        }
        if ($match.Groups['attrs'].Value -notmatch '\bdata-pane="[^"]+"') {
            Add-Failure "$dashboardPath contains a tab button without data-pane"
        }
    }

    $dataPanes = @(
        $tabMatches | ForEach-Object {
            if ($_.Groups['attrs'].Value -match '\bdata-pane="([^"]+)"') { $Matches[1] }
        }
    )
    $expectedPanes = @('settings.html', '3p-filters.html', '1p-filters.html', 'dyna-rules.html', 'whitelist.html', 'support.html', 'about.html', 'no-dashboard.html')
    if (@(Compare-Object $expectedPanes $dataPanes).Count -ne 0) {
        Add-Failure "$dashboardPath changed data-pane routing"
    }
    if ($dashboard -notmatch 'class="wikilink"') {
        Add-Failure "$dashboardPath must preserve the .wikilink element expected by dashboard.js"
    }
    if (
        $dashboard -notmatch '<nav\b[^>]*id="dashboard-nav"[^>]*aria-label=' -or
        $dashboard -notmatch '<section\b[^>]*id="unsavedWarning"[^>]*role="dialog"[^>]*aria-modal="true"[^>]*tabindex="-1"' -or
        $dashboard -notmatch '<iframe\b[^>]*id="iframe"[^>]*title='
    ) {
        Add-Failure "$dashboardPath is missing dashboard, dialog or iframe accessibility semantics"
    }

    $filterLists = Get-Content -LiteralPath (Join-Path $Root '3p-filters.html') -Raw -Encoding UTF8
    if (
        $filterLists -notmatch 'class="fa-icon listExpander"[^>]*role="button"[^>]*tabindex="0"[^>]*aria-expanded=' -or
        $filterLists -notmatch '<input\b[^>]*type="search"[^>]*aria-label='
    ) {
        Add-Failure "$Root\3p-filters.html is missing keyboard disclosure or search semantics"
    }

    $rules = Get-Content -LiteralPath (Join-Path $Root 'dyna-rules.html') -Raw -Encoding UTF8
    if (
        $rules -notmatch '<input\b[^>]*type="search"[^>]*aria-label=' -or
        $rules -notmatch '<select\b[^>]*aria-label=' -or
        $rules -notmatch 'id="diffCollapse"[^>]*role="button"[^>]*tabindex="0"[^>]*aria-expanded='
    ) {
        Add-Failure "$Root\dyna-rules.html is missing filter or disclosure accessibility semantics"
    }

    $support = Get-Content -LiteralPath (Join-Path $Root 'support.html') -Raw -Encoding UTF8
    if ($support -notmatch 'id="showSupportInfo"[^>]*role="button"[^>]*tabindex="0"[^>]*aria-expanded=') {
        Add-Failure "$Root\support.html is missing keyboard disclosure semantics"
    }

    $settings = Get-Content -LiteralPath (Join-Path $Root 'settings.html') -Raw -Encoding UTF8
    if ($settings -notmatch '<input\b[^>]*type="color"[^>]*aria-label=') {
        Add-Failure "$Root\settings.html color input is missing an accessible name"
    }

    foreach ($locale in @('en', 'ru')) {
        $localePath = Join-Path $Root "_locales\$locale\messages.json"
        try {
            $messages = Get-Content -LiteralPath $localePath -Raw -Encoding UTF8 | ConvertFrom-Json
        } catch {
            Add-Failure "Invalid JSON in $localePath : $($_.Exception.Message)"
            continue
        }
        $properties = @($messages.PSObject.Properties.Name)
        foreach ($key in $localeKeys) {
            if ($properties -notcontains $key) {
                Add-Failure "$localePath is missing locale key $key"
            }
        }
        foreach ($key in $referencedLocaleKeys) {
            if ($properties -notcontains $key) {
                Add-Failure "$localePath is missing page-referenced locale key $key"
            }
        }
        $intro = $messages.focusDashboardAboutIntro.message
        if ($intro -notmatch 'uBlock Origin' -or $intro -notmatch 'GPL-3\.0') {
            Add-Failure "$localePath must retain uBlock Origin and GPL-3.0 attribution"
        }
    }

    $localeRoot = Join-Path $Root '_locales'
    foreach ($localeDir in Get-ChildItem -LiteralPath $localeRoot -Directory) {
        $localePath = Join-Path $localeDir.FullName 'messages.json'
        try {
            $messages = Get-Content -LiteralPath $localePath -Raw -Encoding UTF8 |
                ConvertFrom-Json
        } catch {
            Add-Failure "Invalid JSON in $localePath : $($_.Exception.Message)"
            continue
        }
        foreach ($property in $messages.PSObject.Properties) {
            $message = [string]$property.Value.message
            if ($message -match '(?i)my-ublock') {
                Add-Failure "$localePath keeps a stock export filename in $($property.Name)"
            }
            if (
                $property.Name -match '^(?:about|focusDashboardAboutIntro$)' -or
                $message.Length -eq 0
            ) {
                continue
            }
            $visibleMessage = [regex]::Replace(
                $message,
                '\b(?:href|data-url)\s*=\s*(?:"[^"]*"|''[^'']*'')',
                '',
                [System.Text.RegularExpressions.RegexOptions]::IgnoreCase
            )
            $visibleMessage = [regex]::Replace($visibleMessage, '<[^>]+>', ' ')
            if ($visibleMessage -match '(?i)(?:u|µ|μ)Block(?:\s+Origin|Origin|/wiki)?|(?:\buBO|[µμ]BO)\b') {
                Add-Failure "$localePath exposes stock branding in $($property.Name)"
            }
        }
        foreach ($exportKey in @(
            '1pExportFilename',
            'rulesDefaultFileName',
            'whitelistExportFilename',
            'aboutBackupFilename'
        )) {
            $exportName = [string]$messages.$exportKey.message
            if ($exportName -notmatch '^my-focusblock-.*\{\{datetime\}\}\.txt$') {
                Add-Failure "$localePath has an invalid FocusBlock export name in $exportKey"
            }
        }
    }

    foreach ($htmlPath in Get-ChildItem -LiteralPath $Root -Filter '*.html' -File -Recurse) {
        if ($htmlPath.Name -eq 'about.html') { continue }
        $html = Get-Content -LiteralPath $htmlPath.FullName -Raw -Encoding UTF8
        if ((Get-VisibleHtmlText $html) -match '(?i)(?:u|µ|μ)Block(?:\s+Origin|Origin)?|(?:\buBO|[µμ]BO)\b') {
            Add-Failure "$($htmlPath.FullName) exposes stock branding in visible HTML text"
        }
    }

    $managedStoragePath = Join-Path $Root 'managed_storage.json'
    $managedStorage = Get-Content -LiteralPath $managedStoragePath -Raw -Encoding UTF8
    if ($managedStorage -match '"title"\s*:\s*"[^"]*(?i:(?:u|µ|μ)Block|uBO|[µμ]BO)') {
        Add-Failure "$managedStoragePath exposes stock branding in a policy title"
    }

    $about = Get-Content -LiteralPath (Join-Path $Root 'about.html') -Raw -Encoding UTF8
    if ($about -notmatch 'github\.com/gorhill/uBlock' -or $about -notmatch 'Raymond Hill') {
        Add-Failure "$Root\about.html lost upstream source or copyright attribution"
    }

    $build = Get-Content -LiteralPath (Join-Path $Root 'BUILD.gn') -Raw -Encoding UTF8
    if ($build -notmatch '"css/focus-dashboard\.css"') {
        Add-Failure "$Root\BUILD.gn does not track the new recursive resource input"
    }

    $dashboardJsPath = Join-Path $Root 'js\dashboard.js'
    $dashboardJs = Get-Content -LiteralPath $dashboardJsPath -Raw -Encoding UTF8
    if ($dashboardJs -match 'wikilink\s*\|\|\s*''''') {
        Add-Failure "$dashboardJsPath still clears the documentation URL when a pane has no wikilink"
    }
    if (
        $dashboardJs -notmatch 'aria-current' -or
        $dashboardJs -notmatch 'modal\.setAttribute\(''aria-hidden''' -or
        $dashboardJs -notmatch 'ev\.key\s*===\s*''Escape''' -or
        $dashboardJs -notmatch 'ev\.key\s*!==\s*''Tab'''
    ) {
        Add-Failure "$dashboardJsPath is missing current-tab or modal focus behavior"
    }

    $filterListsJs = Get-Content -LiteralPath (Join-Path $Root 'js\3p-filters.js') -Raw -Encoding UTF8
    $rulesJs = Get-Content -LiteralPath (Join-Path $Root 'js\dyna-rules.js') -Raw -Encoding UTF8
    $supportJs = Get-Content -LiteralPath (Join-Path $Root 'js\support.js') -Raw -Encoding UTF8
    if (
        $filterListsJs -notmatch 'keydown[\s\S]*?\.listExpander' -and
        $filterListsJs -notmatch '\.listExpander[\s\S]*?keydown'
    ) {
        Add-Failure "$Root\js\3p-filters.js is missing keyboard list expansion"
    }
    if ($rulesJs -notmatch 'diffCollapse'',\s*''keydown') {
        Add-Failure "$Root\js\dyna-rules.js is missing keyboard collapse behavior"
    }
    if ($supportJs -notmatch 'showSupportInfo'',\s*''keydown') {
        Add-Failure "$Root\js\support.js is missing keyboard support-info behavior"
    }

    $i18nJsPath = Join-Path $Root 'js\i18n.js'
    $i18nJs = Get-Content -LiteralPath $i18nJsPath -Raw -Encoding UTF8
    if (
        $i18nJs -notmatch '@@ui_locale' -or
        $i18nJs -notmatch 'document\.documentElement\.lang'
    ) {
        Add-Failure "$i18nJsPath does not update html.lang for Russian, English and fallback locales"
    }
}

Assert-Root $SourceRoot
Assert-Root $ActiveRoot

$sourceFiles = @(
    Get-ChildItem -LiteralPath $SourceRoot -File -Recurse |
        ForEach-Object { $_.FullName.Substring($SourceRoot.Length).TrimStart('\') } |
        Sort-Object
)
$activeFiles = @(
    Get-ChildItem -LiteralPath $ActiveRoot -File -Recurse |
        ForEach-Object { $_.FullName.Substring($ActiveRoot.Length).TrimStart('\') } |
        Sort-Object
)
$treeDiff = @(Compare-Object -ReferenceObject $sourceFiles -DifferenceObject $activeFiles)
if ($treeDiff.Count -ne 0) {
    Add-Failure "Source/active resource lists differ: $($treeDiff | Out-String)"
}

foreach ($relativePath in $sourceFiles) {
    $sourcePath = Join-Path $SourceRoot $relativePath
    $activePath = Join-Path $ActiveRoot $relativePath
    if (-not (Test-Path -LiteralPath $sourcePath -PathType Leaf) -or -not (Test-Path -LiteralPath $activePath -PathType Leaf)) {
        continue
    }
    $sourceHash = (Get-FileHash -LiteralPath $sourcePath -Algorithm SHA256).Hash
    $activeHash = (Get-FileHash -LiteralPath $activePath -Algorithm SHA256).Hash
    if ($sourceHash -ne $activeHash) {
        Add-Failure "Mirror mismatch: $relativePath"
    }
}

if ($failures.Count -ne 0) {
    Write-Host "FocusBlock dashboard contract: FAIL ($($failures.Count))" -ForegroundColor Red
    $failures | ForEach-Object { Write-Host " - $_" -ForegroundColor Red }
    exit 1
}

Write-Host 'FocusBlock dashboard contract: PASS' -ForegroundColor Green
Write-Host "Validated $($pages.Count) pages, all locales, functional IDs, neutral branding, GPL attribution and full source/active parity."
