# Copyright 2026 The Focus Browser Authors
# Read-only release verification for Windows artifacts, registration, and
# upgrade data preservation. This script never installs, launches, terminates,
# or makes Focus Browser the OS default browser.

[CmdletBinding()]
param(
    [ValidateSet('Artifacts', 'Registry', 'Snapshot', 'Compare', 'All')]
    [string]$Mode = 'Artifacts',

    [string]$BrowserPath,
    [string]$InstallerPath,
    [string]$ExpectedVersion = '1.0.0.0',

    [string]$UserDataPath =
        (Join-Path $env:LOCALAPPDATA 'FocusBrowser\Focus Browser\User Data'),
    [string]$SnapshotPath,
    [string]$BaselineSnapshot
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$script:failureCount = 0
$script:warningCount = 0
$focusQaRoot = Split-Path -Parent $PSScriptRoot
$focusOutDir = Join-Path $focusQaRoot 'build\src\out\Default'

function Write-Pass([string]$Message) {
    Write-Host "[PASS] $Message" -ForegroundColor Green
}

function Write-Warn([string]$Message) {
    $script:warningCount++
    Write-Host "[WARN] $Message" -ForegroundColor Yellow
}

function Write-Fail([string]$Message) {
    $script:failureCount++
    Write-Host "[FAIL] $Message" -ForegroundColor Red
}

function Assert-Focus([bool]$Condition, [string]$Message) {
    if ($Condition) {
        Write-Pass $Message
    } else {
        Write-Fail $Message
    }
}

function Resolve-ArtifactPaths {
    if ([string]::IsNullOrWhiteSpace($script:BrowserPath)) {
        $script:BrowserPath = Join-Path $focusOutDir 'chrome.exe'
    }

    if ([string]::IsNullOrWhiteSpace($script:InstallerPath)) {
        $candidateRoots = @(
            (Join-Path $focusQaRoot 'build'),
            [Environment]::GetFolderPath('Desktop')
        )
        $candidate = $candidateRoots |
            Where-Object { Test-Path -LiteralPath $_ -PathType Container } |
            ForEach-Object {
                Get-ChildItem -LiteralPath $_ -File -Filter '*FocusBrowser*1.0*installer.exe' |
                    Where-Object { $_.Name -notmatch 'mini-installer' }
            } |
            Sort-Object LastWriteTimeUtc -Descending |
            Select-Object -First 1
        if ($null -ne $candidate) {
            $script:InstallerPath = $candidate.FullName
        }
    }
}

function Test-MonochromeExecutableIcon([string]$Path) {
    try {
        Add-Type -AssemblyName System.Drawing
        $icon = [System.Drawing.Icon]::ExtractAssociatedIcon($Path)
        if ($null -eq $icon) {
            Write-Fail "No executable icon was found: $Path"
            return
        }

        $bitmap = $icon.ToBitmap()
        $visible = 0
        $chromatic = 0
        $light = 0
        $dark = 0
        for ($y = 0; $y -lt $bitmap.Height; $y++) {
            for ($x = 0; $x -lt $bitmap.Width; $x++) {
                $pixel = $bitmap.GetPixel($x, $y)
                if ($pixel.A -le 16) { continue }
                $visible++
                $maximum = [Math]::Max($pixel.R, [Math]::Max($pixel.G, $pixel.B))
                $minimum = [Math]::Min($pixel.R, [Math]::Min($pixel.G, $pixel.B))
                if (($maximum - $minimum) -gt 18) { $chromatic++ }
                $luma = ($pixel.R + $pixel.G + $pixel.B) / 3
                if ($luma -ge 210) { $light++ }
                if ($luma -le 45) { $dark++ }
            }
        }
        $bitmap.Dispose()
        $icon.Dispose()

        Assert-Focus ($visible -gt 0) 'Browser executable contains a visible icon'
        if ($visible -gt 0) {
            $chromaticRatio = $chromatic / $visible
            Assert-Focus ($chromaticRatio -le 0.02) `
                ('Browser executable icon is monochrome ({0:P2} chromatic pixels)' -f $chromaticRatio)
            Assert-Focus ($light -gt 0 -and $dark -gt 0) `
                'Browser executable icon contains both black and white artwork'
        }
    } catch {
        Write-Warn "Icon inspection was unavailable: $($_.Exception.Message)"
    }
}

function Test-PeArtifact(
    [string]$Path,
    [string]$Label,
    [bool]$RequireFocusVersion,
    [string]$ExpectedProductName = 'Focus Browser'
) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        Write-Fail "$Label is missing: $Path"
        return
    }

    $item = Get-Item -LiteralPath $Path
    Assert-Focus ($item.Length -gt 100KB) "$Label is non-empty ($($item.Length) bytes)"

    $versionInfo = [Diagnostics.FileVersionInfo]::GetVersionInfo($item.FullName)
    Assert-Focus ($versionInfo.ProductName -eq $ExpectedProductName) `
        "$Label ProductName is exactly $ExpectedProductName (actual: $($versionInfo.ProductName))"
    Assert-Focus ($versionInfo.FileDescription -match '^Focus Browser') `
        "$Label FileDescription is Focus Browser branded"
    if ($RequireFocusVersion) {
        Assert-Focus ($versionInfo.FileVersion -eq $ExpectedVersion) `
            "$Label FileVersion is exactly $ExpectedVersion (actual: $($versionInfo.FileVersion))"
        Assert-Focus ($versionInfo.ProductVersion -eq $ExpectedVersion) `
            "$Label ProductVersion is exactly $ExpectedVersion (actual: $($versionInfo.ProductVersion))"
    }

    $hash = Get-FileHash -LiteralPath $item.FullName -Algorithm SHA256
    Write-Host "[INFO] $Label SHA256 $($hash.Hash)"

    $signature = Get-AuthenticodeSignature -LiteralPath $item.FullName
    if ($signature.Status -eq 'Valid') {
        Write-Pass "$Label Authenticode signature is valid"
    } elseif ($signature.Status -eq 'NotSigned') {
        Write-Warn "$Label is unsigned; Windows may show Unknown publisher/SmartScreen"
    } else {
        Write-Fail "$Label has an invalid Authenticode state: $($signature.Status)"
    }
}

