# Copyright 2026 The Focus Browser Authors
# Destructive scope: this QA script may install/update Focus Browser only when
# -AllowInstall is supplied. It never reads, writes, launches, or terminates the
# user's real browser profile. All runtime work uses two unique disposable
# profiles under %TEMP%, and process cleanup is restricted to the exact
# installed executable plus the exact disposable --user-data-dir.

[CmdletBinding()]
param(
    [ValidateSet('Artifacts', 'InstallSmoke', 'All')]
    [string]$Mode = 'Artifacts',

    [string]$RepoRoot,
    [string]$InstallerPath,
    [string]$ExpectedVersion = '1.0.1.0',
    [string]$ExpectedDisplayVersion = '1.0.1',
    [ValidateRange(15, 180)]
    [int]$StartupTimeoutSeconds = 60,
    [string]$EvidenceDirectory,
    [switch]$AllowInstall
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
    $RepoRoot = Split-Path -Parent $PSScriptRoot
}

function Assert-Qa([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw "[FAIL] $Message" }
    Write-Host "[PASS] $Message" -ForegroundColor Green
}

function Get-NormalizedPath([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path)) { return '' }
    return [IO.Path]::GetFullPath($Path).TrimEnd('\')
}

function Test-SamePath([string]$Left, [string]$Right) {
    if ([string]::IsNullOrWhiteSpace($Left) -or
        [string]::IsNullOrWhiteSpace($Right)) {
        return $false
    }
    return [string]::Equals(
        (Get-NormalizedPath $Left),
        (Get-NormalizedPath $Right),
        [StringComparison]::OrdinalIgnoreCase)
}

function Assert-SafeQaTempRoot([string]$Path) {
    $tempRoot = (Get-NormalizedPath ([IO.Path]::GetTempPath())) + '\'
    $candidate = Get-NormalizedPath $Path
    $leaf = Split-Path -Leaf $candidate
    Assert-Qa (
        $candidate.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase) -and
        $leaf.StartsWith('FocusBrowser-QA-Installer-',
            [StringComparison]::Ordinal)
    ) "Disposable cleanup root is a unique Focus Browser QA child of TEMP: $candidate"
}

