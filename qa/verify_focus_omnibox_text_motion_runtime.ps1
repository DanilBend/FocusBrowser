# Copyright 2026 The Focus Browser Authors
#
# Visible, deterministic smoke test for the native omnibox glyph settle. The
# test launches one exact Focus Browser process with a unique disposable
# profile, sends real Windows keyboard input to the address bar, and captures
# the address-bar pixels at fixed intervals. It never attaches to or terminates
# another browser process.

[CmdletBinding()]
param(
    [string]$BrowserPath,
    [string]$EvidenceDirectory,
    [ValidateRange(10, 60)]
    [int]$StartupTimeoutSeconds = 30,
    [switch]$KeepProfile
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($BrowserPath)) {
    $BrowserPath = Join-Path (Split-Path -Parent $PSScriptRoot) `
        'build\src\out\Default\chrome.exe'
}
$BrowserPath = [IO.Path]::GetFullPath($BrowserPath)
if (-not (Test-Path -LiteralPath $BrowserPath -PathType Leaf)) {
    throw "Focus Browser executable was not found: $BrowserPath"
}

$runId = [Guid]::NewGuid().ToString('N')
$profileDirectory = Join-Path ([IO.Path]::GetTempPath()) `
    "FocusBrowser-QA-Omnibox-$runId"
if ([string]::IsNullOrWhiteSpace($EvidenceDirectory)) {
    $EvidenceDirectory = Join-Path ([IO.Path]::GetTempPath()) `
        "FocusBrowser-QA-Omnibox-Evidence-$runId"
}
$EvidenceDirectory = [IO.Path]::GetFullPath($EvidenceDirectory)

function Assert-Qa([bool]$Condition, [string]$Message) {
    if (-not $Condition) {
        throw "[FAIL] $Message"
    }
    Write-Host "[PASS] $Message" -ForegroundColor Green
}

function Assert-SafeProfilePath([string]$Path) {
    $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    $fullPath = [IO.Path]::GetFullPath($Path)
    $leaf = Split-Path -Leaf $fullPath
    Assert-Qa (
        $fullPath.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase) -and
        $leaf.StartsWith('FocusBrowser-QA-Omnibox-',
            [StringComparison]::Ordinal)
    ) "Disposable profile is an exact Focus Browser QA child of TEMP"
}

function Get-BitmapHash([Drawing.Bitmap]$Bitmap) {
    $stream = [IO.MemoryStream]::new()
    try {
        $Bitmap.Save($stream, [Drawing.Imaging.ImageFormat]::Png)
        $stream.Position = 0
        $sha = [Security.Cryptography.SHA256]::Create()
        try {
            return ([BitConverter]::ToString($sha.ComputeHash($stream))).Replace('-', '').ToLowerInvariant()
        } finally {
            $sha.Dispose()
        }
    } finally {
        $stream.Dispose()
    }
}

function Copy-ScreenRectangle([Drawing.Rectangle]$Rectangle) {
    $bitmap = [Drawing.Bitmap]::new(
        $Rectangle.Width, $Rectangle.Height,
        [Drawing.Imaging.PixelFormat]::Format32bppArgb)
    $graphics = [Drawing.Graphics]::FromImage($bitmap)
    try {
        $graphics.CopyFromScreen(
            $Rectangle.Location, [Drawing.Point]::Empty, $Rectangle.Size,
            [Drawing.CopyPixelOperation]::SourceCopy)
    } finally {
        $graphics.Dispose()
    }
    return $bitmap
}

function Get-DifferenceBounds(
    [Drawing.Bitmap]$Before,
    [Drawing.Bitmap]$After,
    [int]$Threshold = 18
) {
    Assert-Qa (
        $Before.Width -eq $After.Width -and $Before.Height -eq $After.Height
    ) 'Compared omnibox captures have equal dimensions'

    $left = $Before.Width
    $top = $Before.Height
    $right = -1
    $bottom = -1
    for ($y = 0; $y -lt $Before.Height; $y++) {
        for ($x = 0; $x -lt $Before.Width; $x++) {
            $a = $Before.GetPixel($x, $y)
            $b = $After.GetPixel($x, $y)
            $delta = [Math]::Abs([int]$a.R - [int]$b.R) +
                [Math]::Abs([int]$a.G - [int]$b.G) +
                [Math]::Abs([int]$a.B - [int]$b.B)
            if ($delta -gt $Threshold) {
                $left = [Math]::Min($left, $x)
                $top = [Math]::Min($top, $y)
                $right = [Math]::Max($right, $x)
                $bottom = [Math]::Max($bottom, $y)
            }
        }
    }
    if ($right -lt $left -or $bottom -lt $top) {
        return $null
    }
    return [Drawing.Rectangle]::FromLTRB($left, $top, $right + 1, $bottom + 1)
}

function Get-RegionHash([Drawing.Bitmap]$Bitmap, [Drawing.Rectangle]$Region) {
    $clone = $Bitmap.Clone($Region, [Drawing.Imaging.PixelFormat]::Format32bppArgb)
    try {
        return Get-BitmapHash $clone
    } finally {
        $clone.Dispose()
    }
}

function Get-RegionDistance(
    [Drawing.Bitmap]$Bitmap,
    [Drawing.Bitmap]$Reference,
    [Drawing.Rectangle]$Region
) {
    [long]$distance = 0
    for ($y = $Region.Top; $y -lt $Region.Bottom; $y++) {
        for ($x = $Region.Left; $x -lt $Region.Right; $x++) {
            $a = $Bitmap.GetPixel($x, $y)
            $b = $Reference.GetPixel($x, $y)
            $distance += [Math]::Abs([int]$a.R - [int]$b.R)
            $distance += [Math]::Abs([int]$a.G - [int]$b.G)
            $distance += [Math]::Abs([int]$a.B - [int]$b.B)
        }
    }
    return $distance
}

function Get-MostFrequentRegionColor(
    [Drawing.Bitmap]$Bitmap,
    [Drawing.Rectangle]$Region
) {
    $counts = [Collections.Generic.Dictionary[int, int]]::new()
    $mostFrequentKey = 0
    $mostFrequentCount = 0
    for ($y = $Region.Top; $y -lt $Region.Bottom; $y++) {
        for ($x = $Region.Left; $x -lt $Region.Right; $x++) {
            $pixel = $Bitmap.GetPixel($x, $y)
            $key = ([int]$pixel.R -shl 16) -bor
                ([int]$pixel.G -shl 8) -bor [int]$pixel.B
            if ($counts.ContainsKey($key)) {
                $counts[$key]++
            } else {
                $counts.Add($key, 1)
            }
            if ($counts[$key] -gt $mostFrequentCount) {
                $mostFrequentKey = $key
                $mostFrequentCount = $counts[$key]
            }
        }
    }
    return [Drawing.Color]::FromArgb(
        ($mostFrequentKey -shr 16) -band 0xff,
        ($mostFrequentKey -shr 8) -band 0xff,
        $mostFrequentKey -band 0xff)
}

function Get-ForegroundVerticalCentroid(
    [Drawing.Bitmap]$Bitmap,
    [Drawing.Rectangle]$Region,
    [Drawing.Color]$BackgroundColor,
    [int]$Threshold = 18
) {
    [double]$weightedY = 0.0
    [double]$totalWeight = 0.0
    for ($y = $Region.Top; $y -lt $Region.Bottom; $y++) {
        for ($x = $Region.Left; $x -lt $Region.Right; $x++) {
            $pixel = $Bitmap.GetPixel($x, $y)
            $distance = [Math]::Abs([int]$pixel.R - [int]$BackgroundColor.R) +
                [Math]::Abs([int]$pixel.G - [int]$BackgroundColor.G) +
                [Math]::Abs([int]$pixel.B - [int]$BackgroundColor.B)
            if ($distance -le $Threshold) {
                continue
            }
            $totalWeight += $distance
            $weightedY += (($y - $Region.Top) + 0.5) * $distance
        }
    }
    if ($totalWeight -eq 0.0) {
        return $null
    }
    return $weightedY / $totalWeight
}

Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class FocusOmniboxNativeMethods {
  [DllImport("user32.dll")]
  public static extern bool SetForegroundWindow(IntPtr hWnd);
  [DllImport("user32.dll")]
  public static extern bool ShowWindowAsync(IntPtr hWnd, int nCmdShow);
  [DllImport("user32.dll", SetLastError = true)]
  public static extern bool SetWindowPos(
      IntPtr hWnd, IntPtr hWndInsertAfter, int x, int y, int cx, int cy,
      uint flags);
  [DllImport("shcore.dll")]
  public static extern int SetProcessDpiAwareness(int value);
}
'@

try {
    # Per-monitor DPI coordinates keep UI Automation and CopyFromScreen aligned.
    [void][FocusOmniboxNativeMethods]::SetProcessDpiAwareness(2)
} catch {
    # Windows may reject a second DPI-awareness request; the host's existing
    # awareness remains valid in that case.
}

Assert-SafeProfilePath $profileDirectory
New-Item -ItemType Directory -Path $profileDirectory -Force | Out-Null
New-Item -ItemType Directory -Path $EvidenceDirectory -Force | Out-Null

$process = $null
$frames = [Collections.Generic.List[object]]::new()
$bitmaps = [Collections.Generic.List[Drawing.Bitmap]]::new()
$baselineBitmap = $null

try {
    $startInfo = [Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $BrowserPath
    $startInfo.Arguments = @(
        "--user-data-dir=`"$profileDirectory`""
        '--no-first-run'
        '--no-default-browser-check'
        '--disable-background-networking'
        '--disable-component-update'
        '--window-size=1400,900'
        'chrome://newtab/'
    ) -join ' '
    $startInfo.UseShellExecute = $false
    $process = [Diagnostics.Process]::Start($startInfo)
    Assert-Qa ($null -ne $process) 'Started the exact requested Focus Browser executable'

    $deadline = [DateTime]::UtcNow.AddSeconds($StartupTimeoutSeconds)
    $windowHandle = [IntPtr]::Zero
    while ([DateTime]::UtcNow -lt $deadline -and $windowHandle -eq [IntPtr]::Zero) {
        Start-Sleep -Milliseconds 100
        $process.Refresh()
        if ($process.HasExited) {
            throw "Focus Browser exited before exposing a window (exit $($process.ExitCode))"
        }
        $windowHandle = $process.MainWindowHandle
    }
    Assert-Qa ($windowHandle -ne [IntPtr]::Zero) `
        'The exact spawned process exposed a top-level browser window'

    [void][FocusOmniboxNativeMethods]::ShowWindowAsync($windowHandle, 9)
    [void][FocusOmniboxNativeMethods]::SetWindowPos(
        $windowHandle, [IntPtr]::Zero, 70, 70, 1400, 900, 0x0040)
    [void][FocusOmniboxNativeMethods]::SetForegroundWindow($windowHandle)
    Start-Sleep -Milliseconds 350

    $window = [Windows.Automation.AutomationElement]::FromHandle($windowHandle)
    Assert-Qa ($null -ne $window) 'UI Automation attached to the exact Focus Browser window'

    $editCondition = [Windows.Automation.PropertyCondition]::new(
        [Windows.Automation.AutomationElement]::ControlTypeProperty,
        [Windows.Automation.ControlType]::Edit)
    $edits = $window.FindAll(
        [Windows.Automation.TreeScope]::Descendants, $editCondition)
    $windowRect = $window.Current.BoundingRectangle
    $addressBar = $null
    foreach ($candidate in $edits) {
        $rect = $candidate.Current.BoundingRectangle
        if ($candidate.Current.IsEnabled -and $rect.Width -gt 300 -and
            $rect.Height -gt 20 -and $rect.Height -lt 80 -and
            $rect.Top -ge $windowRect.Top -and
            $rect.Top -lt ($windowRect.Top + 180)) {
            if ($null -eq $addressBar -or
                $rect.Width -gt $addressBar.Current.BoundingRectangle.Width) {
                $addressBar = $candidate
            }
        }
    }
    Assert-Qa ($null -ne $addressBar) 'Found the native address bar by UI Automation geometry'

    $addressBar.SetFocus()
    [void][FocusOmniboxNativeMethods]::SetForegroundWindow($windowHandle)
    [Windows.Forms.SendKeys]::SendWait('^l')
    [Windows.Forms.SendKeys]::SendWait('^a')
    [Windows.Forms.SendKeys]::SendWait('{BACKSPACE}')
    $prefix = 'MotionProbe'
    $inserted = 'Z'
    [Windows.Forms.SendKeys]::SendWait($prefix)
    Start-Sleep -Milliseconds 260

    $valuePattern = $addressBar.GetCurrentPattern(
        [Windows.Automation.ValuePattern]::Pattern)
    Assert-Qa ($valuePattern.Current.Value -eq $prefix) `
        'Address bar received the settled baseline through real keyboard input'

    $bounds = $addressBar.Current.BoundingRectangle
    $captureRect = [Drawing.Rectangle]::FromLTRB(
        [Math]::Floor($bounds.Left), [Math]::Floor($bounds.Top),
        [Math]::Ceiling($bounds.Right), [Math]::Ceiling($bounds.Bottom))
    Assert-Qa ($captureRect.Width -gt 300 -and $captureRect.Height -gt 20) `
        'Address bar capture rectangle is valid'

    $baselineBitmap = Copy-ScreenRectangle $captureRect
    $baselinePath = Join-Path $EvidenceDirectory 'omnibox-before-insert.png'
    $baselineBitmap.Save($baselinePath, [Drawing.Imaging.ImageFormat]::Png)

    $watch = [Diagnostics.Stopwatch]::StartNew()
    [Windows.Forms.SendKeys]::SendWait($inserted)
    $expectedValue = $prefix + $inserted
    $valueDeadline = [DateTime]::UtcNow.AddSeconds(2)
    while ($valuePattern.Current.Value -ne $expectedValue -and
        [DateTime]::UtcNow -lt $valueDeadline) {
        Start-Sleep -Milliseconds 1
    }
    Assert-Qa ($valuePattern.Current.Value -eq $expectedValue) `
        'Address bar committed the inserted glyph before sampling'

    $requestedTimes = @(0, 16, 35, 60, 90, 125, 165, 210, 270)
    foreach ($requestedMs in $requestedTimes) {
        while ($watch.ElapsedMilliseconds -lt $requestedMs) {
            Start-Sleep -Milliseconds 1
        }
        # Capture first at the requested deadline. UI Automation property reads
        # can take a frame; doing them first would miss the strongest part of
        # the three-pixel settle and weaken the fade-only regression.
        $captureElapsedMs = $watch.ElapsedMilliseconds
        $bitmap = Copy-ScreenRectangle $captureRect
        $bitmaps.Add($bitmap)
        $index = $bitmaps.Count - 1
        $framePath = Join-Path $EvidenceDirectory `
            ('omnibox-frame-{0:D2}-{1:D3}ms.png' -f $index, $captureElapsedMs)
        $bitmap.Save($framePath, [Drawing.Imaging.ImageFormat]::Png)

        $currentBounds = $addressBar.Current.BoundingRectangle
        Assert-Qa (
            [Math]::Abs($currentBounds.Left - $bounds.Left) -lt 0.5 -and
            [Math]::Abs($currentBounds.Top - $bounds.Top) -lt 0.5 -and
            [Math]::Abs($currentBounds.Width - $bounds.Width) -lt 0.5 -and
            [Math]::Abs($currentBounds.Height - $bounds.Height) -lt 0.5
        ) "Address bar geometry stayed fixed at $($watch.ElapsedMilliseconds) ms"
        Assert-Qa ($valuePattern.Current.Value -eq $expectedValue) `
            "Address bar value stayed fixed at $($watch.ElapsedMilliseconds) ms"

        $frames.Add([PSCustomObject]@{
            Index = $index
            RequestedMs = $requestedMs
            ElapsedMs = $captureElapsedMs
            Path = $framePath
            Hash = Get-BitmapHash $bitmap
        })
    }

    $finalBitmap = $bitmaps[$bitmaps.Count - 1]
    $differenceBounds = Get-DifferenceBounds $baselineBitmap $finalBitmap
    Assert-Qa ($null -ne $differenceBounds) `
        'Inserted glyph changed a bounded pixel region in the native address bar'

    $motionLeft = [Math]::Max(0, $differenceBounds.Left - 3)
    $motionTop = [Math]::Max(0, $differenceBounds.Top - 3)
    $motionRight = [Math]::Min($finalBitmap.Width, $differenceBounds.Right + 3)
    $motionBottom = [Math]::Min($finalBitmap.Height, $differenceBounds.Bottom + 3)
    $motionRegion = [Drawing.Rectangle]::FromLTRB(
        $motionLeft, $motionTop, $motionRight, $motionBottom)
    $prefixRegion = [Drawing.Rectangle]::FromLTRB(
        0, 0, [Math]::Max(1, $differenceBounds.Left - 3),
        $finalBitmap.Height)

    $motionHashes = [Collections.Generic.List[string]]::new()
    $prefixHashes = [Collections.Generic.List[string]]::new()
    $distances = [Collections.Generic.List[long]]::new()
    $backgroundColor = Get-MostFrequentRegionColor $finalBitmap $motionRegion
    $foregroundCentroids = [Collections.Generic.List[object]]::new()
    for ($i = 0; $i -lt $bitmaps.Count; $i++) {
        $motionHashes.Add((Get-RegionHash $bitmaps[$i] $motionRegion))
        $prefixHashes.Add((Get-RegionHash $bitmaps[$i] $prefixRegion))
        $distance = Get-RegionDistance $bitmaps[$i] $finalBitmap $motionRegion
        $distances.Add($distance)
        $centroid = Get-ForegroundVerticalCentroid `
            $bitmaps[$i] $motionRegion $backgroundColor
        $foregroundCentroids.Add($centroid)
        Add-Member -InputObject $frames[$i] -NotePropertyName MotionHash `
            -NotePropertyValue $motionHashes[$i]
        Add-Member -InputObject $frames[$i] -NotePropertyName PrefixHash `
            -NotePropertyValue $prefixHashes[$i]
        Add-Member -InputObject $frames[$i] -NotePropertyName DistanceToFinal `
            -NotePropertyValue $distance
        Add-Member -InputObject $frames[$i] `
            -NotePropertyName ForegroundCentroidY `
            -NotePropertyValue $centroid
    }

    $uniqueMotionFrames = @($motionHashes | Select-Object -Unique).Count
    $uniquePrefixFrames = @($prefixHashes | Select-Object -Unique).Count
    $uniqueDistances = @($distances | Select-Object -Unique).Count
    Assert-Qa ($uniqueMotionFrames -ge 3 -and $uniqueDistances -ge 3) `
        'New omnibox glyph has multiple transient settle frames'
    Assert-Qa ($uniquePrefixFrames -eq 1) `
        'Pixels before the inserted grapheme stay exactly stable'
    Assert-Qa ($distances[0] -gt 0) `
        'First committed-glyph frame is visibly different from the settled glyph'
    Assert-Qa (
        $motionHashes[$motionHashes.Count - 1] -eq
        $motionHashes[$motionHashes.Count - 2]
    ) 'Omnibox glyph settles without residual movement'

    $finalCentroid = $foregroundCentroids[$foregroundCentroids.Count - 1]
    Assert-Qa ($null -ne $finalCentroid) `
        'Settled omnibox glyph has measurable foreground pixels'
    $transientCentroids = @(
        $foregroundCentroids |
            Select-Object -First ($foregroundCentroids.Count - 2) |
            Where-Object { $null -ne $_ }
    )
    Assert-Qa ($transientCentroids.Count -gt 0) `
        'Transient omnibox frames have measurable foreground pixels'
    $maxTransientCentroid = (
        $transientCentroids | Measure-Object -Maximum
    ).Maximum
    Assert-Qa (
        [double]$maxTransientCentroid -ge ([double]$finalCentroid + 0.5)
    ) 'At least one transient glyph frame is visibly below its final baseline'

    $report = [PSCustomObject]@{
        Ok = $true
        Executable = $BrowserPath
        SpawnedPid = $process.Id
        Profile = $profileDirectory
        Preference = 'focus.ui.motion_enabled (default true)'
        InputPath = 'Windows keyboard -> native OmniboxViewViews'
        Style = 'opacity 0.12 + translateY 3px -> settled, 180ms'
        ForegroundCentroid = [PSCustomObject]@{
            Background = ('#{0:X2}{1:X2}{2:X2}' -f
                $backgroundColor.R, $backgroundColor.G, $backgroundColor.B)
            FinalY = $finalCentroid
            MaximumTransientY = $maxTransientCentroid
            MinimumRequiredDeltaY = 0.5
        }
        Geometry = [PSCustomObject]@{
            Left = $captureRect.Left
            Top = $captureRect.Top
            Width = $captureRect.Width
            Height = $captureRect.Height
        }
        MotionRegion = [PSCustomObject]@{
            Left = $motionRegion.Left
            Top = $motionRegion.Top
            Width = $motionRegion.Width
            Height = $motionRegion.Height
        }
        UniqueMotionFrames = $uniqueMotionFrames
        UniquePrefixFrames = $uniquePrefixFrames
        Frames = $frames
    }
    $reportPath = Join-Path $EvidenceDirectory 'omnibox-text-motion-report.json'
    $report | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $reportPath `
        -Encoding utf8
    Write-Host ($report | ConvertTo-Json -Depth 8)
} finally {
    foreach ($bitmap in $bitmaps) {
        if ($null -ne $bitmap) { $bitmap.Dispose() }
    }
    if ($null -ne $baselineBitmap) { $baselineBitmap.Dispose() }

    if ($null -ne $process) {
        try { $process.Refresh() } catch {}
        if (-not $process.HasExited) {
            # Scope cleanup to the exact root PID created above and its child
            # process tree. Never terminate by image name.
            & taskkill.exe /PID $process.Id /T /F 2>$null | Out-Null
        }
        $process.Dispose()
    }

    if (-not $KeepProfile -and (Test-Path -LiteralPath $profileDirectory)) {
        Assert-SafeProfilePath $profileDirectory
        Remove-Item -LiteralPath $profileDirectory -Recurse -Force `
            -ErrorAction SilentlyContinue
    }
}
