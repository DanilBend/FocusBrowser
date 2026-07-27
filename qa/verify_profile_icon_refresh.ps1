# Copyright 2026 The Focus Browser Authors
#
# Isolated Windows runtime QA for profile icon migration. This script never
# opens, reads, or writes the real browser profile and never creates, edits,
# pins, or unpins a shortcut. The launched browser receives one unique
# --user-data-dir below %TEMP%. Cleanup is restricted to the exact executable
# and the process tree rooted at the PID started by this script.

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateScript({ Test-Path -LiteralPath $_ -PathType Leaf })]
    [string]$BrowserPath,

    [ValidateRange(15, 180)]
    [int]$TimeoutSeconds = 60,

    [string]$EvidenceDirectory,

    [switch]$AllowLaunch
)

Set-StrictMode -Version 3.0
$ErrorActionPreference = 'Stop'

$SeedIconVersion = 10
$ExpectedIconVersion = 11

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

function Write-Utf8NoBom([string]$Path, [string]$Text) {
    [IO.File]::WriteAllText($Path, $Text, [Text.UTF8Encoding]::new($false))
}

function Assert-SafeQaTempRoot([string]$Path) {
    $tempRoot = (Get-NormalizedPath ([IO.Path]::GetTempPath())) + '\'
    $candidate = Get-NormalizedPath $Path
    $leaf = Split-Path -Leaf $candidate
    Assert-Qa (
        $candidate.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase) -and
        $leaf.StartsWith('FocusBrowser-QA-ProfileIcon-',
            [StringComparison]::Ordinal)
    ) "Disposable profile root is an isolated Focus Browser QA child of TEMP: $candidate"
}

function Get-ExactBrowserProcesses([string]$ExecutablePath) {
    return @(
        Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
            Where-Object {
                -not [string]::IsNullOrWhiteSpace([string]$_.ExecutablePath) -and
                (Test-SamePath ([string]$_.ExecutablePath) $ExecutablePath)
            }
    )
}

function Get-OwnedBrowserProcesses(
    [int]$RootProcessId,
    [string]$ExecutablePath,
    [string]$UserDataDirectory
) {
    $all = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
    $owned = @{}

    foreach ($process in $all) {
        $isExactExecutable = -not [string]::IsNullOrWhiteSpace(
                [string]$process.ExecutablePath) -and
            (Test-SamePath ([string]$process.ExecutablePath) $ExecutablePath)
        if (-not $isExactExecutable) { continue }

        $commandLine = [string]$process.CommandLine
        $hasQaProfile = -not [string]::IsNullOrWhiteSpace($commandLine) -and
            $commandLine.IndexOf('--user-data-dir',
                [StringComparison]::OrdinalIgnoreCase) -ge 0 -and
            $commandLine.IndexOf($UserDataDirectory,
                [StringComparison]::OrdinalIgnoreCase) -ge 0
        if ([int]$process.ProcessId -eq $RootProcessId -or $hasQaProfile) {
            $owned[[int]$process.ProcessId] = $process
        }
    }

    $added = $true
    while ($added) {
        $added = $false
        foreach ($process in $all) {
            $processId = [int]$process.ProcessId
            if ($owned.ContainsKey($processId) -or
                -not $owned.ContainsKey([int]$process.ParentProcessId)) {
                continue
            }
            if (-not [string]::IsNullOrWhiteSpace(
                    [string]$process.ExecutablePath) -and
                (Test-SamePath ([string]$process.ExecutablePath) $ExecutablePath)) {
                $owned[$processId] = $process
                $added = $true
            }
        }
    }
    return @($owned.Values | Sort-Object ProcessId)
}