function Test-ArtifactRelease {
    Resolve-ArtifactPaths

    $installModesPath = Join-Path $focusQaRoot `
        'build\src\chrome\install_static\chromium_install_modes.h'
    if (Test-Path -LiteralPath $installModesPath -PathType Leaf) {
        $installModesText = Get-Content -LiteralPath $installModesPath -Raw
        $progIdPrefixDefinitions = @(
            [PSCustomObject]@{
                Label = 'browser'
                Match = [regex]::Match(
                    $installModesText,
                    '(?m)\.browser_prog_id_prefix\s*=\s*L"([^"]+)"'
                )
            },
            [PSCustomObject]@{
                Label = 'PDF'
                Match = [regex]::Match(
                    $installModesText,
                    '(?m)\.pdf_prog_id_prefix\s*=\s*L"([^"]+)"'
                )
            }
        )
        foreach ($definition in $progIdPrefixDefinitions) {
            if (-not $definition.Match.Success) {
                Write-Fail "Could not read the $($definition.Label) ProgID prefix from $installModesPath"
                continue
            }
            $prefix = $definition.Match.Groups[1].Value
            Assert-Focus ($prefix.Length -le 11) `
                "$($definition.Label) ProgID prefix is at most 11 characters (${prefix}: $($prefix.Length))"
            # Per-user registration adds a dot plus a 26-character base32 MD5
            # suffix. Windows permits at most 39 characters in this ProgID.
            $maximumUserProgIdLength = $prefix.Length + 27
            Assert-Focus ($maximumUserProgIdLength -le 39) `
                "$($definition.Label) user-specific ProgID is at most 39 characters ($maximumUserProgIdLength)"
        }
    } else {
        Write-Fail "Install modes source is missing: $installModesPath"
    }

    Test-PeArtifact $script:BrowserPath 'chrome.exe' $true
    Test-PeArtifact (Join-Path $focusOutDir 'chrome.dll') 'chrome.dll' $true
    Test-PeArtifact (Join-Path $focusOutDir 'setup.exe') 'setup.exe' $true `
        'Focus Browser Installer'
    Test-PeArtifact (Join-Path $focusOutDir 'mini_installer.exe') `
        'mini_installer.exe' $true 'Focus Browser Installer'
    if (Test-Path -LiteralPath $script:BrowserPath -PathType Leaf) {
        Test-MonochromeExecutableIcon $script:BrowserPath
    }

    if ([string]::IsNullOrWhiteSpace($script:InstallerPath)) {
        Write-Fail 'Focus Browser 1.0 NSIS installer was not found; pass -InstallerPath explicitly'
    } else {
        Test-PeArtifact $script:InstallerPath 'NSIS installer' $true
        if ((Split-Path -Leaf $script:InstallerPath) -match '1[._-]0') {
            Write-Pass 'Installer filename carries release version 1.0'
        } else {
            Write-Fail "Installer filename does not carry version 1.0: $script:InstallerPath"
        }
    }

    foreach ($required in @('setup.exe', 'focus_browser.packed.7z', 'mini_installer.exe')) {
        $requiredPath = Join-Path $focusOutDir $required
        Assert-Focus (Test-Path -LiteralPath $requiredPath -PathType Leaf) `
            "Packaging payload exists: $required"
    }

    $versionFile = Join-Path $focusQaRoot 'build\src\chrome\VERSION'
    if (Test-Path -LiteralPath $versionFile) {
        $versionText = Get-Content -LiteralPath $versionFile -Raw
        Assert-Focus ($versionText -match '(?m)^FOCUS_MAJOR=1\s*$') 'FOCUS_MAJOR is 1'
        Assert-Focus ($versionText -match '(?m)^FOCUS_MINOR=0\s*$') 'FOCUS_MINOR is 0'
        Assert-Focus ($versionText -match '(?m)^FOCUS_PATCH=0\s*$') 'FOCUS_PATCH is 0'
        Assert-Focus ($versionText -match '(?m)^FOCUS_PLATFORM=0\s*$') 'FOCUS_PLATFORM is 0'
    } else {
        Write-Fail "Version source is missing: $versionFile"
    }

    $componentVerifier = Join-Path $PSScriptRoot 'verify_focus_components.mjs'
    $nodeCommand = Get-Command node.exe -ErrorAction SilentlyContinue
    $bundledNode = Join-Path $focusQaRoot 'build\src\third_party\node\win\node.exe'
    $nodePath = if ($null -ne $nodeCommand) {
        $nodeCommand.Source
    } elseif (Test-Path -LiteralPath $bundledNode -PathType Leaf) {
        $bundledNode
    } else {
        $null
    }
    if ($null -ne $nodePath -and (Test-Path -LiteralPath $componentVerifier)) {
        & $nodePath $componentVerifier
        Assert-Focus ($LASTEXITCODE -eq 0) 'FocusBlock/FocusYoutube static integration verifier passed'
    } else {
        Write-Warn 'node.exe was not found; run verify_focus_components.mjs manually'
    }
}

