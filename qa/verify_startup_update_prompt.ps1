# Copyright 2026 The Focus Browser Authors
# Isolated runtime smoke test for stale startup update metadata.
# A cached version must never be offered until the current signed feed has
# successfully rediscovered it. Positive prompt actions are source/model tested.

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateScript({ Test-Path -LiteralPath $_ -PathType Leaf })]
    [string]$BrowserPath,

    [ValidateRange(5, 120)]
    [int]$TimeoutSeconds = 25,

    [ValidateSet('ru', 'en-US')]
    [string]$Language = 'ru',

    [switch]$KeepProfileOnFailure
)

Set-StrictMode -Version 3.0
$ErrorActionPreference = 'Stop'

function Get-DescendantProcessIds([int]$RootProcessId) {
    $all = @(Get-CimInstance Win32_Process)
    $seen = [System.Collections.Generic.HashSet[int]]::new()
    $pending = [System.Collections.Generic.Queue[int]]::new()
    $pending.Enqueue($RootProcessId)
    while ($pending.Count -gt 0) {
        $parent = $pending.Dequeue()
        foreach ($child in @($all | Where-Object ParentProcessId -eq $parent)) {
            $childId = [int]$child.ProcessId
            if ($seen.Add($childId)) {
                $pending.Enqueue($childId)
            }
        }
    }
    return @($seen)
}

function Stop-ExactProcessTree([int]$RootProcessId) {
    $ids = @(Get-DescendantProcessIds $RootProcessId)
    [array]::Reverse($ids)
    foreach ($processId in @($ids + $RootProcessId)) {
        Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
    }
}

function Assert-TemporaryQaPath([string]$Path) {
    $resolved = [System.IO.Path]::GetFullPath($Path).TrimEnd('\')
    $temporary = [System.IO.Path]::GetFullPath(
        [System.IO.Path]::GetTempPath()).TrimEnd('\')
    if (-not $resolved.StartsWith(
            $temporary + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to use a QA path outside TEMP: $resolved"
    }
}

function ConvertFrom-UnicodeHex([string]$CodePoints) {
    return -join @(
        $CodePoints.Split(' ', [System.StringSplitOptions]::RemoveEmptyEntries) |
            ForEach-Object { [char][Convert]::ToInt32($_, 16) }
    )
}

function Get-JsonPropertyValue($Object, [string]$Name) {
    if ($null -eq $Object) {
        return $null
    }
    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property) {
        return $null
    }
    return $property.Value
}

function Read-UpdaterState([string]$LocalStatePath) {
    try {
        $state = Get-Content -LiteralPath $LocalStatePath -Raw |
            ConvertFrom-Json
        $focus = Get-JsonPropertyValue $state 'focus'
        $updater = Get-JsonPropertyValue $focus 'updater'
        return [pscustomobject]@{
            ReadSucceeded = $true
            AvailableVersion = [string](
                Get-JsonPropertyValue $updater 'available_version')
            SuppressedSession = [string](
                Get-JsonPropertyValue $updater 'suppressed_session')
        }
    } catch {
        # Chromium replaces Local State atomically. A read can briefly race that
        # replacement, so the observation loop retries instead of failing QA.
        return [pscustomobject]@{
            ReadSucceeded = $false
            AvailableVersion = $null
            SuppressedSession = $null
        }
    }
}

Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

$browser = (Resolve-Path -LiteralPath $BrowserPath).Path
$qaRoot = Join-Path ([System.IO.Path]::GetTempPath()) `
    ('FocusBrowser-StartupUpdate-QA-' + [guid]::NewGuid().ToString('N'))
$userData = Join-Path $qaRoot 'User Data'
$defaultProfile = Join-Path $userData 'Default'
$localStatePath = Join-Path $userData 'Local State'
$browserProcess = $null
$passed = $false
$evidence = [ordered]@{
    BrowserPath = $browser
    BrowserSha256 = (Get-FileHash -LiteralPath $browser -Algorithm SHA256).Hash
    UserDataDir = $userData
    SeededVersion = '9.9.9.9'
    Language = $Language
    RuntimeContract = 'RejectStaleCachedOfferBeforeCurrentFeedDiscovery'
    StartupWindowObserved = $false
    StalePromptDetected = $false
    CachedOfferRevalidated = $false
    CompletionReason = $null
    PromptWindow = $null
    Buttons = @()
    ObservedAvailableVersions = @()
    ElapsedMilliseconds = $null
    ObservedWindows = @{}
    BrowserProcessIds = @()
    BrowserLog = (Join-Path $qaRoot 'focus-browser.log')
}

Assert-TemporaryQaPath $qaRoot
New-Item -ItemType Directory -Path $defaultProfile -Force | Out-Null

$localState = @{
    focus = @{
        updater = @{
            available_version = '9.9.9.9'
            skipped_version = ''
            suppressed_session = ''
        }
    }
    profile = @{ last_used = 'Default' }
}
$preferences = @{
    browser = @{ has_seen_welcome_page = $true }
    distribution = @{ skip_first_run_ui = $true }
    focus = @{ services = @{ browser_updates = $true } }
    session = @{ restore_on_startup = 5 }
}

[System.IO.File]::WriteAllText(
    $localStatePath,
    ($localState | ConvertTo-Json -Depth 8 -Compress),
    [System.Text.UTF8Encoding]::new($false))
[System.IO.File]::WriteAllText(
    (Join-Path $defaultProfile 'Preferences'),
    ($preferences | ConvertTo-Json -Depth 8 -Compress),
    [System.Text.UTF8Encoding]::new($false))

$launchArguments = @(
    ('--user-data-dir="{0}"' -f $userData),
    '--no-first-run',
    '--no-default-browser-check',
    '--disable-session-crashed-bubble',
    '--enable-logging',
    "--log-file=$($evidence.BrowserLog)",
    '--v=1',
    "--lang=$Language",
    'about:blank'
)
$expectedUpdateButtons = @(
    (ConvertFrom-UnicodeHex `
        '041E 0431 043D 043E 0432 0438 0442 044C 0020 0441 0435 0439 0447 0430 0441'),
    'Update now'
)
$expectedLaterButtons = @(
    (ConvertFrom-UnicodeHex `
        '041D 0430 043F 043E 043C 043D 0438 0442 044C 0020 043F 043E 0437 0436 0435'),
    'Remind me later'
)
$expectedSkipButtons = @(
    (ConvertFrom-UnicodeHex `
        '041F 0440 043E 043F 0443 0441 0442 0438 0442 044C 0020 0432 0435 0440 0441 0438 044E'),
    'Skip this version'
)
$stopwatch = [System.Diagnostics.Stopwatch]::StartNew()