function Stop-OwnedBrowserProcesses(
    [int]$RootProcessId,
    [string]$ExecutablePath,
    [string]$UserDataDirectory
) {
    $owned = @(
        Get-OwnedBrowserProcesses $RootProcessId $ExecutablePath `
            $UserDataDirectory
    )
    foreach ($process in @($owned | Sort-Object ProcessId -Descending)) {
        Stop-Process -Id ([int]$process.ProcessId) -Force `
            -ErrorAction SilentlyContinue
    }
    $deadline = [DateTime]::UtcNow.AddSeconds(10)
    do {
        Start-Sleep -Milliseconds 100
        $remaining = @(
            Get-OwnedBrowserProcesses $RootProcessId $ExecutablePath `
                $UserDataDirectory
        )
    } while ($remaining.Count -gt 0 -and [DateTime]::UtcNow -lt $deadline)
    Assert-Qa ($remaining.Count -eq 0) `
        'Only the browser processes owned by the disposable QA profile were cleaned up'
}

function New-NoTileSeedIcon([string]$Path) {
    Add-Type -AssemblyName System.Drawing
    $frames = @()
    foreach ($size in @(48, 256)) {
        $bitmap = [Drawing.Bitmap]::new(
            $size, $size, [Drawing.Imaging.PixelFormat]::Format32bppArgb)
        $graphics = [Drawing.Graphics]::FromImage($bitmap)
        $pen = $null
        $brush = $null
        $stream = $null
        try {
            $graphics.Clear([Drawing.Color]::Transparent)
            $graphics.SmoothingMode = [Drawing.Drawing2D.SmoothingMode]::AntiAlias
            $graphics.PixelOffsetMode = [Drawing.Drawing2D.PixelOffsetMode]::HighQuality
            $stroke = [Math]::Max(2.0, $size * 0.075)
            $pen = [Drawing.Pen]::new([Drawing.Color]::White, [single]$stroke)
            $brush = [Drawing.SolidBrush]::new([Drawing.Color]::White)
            $outerInset = [single]($size * 0.13)
            $innerInset = [single]($size * 0.36)
            $graphics.DrawEllipse(
                $pen,
                [Drawing.RectangleF]::new(
                    $outerInset, $outerInset,
                    [single]($size - 2 * $outerInset),
                    [single]($size - 2 * $outerInset)))
            $graphics.DrawEllipse(
                $pen,
                [Drawing.RectangleF]::new(
                    $innerInset, $innerInset,
                    [single]($size - 2 * $innerInset),
                    [single]($size - 2 * $innerInset)))
            $dot = [single]($size * 0.11)
            $graphics.FillEllipse(
                $brush,
                [Drawing.RectangleF]::new(
                    [single](($size - $dot) / 2),
                    [single](($size - $dot) / 2), $dot, $dot))
            $stream = [IO.MemoryStream]::new()
            $bitmap.Save($stream, [Drawing.Imaging.ImageFormat]::Png)
            $frames += [PSCustomObject]@{
                Size = $size
                Bytes = $stream.ToArray()
            }
        } finally {
            if ($null -ne $stream) { $stream.Dispose() }
            if ($null -ne $brush) { $brush.Dispose() }
            if ($null -ne $pen) { $pen.Dispose() }
            $graphics.Dispose()
            $bitmap.Dispose()
        }
    }

    $output = [IO.MemoryStream]::new()
    $writer = [IO.BinaryWriter]::new($output)
    try {
        $writer.Write([uint16]0)
        $writer.Write([uint16]1)
        $writer.Write([uint16]$frames.Count)
        $offset = 6 + 16 * $frames.Count
        foreach ($frame in $frames) {
            $encodedSize = if ($frame.Size -eq 256) { 0 } else { $frame.Size }
            $writer.Write([byte]$encodedSize)
            $writer.Write([byte]$encodedSize)
            $writer.Write([byte]0)
            $writer.Write([byte]0)
            $writer.Write([uint16]1)
            $writer.Write([uint16]32)
            $writer.Write([uint32]$frame.Bytes.Length)
            $writer.Write([uint32]$offset)
            $offset += $frame.Bytes.Length
        }
        foreach ($frame in $frames) { $writer.Write([byte[]]$frame.Bytes) }
        $writer.Flush()
        [IO.File]::WriteAllBytes($Path, $output.ToArray())
    } finally {
        $writer.Dispose()
        $output.Dispose()
    }
}

function Get-IcoDirectory([string]$Path) {
    $bytes = [IO.File]::ReadAllBytes($Path)
    $stream = [IO.MemoryStream]::new($bytes, $false)
    $reader = [IO.BinaryReader]::new($stream)
    try {
        if ($reader.ReadUInt16() -ne 0) {
            throw "[FAIL] $Path has an invalid ICO reserved field"
        }
        if ($reader.ReadUInt16() -ne 1) {
            throw "[FAIL] $Path is not a Windows icon family"
        }
        $count = [int]$reader.ReadUInt16()
        if ($count -le 0 -or $count -gt 64) {
            throw "[FAIL] $Path has an invalid icon family size: $count"
        }
        $records = @()
        for ($index = 0; $index -lt $count; $index++) {
            $widthByte = [int]$reader.ReadByte()
            $heightByte = [int]$reader.ReadByte()
            $null = $reader.ReadByte()
            $null = $reader.ReadByte()
            $planes = $reader.ReadUInt16()
            $bitsPerPixel = $reader.ReadUInt16()
            $length = [uint32]$reader.ReadUInt32()
            $offset = [uint32]$reader.ReadUInt32()
            $width = if ($widthByte -eq 0) { 256 } else { $widthByte }
            $height = if ($heightByte -eq 0) { 256 } else { $heightByte }
            if ($width -ne $height) {
                throw "[FAIL] $Path frame $index is not square ($width x $height)"
            }
            if ($length -eq 0 -or
                ([uint64]$offset + [uint64]$length) -gt [uint64]$bytes.Length) {
                throw "[FAIL] $Path frame $index payload is outside the ICO file"
            }
            $records += [PSCustomObject]@{
                Index = $index
                Width = $width
                Height = $height
                Planes = $planes
                BitsPerPixel = $bitsPerPixel
                PayloadLength = $length
                PayloadOffset = $offset
            }
        }
        return @($records)
    } finally {
        $reader.Dispose()
        $stream.Dispose()
    }
}

function Get-BitmapInspection(
    [Drawing.Bitmap]$Bitmap,
    [int]$RequestedSize,
    [string]$Source
) {
    $pixels = [byte[]]::new($Bitmap.Width * $Bitmap.Height * 4)
    $offset = 0
    $transparent = 0
    $partial = 0
    $opaque = 0
    $graphite = 0
    $graphiteLike = 0
    $white = 0
    for ($y = 0; $y -lt $Bitmap.Height; $y++) {
        for ($x = 0; $x -lt $Bitmap.Width; $x++) {
            $pixel = $Bitmap.GetPixel($x, $y)
            $pixels[$offset++] = $pixel.R
            $pixels[$offset++] = $pixel.G
            $pixels[$offset++] = $pixel.B
            $pixels[$offset++] = $pixel.A
            if ($pixel.A -eq 0) {
                $transparent++
            } elseif ($pixel.A -eq 255) {
                $opaque++
                if ($pixel.R -eq 48 -and $pixel.G -eq 48 -and
                    $pixel.B -eq 48) {
                    $graphite++
                }
                if ($pixel.R -eq $pixel.G -and $pixel.G -eq $pixel.B -and
                    $pixel.R -ge 36 -and $pixel.R -le 80) {
                    $graphiteLike++
                }
                if ($pixel.R -ge 248 -and $pixel.G -ge 248 -and
                    $pixel.B -ge 248) {
                    $white++
                }
            } else {
                $partial++
            }
        }
    }
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        $hashBytes = $sha.ComputeHash($pixels)
    } finally {
        $sha.Dispose()
    }
    return [PSCustomObject]@{
        Source = $Source
        RequestedSize = $RequestedSize
        ActualWidth = $Bitmap.Width
        ActualHeight = $Bitmap.Height
        PixelSha256 = ([BitConverter]::ToString($hashBytes)).Replace(
            '-', '').ToLowerInvariant()
        TransparentPixels = $transparent
        PartialAlphaPixels = $partial
        OpaquePixels = $opaque
        GraphitePixels = $graphite
        GraphiteLikePixels = $graphiteLike
        WhitePixels = $white
    }
}

function Initialize-NativeIconApi {
    if ('FocusBrowserQa.NativeIcon' -as [type]) { return }
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
namespace FocusBrowserQa {
  public static class NativeIcon {
    [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    public static extern uint PrivateExtractIcons(
        string fileName, int iconIndex, int width, int height,
        IntPtr[] icons, uint[] iconIds, uint iconCount, uint flags);
    [DllImport("user32.dll", SetLastError = true)]
    public static extern IntPtr CreateIconFromResourceEx(
        byte[] resourceBits, uint resourceSize,
        [MarshalAs(UnmanagedType.Bool)] bool isIcon, uint version,
        int desiredWidth, int desiredHeight, uint flags);
    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool DestroyIcon(IntPtr icon);
  }
}
'@
}

function Get-IcoFrameInspection([string]$Path, [int]$Size) {
    Add-Type -AssemblyName System.Drawing
    Initialize-NativeIconApi
    $entry = @(Get-IcoDirectory $Path) | Where-Object {
        $_.Width -eq $Size
    } | Select-Object -First 1
    if ($null -eq $entry) { throw "[FAIL] $Path has no $Size px frame" }
    $fileBytes = [IO.File]::ReadAllBytes($Path)
    $payload = [byte[]]::new([int]$entry.PayloadLength)
    [Array]::Copy(
        $fileBytes, [int]$entry.PayloadOffset,
        $payload, 0, [int]$entry.PayloadLength)
    $handle = [FocusBrowserQa.NativeIcon]::CreateIconFromResourceEx(
        $payload, [uint32]$payload.Length, $true, 0x00030000,
        $Size, $Size, 0)
    if ($handle -eq [IntPtr]::Zero) {
        throw "[FAIL] Win32 could not decode the $Size px frame from $Path"
    }
    $icon = [Drawing.Icon]::FromHandle($handle)
    $ownedIcon = $null
    $bitmap = $null
    try {
        $ownedIcon = [Drawing.Icon]$icon.Clone()
        $bitmap = $ownedIcon.ToBitmap()
        return Get-BitmapInspection $bitmap $Size $Path
    } finally {
        if ($null -ne $bitmap) { $bitmap.Dispose() }
        if ($null -ne $ownedIcon) { $ownedIcon.Dispose() }
        $icon.Dispose()
        $null = [FocusBrowserQa.NativeIcon]::DestroyIcon($handle)
    }
}

function Get-IcoInspection([string]$Path) {
    Assert-Qa (Test-Path -LiteralPath $Path -PathType Leaf) `
        "Profile ICO exists: $Path"
    $directory = @(Get-IcoDirectory $Path)
    $sizes = @($directory.Width | Sort-Object -Unique)
    $frames = @(
        foreach ($size in $sizes) {
            Get-IcoFrameInspection $Path ([int]$size)
        }
    )
    return [PSCustomObject]@{
        Path = $Path
        Length = (Get-Item -LiteralPath $Path).Length
        FileSha256 = (Get-FileHash -LiteralPath $Path `
            -Algorithm SHA256).Hash.ToLowerInvariant()
        Directory = $directory
        Frames = $frames
    }
}