function Get-CriticalProfileSnapshot([string]$Root) {
    $records = @()
    if (-not (Test-Path -LiteralPath $Root -PathType Container)) {
        return $records
    }

    $relativeFiles = [System.Collections.Generic.List[string]]::new()
    $relativeFiles.Add('Local State')
    $relativeFiles.Add('First Run')
    $profileNames = Get-ChildItem -LiteralPath $Root -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -eq 'Default' -or $_.Name -like 'Profile *' } |
        Select-Object -ExpandProperty Name
    $criticalNames = @(
        'Bookmarks', 'Bookmarks.bak', 'Preferences', 'Secure Preferences',
        'Login Data', 'Login Data For Account', 'History', 'Web Data',
        'Favicons', 'Cookies'
    )
    foreach ($profileName in $profileNames) {
        foreach ($criticalName in $criticalNames) {
            $relativeFiles.Add((Join-Path $profileName $criticalName))
        }
    }

    foreach ($relative in ($relativeFiles | Sort-Object -Unique)) {
        $absolute = Join-Path $Root $relative
        if (-not (Test-Path -LiteralPath $absolute -PathType Leaf)) { continue }
        try {
            $item = Get-Item -LiteralPath $absolute
            $hash = Get-FileHash -LiteralPath $absolute -Algorithm SHA256
            $records += [PSCustomObject]@{
                Path = $relative.Replace('\', '/')
                Length = $item.Length
                LastWriteTimeUtc = $item.LastWriteTimeUtc.ToString('o')
                Sha256 = $hash.Hash
            }
        } catch {
            Write-Warn "Could not snapshot $absolute (close Focus Browser first): $($_.Exception.Message)"
        }
    }
    return $records
}