try {
    $browserProcess = Start-Process -FilePath $browser `
        -ArgumentList $launchArguments -PassThru

    $buttonCondition = [System.Windows.Automation.PropertyCondition]::new(
        [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
        [System.Windows.Automation.ControlType]::Button)
    $textCondition = [System.Windows.Automation.PropertyCondition]::new(
        [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
        [System.Windows.Automation.ControlType]::Text)
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)

    while ([DateTime]::UtcNow -lt $deadline) {
        $browserPids = @(
            Get-CimInstance Win32_Process |
                Where-Object {
                    $_.ExecutablePath -eq $browser -and
                    $_.CommandLine -like "*--user-data-dir=*$userData*"
                } |
                ForEach-Object { [int]$_.ProcessId }
        )
        if ($browserPids.Count -eq 0 -and $browserProcess.HasExited) {
            throw "Focus Browser exited during stale-cache observation"
        }
        $evidence.BrowserProcessIds = @($browserPids)
        $windows = [System.Windows.Automation.AutomationElement]::RootElement.FindAll(
            [System.Windows.Automation.TreeScope]::Children,
            [System.Windows.Automation.Condition]::TrueCondition)
        foreach ($window in $windows) {
            if ($browserPids -notcontains [int]$window.Current.ProcessId) {
                continue
            }
            $evidence.StartupWindowObserved = $true
            $buttons = $window.FindAll(
                [System.Windows.Automation.TreeScope]::Descendants,
                $buttonCondition)
            $buttonNames = @(
                foreach ($button in $buttons) {
                    if (-not [string]::IsNullOrWhiteSpace($button.Current.Name)) {
                        $button.Current.Name
                    }
                }
            )
            $texts = $window.FindAll(
                [System.Windows.Automation.TreeScope]::Descendants,
                $textCondition)
            $textNames = @(
                foreach ($textElement in $texts) {
                    if (-not [string]::IsNullOrWhiteSpace(
                            $textElement.Current.Name)) {
                        $textElement.Current.Name
                    }
                }
            )
            $windowKey = '{0}:{1}' -f $window.Current.ProcessId,
                $window.Current.NativeWindowHandle
            $evidence.ObservedWindows[$windowKey] = [ordered]@{
                Name = $window.Current.Name
                ControlType = $window.Current.ControlType.ProgrammaticName
                IsOffscreen = $window.Current.IsOffscreen
                Buttons = @($buttonNames)
                Text = @($textNames)
            }
            $hasUpdate = @($buttonNames | Where-Object {
                    $expectedUpdateButtons -contains $_ }).Count -gt 0
            $hasLater = @($buttonNames | Where-Object {
                    $expectedLaterButtons -contains $_ }).Count -gt 0
            $hasSkip = @($buttonNames | Where-Object {
                    $expectedSkipButtons -contains $_ }).Count -gt 0
            $visibleNames = @(
                @($window.Current.Name) + @($buttonNames) + @($textNames))
            $hasSeededVersion = @($visibleNames | Where-Object {
                    $_ -like "*$($evidence.SeededVersion)*"
                }).Count -gt 0
            if ($hasSeededVersion) {
                $evidence.StalePromptDetected = $true
                $evidence.PromptWindow = $window.Current.Name
                $evidence.Buttons = @($buttonNames)
                $evidence.ElapsedMilliseconds = $stopwatch.ElapsedMilliseconds
                Write-Output ($evidence | ConvertTo-Json -Depth 7)
                throw "Stale cached version $($evidence.SeededVersion) was offered"
            }
            if ($hasUpdate -and $hasLater -and $hasSkip) {
                # A prompt without the sentinel can only represent a version
                # discovered from the current feed, which is permitted.
                $evidence.PromptWindow = $window.Current.Name
                $evidence.Buttons = @($buttonNames)
                $evidence.ElapsedMilliseconds = $stopwatch.ElapsedMilliseconds
                $evidence.CompletionReason = 'CurrentFeedPromptObserved'
                $passed = $true
                break
            }
        }
        if ($passed) {
            break
        }

        $updaterState = Read-UpdaterState $localStatePath
        if ($updaterState.ReadSucceeded) {
            $available = [string]$updaterState.AvailableVersion
            if ($evidence.ObservedAvailableVersions -notcontains $available) {
                $evidence.ObservedAvailableVersions = @(
                    $evidence.ObservedAvailableVersions + $available)
            }
            if ($available -eq $evidence.SeededVersion -and
                -not [string]::IsNullOrEmpty(
                    $updaterState.SuppressedSession)) {
                # Showing the cached dialog records session suppression before
                # constructing its widget, so this also catches a short-lived
                # stale prompt missed between UI Automation samples.
                $evidence.StalePromptDetected = $true
                $evidence.ElapsedMilliseconds = $stopwatch.ElapsedMilliseconds
                Write-Output ($evidence | ConvertTo-Json -Depth 7)
                throw "Stale cached version was marked as offered"
            }
            if ($available -ne $evidence.SeededVersion) {
                $evidence.CachedOfferRevalidated = $true
                $evidence.CompletionReason = 'CachedSentinelRevalidated'
                $evidence.ElapsedMilliseconds = $stopwatch.ElapsedMilliseconds
                $passed = $true
                break
            }
        }
        Start-Sleep -Milliseconds 150
        $browserProcess.Refresh()
    }

    if (-not $passed) {
        if (-not $evidence.StartupWindowObserved) {
            Write-Output ($evidence | ConvertTo-Json -Depth 7)
            throw "No Focus Browser window appeared during runtime QA"
        }
        # Network discovery is intentionally not a prerequisite for this
        # negative security contract. A full quiet observation window with the
        # sentinel neither displayed nor session-suppressed is a valid pass.
        $evidence.CompletionReason = 'QuietObservationWindowCompleted'
        $evidence.ElapsedMilliseconds = $stopwatch.ElapsedMilliseconds
        $passed = $true
    }

    Write-Output ($evidence | ConvertTo-Json -Depth 7)
    Write-Host '[PASS] Stale cached update was not offered before feed discovery.' `
        -ForegroundColor Green
}
finally {
    $stopwatch.Stop()
    if ($null -ne $browserProcess) {
        Stop-ExactProcessTree $browserProcess.Id
    }
    Start-Sleep -Milliseconds 300
    Assert-TemporaryQaPath $qaRoot
    if ((Test-Path -LiteralPath $qaRoot) -and
        ($passed -or -not $KeepProfileOnFailure)) {
        Remove-Item -LiteralPath $qaRoot -Recurse -Force
    } elseif (Test-Path -LiteralPath $qaRoot) {
        Write-Warning "Retained failed QA profile: $qaRoot"
    }
}