function Get-ExecutableIconFrameInspection([string]$Path, [int]$Size) {
    Add-Type -AssemblyName System.Drawing
    Initialize-NativeIconApi
    $handles = [IntPtr[]]::new(1)
    $iconIds = [uint32[]]::new(1)
    $count = [FocusBrowserQa.NativeIcon]::PrivateExtractIcons(
        $Path, 0, $Size, $Size, $handles, $iconIds, 1, 0)
    Assert-Qa ($count -eq 1 -and $handles[0] -ne [IntPtr]::Zero) `
        "Built executable exposes a $Size px application icon"
    $borrowedIcon = [Drawing.Icon]::FromHandle($handles[0])
    $ownedIcon = $null
    $bitmap = $null
    try {
        $ownedIcon = [Drawing.Icon]$borrowedIcon.Clone()
        $bitmap = $ownedIcon.ToBitmap()
        return Get-BitmapInspection $bitmap $Size $Path
    } finally {
        if ($null -ne $bitmap) { $bitmap.Dispose() }
        if ($null -ne $ownedIcon) { $ownedIcon.Dispose() }
        $borrowedIcon.Dispose()
        $null = [FocusBrowserQa.NativeIcon]::DestroyIcon($handles[0])
    }
}

function Get-ProfileIconVersion([string]$PreferencesPath) {
    if (-not (Test-Path -LiteralPath $PreferencesPath -PathType Leaf)) {
        return $null
    }
    try {
        $preferences = Get-Content -LiteralPath $PreferencesPath -Raw |
            ConvertFrom-Json
        if ($null -eq $preferences -or
            $null -eq $preferences.PSObject.Properties['profile']) {
            return $null
        }
        $profile = $preferences.profile
        if ($null -eq $profile -or
            $null -eq $profile.PSObject.Properties['icon_version']) {
            return $null
        }
        return [int]$profile.icon_version
    } catch {
        return $null
    }
}

function Send-CdpBrowserClose([string]$WebSocketDebuggerUrl) {
    $socket = [Net.WebSockets.ClientWebSocket]::new()
    try {
        $null = $socket.ConnectAsync(
            [Uri]$WebSocketDebuggerUrl,
            [Threading.CancellationToken]::None).GetAwaiter().GetResult()
        $payload = [Text.Encoding]::UTF8.GetBytes(
            '{"id":1,"method":"Browser.close"}')
        $segment = [ArraySegment[byte]]::new($payload)
        $null = $socket.SendAsync(
            $segment,
            [Net.WebSockets.WebSocketMessageType]::Text,
            $true,
            [Threading.CancellationToken]::None).GetAwaiter().GetResult()
    } finally {
        $socket.Dispose()
    }
}

Assert-Qa $AllowLaunch.IsPresent `
    'Runtime launch is explicitly authorized with -AllowLaunch'

$script:Browser = (Resolve-Path -LiteralPath $BrowserPath).Path
$browserInfo = [Diagnostics.FileVersionInfo]::GetVersionInfo($script:Browser)
Assert-Qa ($browserInfo.ProductName -eq 'Focus Browser') `
    "BrowserPath is a Focus Browser executable: $($browserInfo.ProductName)"

$existingProcesses = @(Get-ExactBrowserProcesses $script:Browser)
Assert-Qa ($existingProcesses.Count -eq 0) `
    'No process from the supplied BrowserPath is running before isolated QA'

$repoRoot = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($EvidenceDirectory)) {
    $EvidenceDirectory = Join-Path $repoRoot `
        ('build\qa-evidence\profile-icon-refresh-' +
            (Get-Date -Format 'yyyyMMdd-HHmmss'))
}
$script:EvidenceDir = Get-NormalizedPath $EvidenceDirectory
New-Item -ItemType Directory -Path $script:EvidenceDir -Force | Out-Null

$script:QaRoot = Join-Path ([IO.Path]::GetTempPath()) `
    ('FocusBrowser-QA-ProfileIcon-' + [Guid]::NewGuid().ToString('N'))
Assert-SafeQaTempRoot $script:QaRoot
$script:UserDataDir = Join-Path $script:QaRoot 'User Data'
$script:ProfileDir = Join-Path $script:UserDataDir 'Default'
New-Item -ItemType Directory -Path $script:ProfileDir -Force | Out-Null
Assert-Qa ((Get-NormalizedPath $script:ProfileDir).StartsWith(
        (Get-NormalizedPath $script:QaRoot) + '\',
        [StringComparison]::OrdinalIgnoreCase)) `
    'Disposable Default profile is contained by the unique QA root'

$preferencesPath = Join-Path $script:ProfileDir 'Preferences'
$profileIconPath = Join-Path $script:ProfileDir 'Google Profile.ico'
$browserLog = Join-Path $script:EvidenceDir 'browser.log'
$beforeIconEvidence = Join-Path $script:EvidenceDir 'profile-icon-before.ico'
$afterIconEvidence = Join-Path $script:EvidenceDir 'profile-icon-after.ico'
$beforePreferencesEvidence = Join-Path $script:EvidenceDir `
    'Preferences.before.json'
$afterPreferencesEvidence = Join-Path $script:EvidenceDir `
    'Preferences.after.json'

$seedPreferences = [ordered]@{
    browser = [ordered]@{ has_seen_welcome_page = $true }
    distribution = [ordered]@{ skip_first_run_ui = $true }
    profile = [ordered]@{
        avatar_index = 0
        icon_version = $SeedIconVersion
        icon_win11_format = $true
        name = 'Focus Browser QA'
    }
    session = [ordered]@{ restore_on_startup = 5 }
}
Write-Utf8NoBom $preferencesPath `
    ($seedPreferences | ConvertTo-Json -Depth 8 -Compress)
New-NoTileSeedIcon $profileIconPath
Copy-Item -LiteralPath $preferencesPath -Destination $beforePreferencesEvidence
Copy-Item -LiteralPath $profileIconPath -Destination $beforeIconEvidence

$beforeIcon = Get-IcoInspection $profileIconPath
Assert-Qa ((Get-ProfileIconVersion $preferencesPath) -eq $SeedIconVersion) `
    "Disposable profile starts at profile.icon_version=$SeedIconVersion"
Assert-Qa (@($beforeIcon.Frames | Where-Object {
            $_.GraphitePixels -gt 0
        }).Count -eq 0) `
    'Seeded Google Profile.ico has no opaque #303030 graphite tile pixels'

$builtFrames = @(
    Get-ExecutableIconFrameInspection $script:Browser 48
    Get-ExecutableIconFrameInspection $script:Browser 256
)
foreach ($frame in $builtFrames) {
    Assert-Qa ($frame.GraphitePixels -gt 0 -and
        $frame.GraphiteLikePixels -gt 0 -and $frame.WhitePixels -gt 0) `
        "Built $($frame.RequestedSize) px icon is the graphite/white application family"
}

$script:RootProcessId = 0
$rootProcess = $null
$afterIcon = $null
$runtimeObservedVersion = $null
$devToolsPort = $null
$summary = [ordered]@{
    Schema = 1
    StartedUtc = [DateTime]::UtcNow.ToString('o')
    BrowserPath = $script:Browser
    BrowserFileVersion = $browserInfo.FileVersion
    BrowserSha256 = (Get-FileHash -LiteralPath $script:Browser `
        -Algorithm SHA256).Hash.ToLowerInvariant()
    DisposableUserDataDir = $script:UserDataDir
    ProfileDirectory = $script:ProfileDir
    SeedIconVersion = $SeedIconVersion
    ExpectedIconVersion = $ExpectedIconVersion
    BeforeIcon = $beforeIcon
    BuiltIconFrames = $builtFrames
    RuntimeObservedIconVersion = $null
    FinalIconVersion = $null
    AfterIcon = $null
    RootProcessId = $null
    OwnedProcessIds = @()
    DevToolsPort = $null
    BrowserLog = $browserLog
    EvidenceDirectory = $script:EvidenceDir
    Success = $false
    Error = $null
}