function Save-ProfileSnapshot {
    if ([string]::IsNullOrWhiteSpace($SnapshotPath)) {
        Write-Fail 'Snapshot mode requires -SnapshotPath'
        return
    }
    if (-not (Test-Path -LiteralPath $UserDataPath -PathType Container)) {
        Write-Warn "No Focus Browser user-data directory exists yet: $UserDataPath"
    }
    $snapshot = [PSCustomObject]@{
        Schema = 1
        CreatedUtc = [DateTime]::UtcNow.ToString('o')
        UserDataPath = $UserDataPath
        Files = @(Get-CriticalProfileSnapshot $UserDataPath)
    }
    $parent = Split-Path -Parent $SnapshotPath
    if (-not [string]::IsNullOrWhiteSpace($parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    $snapshot | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $SnapshotPath -Encoding UTF8
    Write-Pass "Profile snapshot written outside the profile: $SnapshotPath"
    Write-Host "[INFO] Snapshot contains $($snapshot.Files.Count) critical files"
}

function Compare-ProfileSnapshot {
    if ([string]::IsNullOrWhiteSpace($BaselineSnapshot) -or
        -not (Test-Path -LiteralPath $BaselineSnapshot -PathType Leaf)) {
        Write-Fail 'Compare mode requires an existing -BaselineSnapshot'
        return
    }

    $before = Get-Content -LiteralPath $BaselineSnapshot -Raw | ConvertFrom-Json
    $afterRecords = @(Get-CriticalProfileSnapshot $UserDataPath)
    $beforeMap = @{}
    foreach ($record in $before.Files) { $beforeMap[$record.Path] = $record }
    $afterMap = @{}
    foreach ($record in $afterRecords) { $afterMap[$record.Path] = $record }

    $allPaths = @($beforeMap.Keys) + @($afterMap.Keys) | Sort-Object -Unique
    foreach ($relative in $allPaths) {
        if (-not $beforeMap.ContainsKey($relative)) {
            Write-Fail "Upgrade unexpectedly created critical profile file: $relative"
            continue
        }
        if (-not $afterMap.ContainsKey($relative)) {
            Write-Fail "Upgrade removed critical profile file: $relative"
            continue
        }
        if ($beforeMap[$relative].Sha256 -ne $afterMap[$relative].Sha256) {
            Write-Fail "Upgrade changed profile data while the browser was closed: $relative"
        } else {
            Write-Pass "Upgrade preserved: $relative"
        }
    }

    if (-not [string]::IsNullOrWhiteSpace($SnapshotPath)) {
        $after = [PSCustomObject]@{
            Schema = 1
            CreatedUtc = [DateTime]::UtcNow.ToString('o')
            UserDataPath = $UserDataPath
            Files = $afterRecords
        }
        $after | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $SnapshotPath -Encoding UTF8
        Write-Host "[INFO] Post-upgrade snapshot: $SnapshotPath"
    }
}

function Get-FocusRegistryKeys([string]$RootPath, [string]$Pattern) {
    if (-not (Test-Path -LiteralPath $RootPath)) { return @() }
    return @(Get-ChildItem -LiteralPath $RootPath -ErrorAction SilentlyContinue |
        Where-Object { $_.PSChildName -like $Pattern })
}

function Test-RegistryStringValue(
    [string]$KeyPath,
    [string]$ValueName,
    [string]$ExpectedValue,
    [string]$Message
) {
    if (-not (Test-Path -LiteralPath $KeyPath)) {
        Write-Fail "$Message (missing key: $KeyPath)"
        return
    }

    try {
        $key = Get-Item -LiteralPath $KeyPath -ErrorAction Stop
        $actualValue = $key.GetValue(
            $ValueName,
            $null,
            [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames
        )
    } catch {
        Write-Fail "$Message (could not read: $($_.Exception.Message))"
        return
    }

    Assert-Focus (
        $null -ne $actualValue -and
        [string]::Equals(
            [string]$actualValue,
            $ExpectedValue,
            [StringComparison]::OrdinalIgnoreCase
        )
    ) "$Message (actual: $actualValue)"
}

function Test-ProgIdOpenCommand(
    [string]$ClassesRoot,
    [string]$ProgId,
    [string]$ExpectedChromePath,
    [string]$Label
) {
    $commandPath = Join-Path (Join-Path $ClassesRoot $ProgId) `
        'shell\open\command'
    # GetCommandLineStringForShell() deliberately emits exactly this quoted
    # executable plus the safe one-argument placeholder. Reject stale paths and
    # extra/missing arguments rather than merely looking for "chrome.exe".
    $expectedCommand = '"' + $ExpectedChromePath + '" --single-argument %1'
    Test-RegistryStringValue $commandPath '' $expectedCommand `
        "$Label has the exact Focus Browser executable and safe shell arguments"
}

function Test-RegistryRegistration {
    $userChromePath = Join-Path $env:LOCALAPPDATA `
        'FocusBrowser\Focus Browser\Application\chrome.exe'
    $nativeProgramFiles = $env:ProgramW6432
    if ([string]::IsNullOrWhiteSpace($nativeProgramFiles)) {
        $nativeProgramFiles = [Environment]::GetFolderPath('ProgramFiles')
    }
    $systemChromePath = Join-Path $nativeProgramFiles `
        'FocusBrowser\Focus Browser\Application\chrome.exe'

    $registryRoots = @(
        [PSCustomObject]@{
            Hive = 'HKEY_CURRENT_USER'
            RegisteredApplications = 'Registry::HKEY_CURRENT_USER\Software\RegisteredApplications'
            StartMenuInternet = 'Registry::HKEY_CURRENT_USER\Software\Clients\StartMenuInternet'
            Classes = 'Registry::HKEY_CURRENT_USER\Software\Classes'
            ExpectedChromePath = $userChromePath
        },
        [PSCustomObject]@{
            Hive = 'HKEY_LOCAL_MACHINE'
            RegisteredApplications = 'Registry::HKEY_LOCAL_MACHINE\Software\RegisteredApplications'
            StartMenuInternet = 'Registry::HKEY_LOCAL_MACHINE\Software\Clients\StartMenuInternet'
            Classes = 'Registry::HKEY_LOCAL_MACHINE\Software\Classes'
            ExpectedChromePath = $systemChromePath
        }
    )

    $registered = @()
    foreach ($root in $registryRoots) {
        if (-not (Test-Path -LiteralPath $root.RegisteredApplications)) { continue }
        $properties = (Get-ItemProperty -LiteralPath $root.RegisteredApplications).PSObject.Properties |
            Where-Object {
                $_.Name -notlike 'PS*' -and
                ($_.Name -like 'Focus Browser*' -or [string]$_.Value -like '*Focus Browser*')
            }
        foreach ($property in $properties) {
            $registered += [PSCustomObject]@{
                Root = $root
                Name = [string]$property.Name
                CapabilitiesPath = [string]$property.Value
            }
        }
    }
    Assert-Focus ($registered.Count -gt 0) 'Focus Browser is present in RegisteredApplications'

    $clientKeys = @()
    foreach ($root in $registryRoots) {
        $clientKeys += Get-FocusRegistryKeys $root.StartMenuInternet 'Focus Browser*'
    }
    Assert-Focus ($clientKeys.Count -gt 0) 'Focus Browser StartMenuInternet registration exists'

    $progIdRecords = @()
    foreach ($root in $registryRoots) {
        foreach ($key in (Get-FocusRegistryKeys $root.Classes 'FocusHTM*')) {
            $progIdRecords += [PSCustomObject]@{
                Root = $root
                Kind = 'HTML'
                Prefix = 'FocusHTM'
                Name = $key.PSChildName
            }
        }
        foreach ($key in (Get-FocusRegistryKeys $root.Classes 'FocusPDF*')) {
            $progIdRecords += [PSCustomObject]@{
                Root = $root
                Kind = 'PDF'
                Prefix = 'FocusPDF'
                Name = $key.PSChildName
            }
        }
    }
    $htmlProgIds = @($progIdRecords | Where-Object { $_.Kind -eq 'HTML' })
    $pdfProgIds = @($progIdRecords | Where-Object { $_.Kind -eq 'PDF' })
    Assert-Focus ($htmlProgIds.Count -gt 0) 'FocusHTM browser ProgID registration exists'
    Assert-Focus ($pdfProgIds.Count -gt 0) 'FocusPDF ProgID registration exists'

    foreach ($record in $progIdRecords) {
        $escapedPrefix = [regex]::Escape($record.Prefix)
        Assert-Focus ($record.Name -match "^$escapedPrefix(?:\.[A-Z2-7]{26})?$") `
            "$($record.Kind) ProgID has a valid Focus prefix and optional user suffix ($($record.Name))"
        Assert-Focus ($record.Name.Length -le 39) `
            "$($record.Kind) ProgID is at most 39 characters ($($record.Name.Length))"
        Test-ProgIdOpenCommand $record.Root.Classes $record.Name `
            $record.Root.ExpectedChromePath `
            "$($record.Kind) ProgID $($record.Name)"
    }

    $fileAssociations = @(
        '.htm', '.html', '.mhtml', '.shtml', '.svg', '.xht', '.xhtml', '.webp'
    )
    $urlAssociations = @(
        'http', 'https', 'irc', 'mailto', 'mms', 'news', 'nntp', 'sms',
        'smsto', 'snews', 'tel', 'urn', 'webcal', 'focus'
    )
    foreach ($registration in $registered) {
        if ($registration.Name -notmatch '^Focus Browser(?<suffix>\.[A-Z2-7]{26})?$') {
            Write-Fail "RegisteredApplications has an invalid Focus Browser name: $($registration.Name)"
            continue
        }

        $suffix = $Matches['suffix']
        $expectedHtmlProgId = "FocusHTM$suffix"
        $expectedPdfProgId = "FocusPDF$suffix"
        $expectedCapabilities = "Software\Clients\StartMenuInternet\$($registration.Name)\Capabilities"
        Assert-Focus (
            [string]::Equals(
                $registration.CapabilitiesPath,
                $expectedCapabilities,
                [StringComparison]::OrdinalIgnoreCase
            )
        ) "RegisteredApplications points to the matching Focus Browser capabilities key"

        $capabilitiesKey = "Registry::$($registration.Root.Hive)\$expectedCapabilities"
        Assert-Focus (Test-Path -LiteralPath $capabilitiesKey) `
            "Capabilities key exists for $($registration.Name)"
        $fileAssociationsKey = Join-Path $capabilitiesKey 'FileAssociations'
        foreach ($association in $fileAssociations) {
            Test-RegistryStringValue $fileAssociationsKey $association `
                $expectedHtmlProgId `
                "$($registration.Name) maps $association to $expectedHtmlProgId"
        }
        Test-RegistryStringValue $fileAssociationsKey '.pdf' $expectedPdfProgId `
            "$($registration.Name) maps .pdf to $expectedPdfProgId"

        $urlAssociationsKey = Join-Path $capabilitiesKey 'URLAssociations'
        foreach ($association in $urlAssociations) {
            Test-RegistryStringValue $urlAssociationsKey $association `
                $expectedHtmlProgId `
                "$($registration.Name) maps $association to $expectedHtmlProgId"
        }
    }

    $uninstallEntries = @()
    foreach ($uninstallRoot in @(
        'Registry::HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Uninstall',
        'Registry::HKEY_LOCAL_MACHINE\Software\Microsoft\Windows\CurrentVersion\Uninstall',
        'Registry::HKEY_LOCAL_MACHINE\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall'
    )) {
        if (-not (Test-Path -LiteralPath $uninstallRoot)) { continue }
        $uninstallEntries += Get-ChildItem -LiteralPath $uninstallRoot -ErrorAction SilentlyContinue |
            ForEach-Object { Get-ItemProperty -LiteralPath $_.PSPath -ErrorAction SilentlyContinue } |
            Where-Object {
                $displayNameProperty = $_.PSObject.Properties['DisplayName']
                $null -ne $displayNameProperty -and
                    [string]$displayNameProperty.Value -like 'Focus Browser*'
            }
    }
    Assert-Focus ($uninstallEntries.Count -gt 0) 'Focus Browser uninstall registration exists'

    $currentDefault = $null
    $defaultPath = 'Registry::HKEY_CURRENT_USER\Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice'
    if (Test-Path -LiteralPath $defaultPath) {
        $currentDefault = (Get-ItemProperty -LiteralPath $defaultPath -ErrorAction SilentlyContinue).ProgId
    }
    Write-Host "[INFO] Current HTTPS default ProgID (not changed): $currentDefault"
}

switch ($Mode) {
    'Artifacts' { Test-ArtifactRelease }
    'Registry' { Test-RegistryRegistration }
    'Snapshot' { Save-ProfileSnapshot }
    'Compare' { Compare-ProfileSnapshot }
    'All' {
        Test-ArtifactRelease
        Test-RegistryRegistration
        if (-not [string]::IsNullOrWhiteSpace($BaselineSnapshot)) {
            Compare-ProfileSnapshot
        }
    }
}

Write-Host "[SUMMARY] failures=$script:failureCount warnings=$script:warningCount"
if ($script:failureCount -gt 0) { exit 1 }
exit 0