function Get-PeMachine([string]$Path) {
    $stream = [IO.File]::Open(
        $Path, [IO.FileMode]::Open, [IO.FileAccess]::Read,
        [IO.FileShare]::ReadWrite)
    try {
        $reader = [IO.BinaryReader]::new($stream)
        try {
            Assert-Qa ($reader.ReadUInt16() -eq 0x5A4D) "$Path has an MZ header"
            $stream.Position = 0x3C
            $peOffset = $reader.ReadInt32()
            Assert-Qa ($peOffset -gt 0 -and $peOffset -lt $stream.Length - 6) `
                "$Path has a valid PE header offset"
            $stream.Position = $peOffset
            Assert-Qa ($reader.ReadUInt32() -eq 0x00004550) "$Path has a PE signature"
            return $reader.ReadUInt16()
        } finally {
            $reader.Dispose()
        }
    } finally {
        $stream.Dispose()
    }
}

function Get-FileRecord([string]$Path, [string]$Label) {
    Assert-Qa (Test-Path -LiteralPath $Path -PathType Leaf) "$Label exists: $Path"
    $item = Get-Item -LiteralPath $Path
    Assert-Qa ($item.Length -gt 100KB) "$Label is non-empty ($($item.Length) bytes)"
    $hash = Get-FileHash -LiteralPath $item.FullName -Algorithm SHA256
    return [PSCustomObject]@{
        Label = $Label
        Path = $item.FullName
        Length = $item.Length
        Sha256 = $hash.Hash.ToLowerInvariant()
    }
}

function Test-PeBranding(
    [string]$Path,
    [string]$Label,
    [string]$ExpectedProductName,
    [UInt16]$ExpectedMachine = 0x8664,
    [string]$ExpectedMachineLabel = 'x64'
) {
    $info = [Diagnostics.FileVersionInfo]::GetVersionInfo($Path)
    Assert-Qa ($info.ProductName -eq $ExpectedProductName) `
        "$Label ProductName is exactly '$ExpectedProductName' (actual: '$($info.ProductName)')"
    Assert-Qa ($info.FileDescription -match '^Focus Browser') `
        "$Label FileDescription is Focus Browser branded"
    Assert-Qa ($info.FileVersion -eq $ExpectedVersion) `
        "$Label FileVersion is exactly $ExpectedVersion (actual: $($info.FileVersion))"
    Assert-Qa ($info.ProductVersion -eq $ExpectedVersion) `
        "$Label ProductVersion is exactly $ExpectedVersion (actual: $($info.ProductVersion))"
    Assert-Qa ((Get-PeMachine $Path) -eq $ExpectedMachine) `
        "$Label is an $ExpectedMachineLabel PE image"
}

function Invoke-ReleaseVerifier(
    [string]$VerifierMode,
    [string]$LogPath,
    [string[]]$AdditionalArguments
) {
    $verifier = Join-Path $script:RepoRootPath `
        'build_support\verify_focus_release.ps1'
    Assert-Qa (Test-Path -LiteralPath $verifier -PathType Leaf) `
        "Release verifier exists: $verifier"
    $powerShell = Join-Path $env:SystemRoot `
        'System32\WindowsPowerShell\v1.0\powershell.exe'
    $arguments = @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $verifier,
        '-Mode', $VerifierMode, '-ExpectedVersion', $ExpectedVersion
    ) + $AdditionalArguments
    & $powerShell @arguments 2>&1 | Tee-Object -LiteralPath $LogPath
    Assert-Qa ($LASTEXITCODE -eq 0) `
        "verify_focus_release.ps1 -Mode $VerifierMode passed"
}

function Get-CrashInventory {
    $records = @()
    $roots = @(
        [PSCustomObject]@{
            Path = (Join-Path $env:LOCALAPPDATA 'CrashDumps')
            Recurse = $false
        },
        [PSCustomObject]@{
            Path = [IO.Path]::GetTempPath()
            Recurse = $false
        },
        [PSCustomObject]@{
            Path = $script:QaTempRoot
            Recurse = $true
        }
    )
    foreach ($root in $roots) {
        if (-not (Test-Path -LiteralPath $root.Path -PathType Container)) { continue }
        $items = if ($root.Recurse) {
            Get-ChildItem -LiteralPath $root.Path -File -Recurse -ErrorAction SilentlyContinue
        } else {
            Get-ChildItem -LiteralPath $root.Path -File -ErrorAction SilentlyContinue
        }
        foreach ($item in $items) {
            if ($item.Extension -ine '.dmp') { continue }
            if ($item.Name -notmatch '^(chrome|setup|mini_installer|FocusBrowser).*[.]dmp$') {
                continue
            }
            $records += [PSCustomObject]@{
                Key = '{0}|{1}|{2}' -f $item.FullName.ToLowerInvariant(),
                    $item.Length, $item.LastWriteTimeUtc.Ticks
                Path = $item.FullName
                Length = $item.Length
                LastWriteTimeUtc = $item.LastWriteTimeUtc.ToString('o')
            }
        }
    }
    return @($records)
}

function Get-NsisPayloadInventory {
    $records = @()
    $temp = [IO.Path]::GetTempPath()
    foreach ($directory in (Get-ChildItem -LiteralPath $temp -Directory `
            -Filter 'ns*.tmp' -ErrorAction SilentlyContinue)) {
        $payload = Join-Path $directory.FullName 'focus_browser'
        if (Test-Path -LiteralPath $payload) {
            $records += [PSCustomObject]@{
                Key = $payload.ToLowerInvariant()
                Path = $payload
            }
        }
    }
    return @($records)
}

function Get-NewInventoryRecords($Before, $After) {
    $beforeKeys = @{}
    foreach ($record in @($Before)) { $beforeKeys[$record.Key] = $true }
    return @($After | Where-Object { -not $beforeKeys.ContainsKey($_.Key) })
}

function Write-Utf8NoBom([string]$Path, [string]$Text) {
    [IO.File]::WriteAllText($Path, $Text, [Text.UTF8Encoding]::new($false))
}

function Initialize-PreservationProfile([string]$ProfileRoot) {
    $default = Join-Path $ProfileRoot 'Default'
    New-Item -ItemType Directory -Path $default -Force | Out-Null
    $seed = [Guid]::NewGuid().ToString('N')
    Write-Utf8NoBom (Join-Path $ProfileRoot 'Local State') `
        ('{"focus_qa_seed":"' + $seed + '"}')
    Write-Utf8NoBom (Join-Path $ProfileRoot 'First Run') ''
    Write-Utf8NoBom (Join-Path $default 'Bookmarks') `
        ('{"checksum":"","roots":{},"version":1,"focus_qa_seed":"' + $seed + '"}')
    Write-Utf8NoBom (Join-Path $default 'Preferences') `
        ('{"focus_qa_seed":"' + $seed + '"}')
    Write-Utf8NoBom (Join-Path $default 'Secure Preferences') `
        ('{"focus_qa_seed":"' + $seed + '"}')
    foreach ($databaseName in @(
        'Login Data', 'Login Data For Account', 'History', 'Web Data',
        'Favicons', 'Cookies'
    )) {
        Write-Utf8NoBom (Join-Path $default $databaseName) `
            ("FOCUS_QA_DISPOSABLE_DATABASE_SENTINEL:$databaseName`n$seed")
    }
}

function Get-InstalledFocusProcesses([string]$ExecutablePath) {
    $records = @()
    foreach ($process in (Get-CimInstance Win32_Process `
            -Filter "Name='chrome.exe'" -ErrorAction SilentlyContinue)) {
        if (Test-SamePath ([string]$process.ExecutablePath) $ExecutablePath) {
            $records += $process
        }
    }
    return @($records)
}

function Get-ProcessTreeIds([int]$RootPid) {
    $all = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
    $childrenByParent = @{}
    foreach ($record in $all) {
        $parent = [int]$record.ParentProcessId
        if (-not $childrenByParent.ContainsKey($parent)) {
            $childrenByParent[$parent] = [System.Collections.Generic.List[int]]::new()
        }
        $childrenByParent[$parent].Add([int]$record.ProcessId)
    }
    $result = [System.Collections.Generic.HashSet[int]]::new()
    $queue = [System.Collections.Generic.Queue[int]]::new()
    $queue.Enqueue($RootPid)
    while ($queue.Count -gt 0) {
        $current = $queue.Dequeue()
        if (-not $result.Add($current)) { continue }
        if ($childrenByParent.ContainsKey($current)) {
            foreach ($child in $childrenByParent[$current]) { $queue.Enqueue($child) }
        }
    }
    return @($result)
}

function Get-OwnedRuntimeProcesses(
    [int]$RootPid,
    [string]$ExecutablePath,
    [string]$RuntimeProfile
) {
    $rootRecord = Get-CimInstance Win32_Process `
        -Filter "ProcessId=$RootPid" -ErrorAction SilentlyContinue
    $rootIsOwned = $null -ne $rootRecord -and
        (Test-SamePath ([string]$rootRecord.ExecutablePath) $ExecutablePath) -and
        ([string]$rootRecord.CommandLine).IndexOf(
            $RuntimeProfile, [StringComparison]::OrdinalIgnoreCase) -ge 0
    $treeIds = if ($rootIsOwned) { @(Get-ProcessTreeIds $RootPid) } else { @() }
    $treeMap = @{}
    foreach ($pidValue in $treeIds) { $treeMap[[int]$pidValue] = $true }
    $owned = @()
    foreach ($record in (Get-CimInstance Win32_Process `
            -Filter "Name='chrome.exe'" -ErrorAction SilentlyContinue)) {
        if (-not (Test-SamePath ([string]$record.ExecutablePath) $ExecutablePath)) {
            continue
        }
        $commandLine = [string]$record.CommandLine
        $hasExactProfile = $commandLine.IndexOf(
            $RuntimeProfile, [StringComparison]::OrdinalIgnoreCase) -ge 0
        if ($treeMap.ContainsKey([int]$record.ProcessId) -or $hasExactProfile) {
            $owned += $record
        }
    }
    return @($owned)
}

function Stop-OwnedRuntimeProcesses(
    [int]$RootPid,
    [string]$ExecutablePath,
    [string]$RuntimeProfile
) {
    $owned = @(Get-OwnedRuntimeProcesses $RootPid $ExecutablePath $RuntimeProfile)
    if ($owned.Count -eq 0) { return }

    # A headed run can exit cleanly through WM_CLOSE; headless runs fall back
    # to exact-PID termination below. No process is selected by image name.
    $root = Get-Process -Id $RootPid -ErrorAction SilentlyContinue
    if ($null -ne $root -and $root.MainWindowHandle -ne 0) {
        $null = $root.CloseMainWindow()
        $null = $root.WaitForExit(5000)
    }

    $owned = @(Get-OwnedRuntimeProcesses $RootPid $ExecutablePath $RuntimeProfile)
    $capturedOwned = @($owned)
    foreach ($record in ($capturedOwned | Sort-Object `
            @{ Expression = { if ([int]$_.ProcessId -eq $RootPid) { 1 } else { 0 } }; Descending = $false }, `
            @{ Expression = 'ProcessId'; Descending = $true })) {
        $fresh = Get-CimInstance Win32_Process `
            -Filter "ProcessId=$($record.ProcessId)" -ErrorAction SilentlyContinue
        if ($null -eq $fresh) { continue }
        $isExactExecutable = Test-SamePath ([string]$fresh.ExecutablePath) $ExecutablePath
        $sameProcessIncarnation = [string]$fresh.CreationDate -ceq `
            [string]$record.CreationDate
        if ($isExactExecutable -and $sameProcessIncarnation) {
            Stop-Process -Id ([int]$fresh.ProcessId) -Force -ErrorAction SilentlyContinue
        }
    }

    $deadline = [DateTime]::UtcNow.AddSeconds(10)
    do {
        $remaining = @()
        foreach ($record in $capturedOwned) {
            $fresh = Get-CimInstance Win32_Process `
                -Filter "ProcessId=$($record.ProcessId)" -ErrorAction SilentlyContinue
            if ($null -eq $fresh) { continue }
            $sameExecutable = Test-SamePath `
                ([string]$fresh.ExecutablePath) $ExecutablePath
            $sameProcessIncarnation = [string]$fresh.CreationDate -ceq `
                [string]$record.CreationDate
            if ($sameExecutable -and $sameProcessIncarnation) {
                $remaining += $fresh
                Stop-Process -Id ([int]$fresh.ProcessId) -Force `
                    -ErrorAction SilentlyContinue
            }
        }
        if ($remaining.Count -eq 0) { break }
        Start-Sleep -Milliseconds 250
    } while ([DateTime]::UtcNow -lt $deadline)
    Assert-Qa ($remaining.Count -eq 0) `
        'Every captured disposable browser PID exited without targeting another process incarnation'
}

function Invoke-ArtifactChecks {
    $expectedInstallerName = "FocusBrowser_${ExpectedDisplayVersion}_x64-installer.exe"
    $expectedMiniName = "FocusBrowser_${ExpectedDisplayVersion}_x64-mini-installer.exe"
    $expectedZipName = "FocusBrowser_${ExpectedDisplayVersion}_x64-windows.zip"
    Assert-Qa ((Split-Path -Leaf $script:InstallerPathResolved) -ceq $expectedInstallerName) `
        "NSIS filename is exactly $expectedInstallerName"

    $browser = Join-Path $script:OutDir 'chrome.exe'
    $chromeDll = Join-Path $script:OutDir 'chrome.dll'
    $setup = Join-Path $script:OutDir 'setup.exe'
    $miniOut = Join-Path $script:OutDir 'mini_installer.exe'
    $packedArchive = Join-Path $script:OutDir 'focus_browser.packed.7z'
    $packagedMini = Join-Path $script:BuildDir $expectedMiniName
    $portableZip = Join-Path $script:BuildDir $expectedZipName

    Invoke-ReleaseVerifier 'Artifacts' `
        (Join-Path $script:EvidenceDir 'artifact-verifier.log') @(
            '-BrowserPath', $browser,
            '-InstallerPath', $script:InstallerPathResolved
        )

    $records = @(
        (Get-FileRecord $browser 'built chrome.exe'),
        (Get-FileRecord $chromeDll 'built chrome.dll'),
        (Get-FileRecord $setup 'setup.exe'),
        (Get-FileRecord $miniOut 'mini_installer.exe'),
        (Get-FileRecord $packedArchive 'focus_browser.packed.7z'),
        (Get-FileRecord $script:InstallerPathResolved 'NSIS installer'),
        (Get-FileRecord $packagedMini 'packaged mini-installer'),
        (Get-FileRecord $portableZip 'portable zip')
    )
    Test-PeBranding $browser 'built chrome.exe' 'Focus Browser'
    Test-PeBranding $chromeDll 'built chrome.dll' 'Focus Browser'
    Test-PeBranding $setup 'setup.exe' 'Focus Browser Installer'
    Test-PeBranding $miniOut 'mini_installer.exe' 'Focus Browser Installer'
    # The bundled NSIS compiler emits an x86 bootstrap executable even though
    # its browser payload is x64. Validate both parts according to their real
    # architectures instead of rejecting a valid 64-bit browser package.
    Test-PeBranding $script:InstallerPathResolved 'NSIS installer' `
        'Focus Browser' 0x014c 'x86 bootstrap'

    $miniOutHash = ($records | Where-Object Label -eq 'mini_installer.exe').Sha256
    $packagedMiniHash = ($records | Where-Object Label -eq 'packaged mini-installer').Sha256
    Assert-Qa ($miniOutHash -ceq $packagedMiniHash) `
        'Packaged mini-installer is byte-for-byte identical to the built updater payload'

    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [IO.Compression.ZipFile]::OpenRead($portableZip)
    try {
        $entryNames = @($archive.Entries | ForEach-Object FullName)
        Assert-Qa (@($entryNames | Where-Object {
            $_ -match '(^|/)chrome[.]exe$'
        }).Count -eq 1) `
            'Portable zip contains exactly one chrome.exe'
    } finally {
        $archive.Dispose()
    }

    $hashManifest = Join-Path $script:EvidenceDir 'SHA256SUMS-installer-smoke.txt'
    $manifestLines = foreach ($record in $records) {
        '{0}  {1}' -f $record.Sha256, (Split-Path -Leaf $record.Path)
    }
    Write-Utf8NoBom $hashManifest (($manifestLines -join "`n") + "`n")
    $records | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath `
        (Join-Path $script:EvidenceDir 'artifact-manifest.json') -Encoding UTF8
    $script:Result.ArtifactHashes = @($records)
}

function Test-UninstallMetadata([string]$InstalledChrome) {
    $uninstallRoot = 'Registry::HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Uninstall'
    $matches = @()
    if (Test-Path -LiteralPath $uninstallRoot) {
        foreach ($key in (Get-ChildItem -LiteralPath $uninstallRoot)) {
            $value = Get-ItemProperty -LiteralPath $key.PSPath -ErrorAction SilentlyContinue
            $displayNameProperty = if ($null -ne $value) {
                $value.PSObject.Properties['DisplayName']
            } else {
                $null
            }
            if ($null -ne $displayNameProperty -and
                [string]$displayNameProperty.Value -eq 'Focus Browser') {
                $matches += $value
            }
        }
    }
    Assert-Qa ($matches.Count -eq 1) `
        'Exactly one current-user Focus Browser uninstall registration exists'
    $entry = $matches[0]
    Assert-Qa ($entry.DisplayVersion -eq $ExpectedVersion) `
        "Uninstall DisplayVersion is exactly $ExpectedVersion"
    $publisher = [string]$entry.Publisher
    Assert-Qa (
        -not [string]::IsNullOrWhiteSpace($publisher) -and
        $publisher.IndexOf('Focus Browser',
            [StringComparison]::OrdinalIgnoreCase) -ge 0 -and
        $publisher -notmatch '(?i)chromium|google chrome'
    ) "Uninstall publisher is Focus Browser branded (actual: $publisher)"
    Assert-Qa (Test-SamePath $entry.InstallLocation (Split-Path -Parent $InstalledChrome)) `
        'Uninstall InstallLocation points to the installed Application directory'
    Assert-Qa (([string]$entry.DisplayIcon).StartsWith(
        $InstalledChrome, [StringComparison]::OrdinalIgnoreCase)) `
        'Uninstall DisplayIcon points to the installed Focus Browser executable'
    $expectedUninstaller = Join-Path (Split-Path -Parent $InstalledChrome) `
        "$ExpectedVersion\Installer\setup.exe"
    $expectedUninstallCommand = `
        '"' + $expectedUninstaller + '" --uninstall --verbose-logging'
    Assert-Qa (([string]$entry.UninstallString) -ceq $expectedUninstallCommand) `
        'Uninstall command uses the exact versioned Focus Browser setup.exe with safe arguments'
}

function Invoke-SilentInstall([int]$Pass, [string]$LogPath) {
    $arguments = @(
        '/S',
        '/VERBOSE-LOGGING',
        ('/LOG-FILE="' + $LogPath + '"')
    )
    $process = Start-Process -FilePath $script:InstallerPathResolved `
        -ArgumentList $arguments -Wait -PassThru
    $script:Result.InstallerExitCodes += [int]$process.ExitCode
    Assert-Qa ($process.ExitCode -eq 0) `
        "Silent installer pass $Pass returned normalized exit code 0"
}

function Invoke-InstalledRuntimeSmoke(
    [string]$InstalledChrome,
    [string]$RuntimeProfile,
    [string]$RuntimeLog
) {
    New-Item -ItemType Directory -Path $RuntimeProfile -Force | Out-Null
    $arguments = @(
        ('--user-data-dir="' + $RuntimeProfile + '"'),
        '--headless=new',
        '--no-first-run',
        '--no-default-browser-check',
        '--remote-debugging-port=0',
        '--enable-logging',
        ('--log-file="' + $RuntimeLog + '"'),
        'about:blank'
    )
    $process = Start-Process -FilePath $InstalledChrome `
        -ArgumentList $arguments -PassThru
    $script:RuntimeRootPid = [int]$process.Id
    try {
        $devToolsPortFile = Join-Path $RuntimeProfile 'DevToolsActivePort'
        $deadline = [DateTime]::UtcNow.AddSeconds($StartupTimeoutSeconds)
        while ([DateTime]::UtcNow -lt $deadline) {
            if ($process.HasExited) {
                throw "Installed Focus Browser exited during startup with code $($process.ExitCode)"
            }
            if (Test-Path -LiteralPath $devToolsPortFile -PathType Leaf) { break }
            Start-Sleep -Milliseconds 250
            $process.Refresh()
        }
        Assert-Qa (Test-Path -LiteralPath $devToolsPortFile -PathType Leaf) `
            'Installed browser created DevToolsActivePort in the disposable runtime profile'
        $portLine = (Get-Content -LiteralPath $devToolsPortFile -TotalCount 1).Trim()
        $port = 0
        Assert-Qa ([int]::TryParse($portLine, [ref]$port) -and
            $port -ge 1024 -and $port -le 65535) `
            'Installed browser exposed a valid loopback DevTools port'
        $versionEndpoint = "http://127.0.0.1:$port/json/version"
        $version = Invoke-RestMethod -Uri $versionEndpoint -TimeoutSec 10
        Assert-Qa (-not [string]::IsNullOrWhiteSpace([string]$version.webSocketDebuggerUrl)) `
            'Installed browser answered the local DevTools version endpoint'
        Assert-Qa (([string]$version.webSocketDebuggerUrl).StartsWith(
            'ws://127.0.0.1:', [StringComparison]::Ordinal)) `
            'DevTools endpoint is bound to loopback'
        Assert-Qa (-not $process.HasExited) `
            'Installed Focus Browser remains alive after startup smoke checks'
        $script:Result.RuntimeDevToolsPort = $port
    } finally {
        Stop-OwnedRuntimeProcesses $script:RuntimeRootPid $InstalledChrome $RuntimeProfile
    }
}

function Invoke-InstallSmoke {
    Assert-Qa $AllowInstall.IsPresent `
        'InstallSmoke is explicitly authorized with -AllowInstall'

    $installedChrome = Join-Path $env:LOCALAPPDATA `
        'FocusBrowser\Focus Browser\Application\chrome.exe'
    $activeBefore = @(Get-InstalledFocusProcesses $installedChrome)
    Assert-Qa ($activeBefore.Count -eq 0) `
        'No installed Focus Browser process is running; no unrelated process will be terminated'

    $crashesBefore = @(Get-CrashInventory)
    $payloadsBefore = @(Get-NsisPayloadInventory)
    $preservationProfile = Join-Path $script:QaTempRoot 'preservation-user-data'
    $runtimeProfile = Join-Path $script:QaTempRoot 'runtime-user-data'
    Initialize-PreservationProfile $preservationProfile

    $snapshotBefore = Join-Path $script:EvidenceDir 'preservation-before.json'
    $snapshotAfter = Join-Path $script:EvidenceDir 'preservation-after.json'
    Invoke-ReleaseVerifier 'Snapshot' `
        (Join-Path $script:EvidenceDir 'preservation-snapshot.log') @(
            '-UserDataPath', $preservationProfile,
            '-SnapshotPath', $snapshotBefore
        )

    Invoke-SilentInstall 1 (Join-Path $script:EvidenceDir 'installer-pass-1.log')
    Assert-Qa (Test-Path -LiteralPath $installedChrome -PathType Leaf) `
        "Installed executable exists: $installedChrome"
    Test-PeBranding $installedChrome 'installed chrome.exe' 'Focus Browser'
    Assert-Qa (@(Get-InstalledFocusProcesses $installedChrome).Count -eq 0) `
        'Silent install did not auto-launch the browser'

    Invoke-SilentInstall 2 (Join-Path $script:EvidenceDir 'installer-pass-2.log')
    Assert-Qa (@(Get-InstalledFocusProcesses $installedChrome).Count -eq 0) `
        'Silent repair/up-to-date pass did not auto-launch the browser'

    Invoke-ReleaseVerifier 'Compare' `
        (Join-Path $script:EvidenceDir 'preservation-compare.log') @(
            '-UserDataPath', $preservationProfile,
            '-BaselineSnapshot', $snapshotBefore,
            '-SnapshotPath', $snapshotAfter
        )
    Invoke-ReleaseVerifier 'Registry' `
        (Join-Path $script:EvidenceDir 'registry-verifier.log') @()
    Test-UninstallMetadata $installedChrome

    Invoke-InstalledRuntimeSmoke $installedChrome $runtimeProfile `
        (Join-Path $script:EvidenceDir 'installed-runtime.log')

    $crashesAfter = @(Get-CrashInventory)
    $newCrashes = @(Get-NewInventoryRecords $crashesBefore $crashesAfter)
    $newCrashes | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath `
        (Join-Path $script:EvidenceDir 'new-crash-dumps.json') -Encoding UTF8
    Assert-Qa ($newCrashes.Count -eq 0) `
        'Install/update/runtime smoke created no new chrome/setup/installer crash dump'

    $payloadsAfter = @(Get-NsisPayloadInventory)
    $newPayloads = @(Get-NewInventoryRecords $payloadsBefore $payloadsAfter)
    $newPayloads | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath `
        (Join-Path $script:EvidenceDir 'new-nsis-payloads.json') -Encoding UTF8
    Assert-Qa ($newPayloads.Count -eq 0) `
        'NSIS wrapper left no new focus_browser payload directory under ns*.tmp'
}

$script:RepoRootPath = (Resolve-Path -LiteralPath $RepoRoot).Path
$script:BuildDir = Join-Path $script:RepoRootPath 'build'
$script:OutDir = Join-Path $script:BuildDir 'src\out\Default'
if ([string]::IsNullOrWhiteSpace($InstallerPath)) {
    $InstallerPath = Join-Path $script:BuildDir `
        "FocusBrowser_${ExpectedDisplayVersion}_x64-installer.exe"
}
$script:InstallerPathResolved = Get-NormalizedPath $InstallerPath
if ([string]::IsNullOrWhiteSpace($EvidenceDirectory)) {
    $EvidenceDirectory = Join-Path $script:BuildDir `
        ('qa-evidence\installer-smoke-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
}
$script:EvidenceDir = Get-NormalizedPath $EvidenceDirectory
New-Item -ItemType Directory -Path $script:EvidenceDir -Force | Out-Null

$script:QaTempRoot = Join-Path ([IO.Path]::GetTempPath()) `
    ('FocusBrowser-QA-Installer-' + [Guid]::NewGuid().ToString('N'))
Assert-SafeQaTempRoot $script:QaTempRoot
New-Item -ItemType Directory -Path $script:QaTempRoot -Force | Out-Null
$script:RuntimeRootPid = 0
$script:Result = [ordered]@{
    Schema = 1
    StartedUtc = [DateTime]::UtcNow.ToString('o')
    Mode = $Mode
    RepoRoot = $script:RepoRootPath
    InstallerPath = $script:InstallerPathResolved
    ExpectedVersion = $ExpectedVersion
    ArtifactHashes = @()
    InstallerExitCodes = @()
    RuntimeDevToolsPort = $null
    Success = $false
    Error = $null
}

$exitCode = 0
try {
    if ($Mode -in @('Artifacts', 'All')) { Invoke-ArtifactChecks }
    if ($Mode -in @('InstallSmoke', 'All')) { Invoke-InstallSmoke }
    $script:Result.Success = $true
    Write-Host '[PASS] Focus Browser installer/update smoke verification completed' `
        -ForegroundColor Green
} catch {
    $script:Result.Error = $_.Exception.Message
    Write-Host $_.Exception.Message -ForegroundColor Red
    $exitCode = 1
} finally {
    if ($script:RuntimeRootPid -gt 0) {
        $runtimeProfile = Join-Path $script:QaTempRoot 'runtime-user-data'
        $installedChrome = Join-Path $env:LOCALAPPDATA `
            'FocusBrowser\Focus Browser\Application\chrome.exe'
        Stop-OwnedRuntimeProcesses $script:RuntimeRootPid $installedChrome $runtimeProfile
    }
    $script:Result.FinishedUtc = [DateTime]::UtcNow.ToString('o')
    $script:Result | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath `
        (Join-Path $script:EvidenceDir 'installer-smoke-summary.json') -Encoding UTF8
    if (Test-Path -LiteralPath $script:QaTempRoot -PathType Container) {
        Assert-SafeQaTempRoot $script:QaTempRoot
        Remove-Item -LiteralPath $script:QaTempRoot -Recurse -Force
    }
    Write-Host "[INFO] Evidence: $script:EvidenceDir"
}

exit $exitCode