$exitCode = 0
try {
    $arguments = @(
        ('--user-data-dir="' + $script:UserDataDir + '"'),
        '--profile-directory=Default',
        '--enable-profile-shortcut-manager',
        '--headless=new',
        '--remote-debugging-port=0',
        '--no-first-run',
        '--no-default-browser-check',
        '--disable-background-networking',
        '--disable-background-mode',
        '--disable-breakpad',
        '--disable-component-update',
        '--disable-default-apps',
        '--disable-extensions',
        '--disable-session-crashed-bubble',
        '--disable-sync',
        '--enable-logging',
        ('--log-file="' + $browserLog + '"'),
        'about:blank'
    )
    $rootProcess = Start-Process -FilePath $script:Browser `
        -ArgumentList $arguments -PassThru
    $script:RootProcessId = [int]$rootProcess.Id
    $summary.RootProcessId = $script:RootProcessId

    $devToolsPortFile = Join-Path $script:UserDataDir 'DevToolsActivePort'
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    $iconChanged = $false
    while ([DateTime]::UtcNow -lt $deadline) {
        $rootProcess.Refresh()
        if ($rootProcess.HasExited) {
            throw "Focus Browser exited before profile icon refresh (exit $($rootProcess.ExitCode))"
        }
        $owned = @(
            Get-OwnedBrowserProcesses $script:RootProcessId $script:Browser `
                $script:UserDataDir
        )
        $summary.OwnedProcessIds = @($owned.ProcessId)
        if (Test-Path -LiteralPath $devToolsPortFile -PathType Leaf) {
            $portText = (Get-Content -LiteralPath $devToolsPortFile `
                -TotalCount 1).Trim()
            $port = 0
            if ([int]::TryParse($portText, [ref]$port)) {
                $devToolsPort = $port
            }
        }
        if (Test-Path -LiteralPath $profileIconPath -PathType Leaf) {
            $currentHash = (Get-FileHash -LiteralPath $profileIconPath `
                -Algorithm SHA256).Hash.ToLowerInvariant()
            $iconChanged = $currentHash -ne $beforeIcon.FileSha256
        }
        $runtimeObservedVersion = Get-ProfileIconVersion $preferencesPath
        if ($iconChanged -and
            $runtimeObservedVersion -eq $ExpectedIconVersion -and
            $null -ne $devToolsPort) {
            break
        }
        Start-Sleep -Milliseconds 150
    }
    Assert-Qa ($null -ne $devToolsPort) `
        'Disposable browser exposed a loopback DevTools port'
    Assert-Qa $iconChanged `
        'Google Profile.ico changed while the disposable profile was loaded'

    $summary.RuntimeObservedIconVersion = $runtimeObservedVersion
    $summary.DevToolsPort = $devToolsPort
    $versionEndpoint = "http://127.0.0.1:$devToolsPort/json/version"
    $devToolsVersion = Invoke-RestMethod -Uri $versionEndpoint -TimeoutSec 10
    Assert-Qa (-not [string]::IsNullOrWhiteSpace(
            [string]$devToolsVersion.webSocketDebuggerUrl)) `
        'Disposable browser answered its loopback DevTools endpoint'
    Assert-Qa (([string]$devToolsVersion.webSocketDebuggerUrl).StartsWith(
            'ws://127.0.0.1:', [StringComparison]::Ordinal)) `
        'DevTools control channel is loopback-only'
    Send-CdpBrowserClose ([string]$devToolsVersion.webSocketDebuggerUrl)

    $shutdownDeadline = [DateTime]::UtcNow.AddSeconds(20)
    do {
        Start-Sleep -Milliseconds 100
        $ownedAfterClose = @(
            Get-OwnedBrowserProcesses $script:RootProcessId $script:Browser `
                $script:UserDataDir
        )
    } while ($ownedAfterClose.Count -gt 0 -and
        [DateTime]::UtcNow -lt $shutdownDeadline)
    Assert-Qa ($ownedAfterClose.Count -eq 0) `
        'Browser.close shut down the disposable browser process tree cleanly'

    $finalIconVersion = Get-ProfileIconVersion $preferencesPath
    $summary.FinalIconVersion = $finalIconVersion
    Assert-Qa ($finalIconVersion -eq $ExpectedIconVersion) `
        "Profile preference migrated from $SeedIconVersion to $ExpectedIconVersion"

    $afterIcon = Get-IcoInspection $profileIconPath
    $summary.AfterIcon = $afterIcon
    Assert-Qa ($afterIcon.FileSha256 -ne $beforeIcon.FileSha256) `
        'Generated profile ICO differs byte-for-byte from the no-tile seed'
    $afterSizes = @($afterIcon.Directory.Width | Sort-Object -Unique)
    Assert-Qa ($afterSizes -contains 48 -and $afterSizes -contains 256) `
        'Generated profile ICO contains the native 48 px and 256 px family members'

    foreach ($builtFrame in $builtFrames) {
        $generatedFrame = $afterIcon.Frames | Where-Object {
            $_.RequestedSize -eq $builtFrame.RequestedSize
        } | Select-Object -First 1
        Assert-Qa ($null -ne $generatedFrame) `
            "Generated profile ICO exposes a $($builtFrame.RequestedSize) px frame"
        Assert-Qa ($generatedFrame.GraphitePixels -gt 0 -and
            $generatedFrame.GraphiteLikePixels -ge
                [Math]::Floor($builtFrame.GraphiteLikePixels * 0.60) -and
            $generatedFrame.WhitePixels -ge
                [Math]::Floor($builtFrame.WhitePixels * 0.60)) `
            "Generated $($builtFrame.RequestedSize) px profile frame belongs to the built graphite/white icon family"
    }

    Copy-Item -LiteralPath $profileIconPath -Destination $afterIconEvidence
    Copy-Item -LiteralPath $preferencesPath `
        -Destination $afterPreferencesEvidence
    Assert-Qa (Test-Path -LiteralPath $afterIconEvidence -PathType Leaf) `
        'Post-refresh profile ICO was copied to QA evidence'
    Assert-Qa (Test-Path -LiteralPath $afterPreferencesEvidence -PathType Leaf) `
        'Post-refresh Preferences were copied to QA evidence'

    $summary.Success = $true
    Write-Host '[PASS] Disposable profile icon migrated to version 11 and the built graphite icon family.' `
        -ForegroundColor Green
} catch {
    $summary.Error = $_.Exception.Message
    Write-Host $_.Exception.Message -ForegroundColor Red
    $exitCode = 1
} finally {
    if ($script:RootProcessId -gt 0) {
        $remaining = @(
            Get-OwnedBrowserProcesses $script:RootProcessId $script:Browser `
                $script:UserDataDir
        )
        if ($remaining.Count -gt 0) {
            Stop-OwnedBrowserProcesses $script:RootProcessId $script:Browser `
                $script:UserDataDir
        }
    }
    if ((Test-Path -LiteralPath $preferencesPath -PathType Leaf) -and
        -not (Test-Path -LiteralPath $afterPreferencesEvidence -PathType Leaf)) {
        Copy-Item -LiteralPath $preferencesPath `
            -Destination $afterPreferencesEvidence -ErrorAction SilentlyContinue
    }
    if ((Test-Path -LiteralPath $profileIconPath -PathType Leaf) -and
        -not (Test-Path -LiteralPath $afterIconEvidence -PathType Leaf)) {
        Copy-Item -LiteralPath $profileIconPath `
            -Destination $afterIconEvidence -ErrorAction SilentlyContinue
    }
    $summary.FinishedUtc = [DateTime]::UtcNow.ToString('o')
    $summary | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath `
        (Join-Path $script:EvidenceDir 'profile-icon-refresh-summary.json') `
        -Encoding UTF8
    if (Test-Path -LiteralPath $script:QaRoot -PathType Container) {
        Assert-SafeQaTempRoot $script:QaRoot
        Remove-Item -LiteralPath $script:QaRoot -Recurse -Force
    }
    Write-Host "[INFO] Evidence: $script:EvidenceDir"
}

exit $exitCode
