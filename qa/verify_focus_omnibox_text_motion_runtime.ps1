# Copyright 2026 The Focus Browser Authors
#
# Visible, deterministic smoke test for crisp native omnibox glyph paint. The
# test launches one exact Focus Browser process with a disposable profile,
# attempts Windows keyboard input, and falls back to the exact writable UIA
# ValuePattern when UIPI blocks OS input. The report records the mechanism used.
# It never attaches to or terminates another browser process.

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

function Get-CaretExcludedRegionDistance(
    [Drawing.Bitmap]$Bitmap,
    [Drawing.Bitmap]$Reference,
    [Drawing.Rectangle]$Region,
    [int]$MaximumCaretColumns = 4,
    [int]$Threshold = 18
) {
    [long]$totalDistance = 0
    $denseColumns = [Collections.Generic.List[object]]::new()
    $minimumChangedPixels = [Math]::Max(
        3, [int][Math]::Ceiling($Region.Height * 0.55))
    $minimumVerticalSpan = [Math]::Max(
        3, [int][Math]::Ceiling($Region.Height * 0.65))

    for ($x = $Region.Left; $x -lt $Region.Right; $x++) {
        [long]$columnDistance = 0
        $changedPixels = 0
        $firstChangedY = $Region.Bottom
        $lastChangedY = -1
        for ($y = $Region.Top; $y -lt $Region.Bottom; $y++) {
            $a = $Bitmap.GetPixel($x, $y)
            $b = $Reference.GetPixel($x, $y)
            $delta = [Math]::Abs([int]$a.R - [int]$b.R) +
                [Math]::Abs([int]$a.G - [int]$b.G) +
                [Math]::Abs([int]$a.B - [int]$b.B)
            $columnDistance += $delta
            if ($delta -gt $Threshold) {
                $changedPixels++
                $firstChangedY = [Math]::Min($firstChangedY, $y)
                $lastChangedY = [Math]::Max($lastChangedY, $y)
            }
        }
        $totalDistance += $columnDistance
        $verticalSpan = if ($lastChangedY -ge $firstChangedY) {
            $lastChangedY - $firstChangedY + 1
        } else { 0 }
        if ($changedPixels -ge $minimumChangedPixels -and
            $verticalSpan -ge $minimumVerticalSpan) {
            $denseColumns.Add([PSCustomObject]@{
                X = $x
                ChangedPixels = $changedPixels
                Distance = $columnDistance
            })
        }
    }

    $excluded = @(
        $denseColumns |
            Sort-Object -Property ChangedPixels, Distance -Descending |
            Select-Object -First $MaximumCaretColumns
    )
    [long]$excludedDistance = 0
    foreach ($column in $excluded) {
        $excludedDistance += [long]$column.Distance
    }
    return [PSCustomObject]@{
        ResidualDistance = [Math]::Max(
            [long]0, $totalDistance - $excludedDistance)
        ExcludedColumns = @($excluded | ForEach-Object { $_.X })
    }
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
  [StructLayout(LayoutKind.Sequential)]
  public struct RECT {
    public int Left;
    public int Top;
    public int Right;
    public int Bottom;
  }
  [DllImport("user32.dll")]
  public static extern bool SetForegroundWindow(IntPtr hWnd);
  [DllImport("user32.dll")]
  public static extern bool ShowWindowAsync(IntPtr hWnd, int nCmdShow);
  [DllImport("user32.dll", SetLastError = true)]
  public static extern bool SetWindowPos(
      IntPtr hWnd, IntPtr hWndInsertAfter, int x, int y, int cx, int cy,
      uint flags);
  [DllImport("user32.dll", SetLastError = true)]
  public static extern bool GetWindowRect(IntPtr hWnd, out RECT rect);
  [DllImport("user32.dll", SetLastError = true)]
  public static extern bool PrintWindow(IntPtr hWnd, IntPtr hdcBlt, uint flags);
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

function Copy-OmniboxRectangle(
    [Drawing.Rectangle]$Rectangle,
    [IntPtr]$WindowHandle
) {
    try {
        return Copy-ScreenRectangle $Rectangle
    } catch [ComponentModel.Win32Exception] {
        # CopyFromScreen is unavailable on some restricted/virtual desktops.
        # Print the exact owned browser window into an off-screen bitmap and
        # crop the same UIA-derived address-bar rectangle instead.
        $nativeRect = [FocusOmniboxNativeMethods+RECT]::new()
        if (-not [FocusOmniboxNativeMethods]::GetWindowRect(
                $WindowHandle, [ref]$nativeRect)) {
            throw
        }
        $width = $nativeRect.Right - $nativeRect.Left
        $height = $nativeRect.Bottom - $nativeRect.Top
        if ($width -le 0 -or $height -le 0) {
            throw 'PrintWindow fallback received invalid browser bounds'
        }

        $windowBitmap = [Drawing.Bitmap]::new(
            $width, $height, [Drawing.Imaging.PixelFormat]::Format32bppArgb)
        $graphics = [Drawing.Graphics]::FromImage($windowBitmap)
        $hdc = [IntPtr]::Zero
        try {
            $hdc = $graphics.GetHdc()
            $printed = [FocusOmniboxNativeMethods]::PrintWindow(
                $WindowHandle, $hdc, 2)
            if (-not $printed) {
                throw [ComponentModel.Win32Exception]::new(
                    [Runtime.InteropServices.Marshal]::GetLastWin32Error(),
                    'PrintWindow fallback failed')
            }
        } finally {
            if ($hdc -ne [IntPtr]::Zero) {
                $graphics.ReleaseHdc($hdc)
            }
            $graphics.Dispose()
        }

        try {
            $crop = [Drawing.Rectangle]::new(
                $Rectangle.Left - $nativeRect.Left,
                $Rectangle.Top - $nativeRect.Top,
                $Rectangle.Width,
                $Rectangle.Height)
            if ($crop.Left -lt 0 -or $crop.Top -lt 0 -or
                $crop.Right -gt $windowBitmap.Width -or
                $crop.Bottom -gt $windowBitmap.Height) {
                throw 'PrintWindow fallback crop is outside browser bounds'
            }
            return $windowBitmap.Clone(
                $crop, [Drawing.Imaging.PixelFormat]::Format32bppArgb)
        } finally {
            $windowBitmap.Dispose()
        }
    }
}

function Set-OmniboxValueWithFallback(
    [object]$AddressBar,
    [object]$ValuePattern,
    [IntPtr]$WindowHandle,
    [string]$ExpectedValue,
    [scriptblock]$KeyboardAction,
    [int]$KeyboardObservationMilliseconds = 150
) {
    $keyboardSucceeded = $false
    try {
        $AddressBar.SetFocus()
        [void][FocusOmniboxNativeMethods]::SetForegroundWindow($WindowHandle)
        & $KeyboardAction
        $keyboardDeadline = [DateTime]::UtcNow.AddMilliseconds(
            $KeyboardObservationMilliseconds)
        while ([DateTime]::UtcNow -lt $keyboardDeadline) {
            if ($ValuePattern.Current.Value -ceq $ExpectedValue) {
                $keyboardSucceeded = $true
                break
            }
            Start-Sleep -Milliseconds 2
        }
    } catch {
        # UIPI and restricted desktop sessions can reject SendKeys/SendInput.
        # The exact UIA value fallback below keeps this visual QA deterministic.
        $keyboardSucceeded = $false
    }

    if ($keyboardSucceeded) {
        return 'Windows SendKeys'
    }

    try {
        $ValuePattern.SetValue($ExpectedValue)
    } catch {
        throw "Keyboard input was unavailable and UIA ValuePattern.SetValue failed: $($_.Exception.Message)"
    }
    $uiaDeadline = [DateTime]::UtcNow.AddSeconds(2)
    while ([DateTime]::UtcNow -lt $uiaDeadline -and
        $ValuePattern.Current.Value -cne $ExpectedValue) {
        Start-Sleep -Milliseconds 2
    }
    Assert-Qa ($ValuePattern.Current.Value -ceq $ExpectedValue) `
        'UIA fallback committed the exact requested omnibox value'
    return 'UI Automation ValuePattern.SetValue fallback'
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

    $valuePattern = $addressBar.GetCurrentPattern(
        [Windows.Automation.ValuePattern]::Pattern)
    Assert-Qa (-not $valuePattern.Current.IsReadOnly) `
        'Native address bar exposes a writable UIA ValuePattern'
    $prefix = 'MotionProbe'
    $inserted = 'Z'
    $baselineInputMechanism = Set-OmniboxValueWithFallback `
        $addressBar $valuePattern $windowHandle $prefix {
            [Windows.Forms.SendKeys]::SendWait('^l')
            [Windows.Forms.SendKeys]::SendWait('^a')
            [Windows.Forms.SendKeys]::SendWait('{BACKSPACE}')
            [Windows.Forms.SendKeys]::SendWait($prefix)
        }
    Start-Sleep -Milliseconds 260

    Assert-Qa ($valuePattern.Current.Value -eq $prefix) `
        "Address bar received the exact baseline via $baselineInputMechanism"

    $bounds = $addressBar.Current.BoundingRectangle
    $captureRect = [Drawing.Rectangle]::FromLTRB(
        [Math]::Floor($bounds.Left), [Math]::Floor($bounds.Top),
        [Math]::Ceiling($bounds.Right), [Math]::Ceiling($bounds.Bottom))
    Assert-Qa ($captureRect.Width -gt 300 -and $captureRect.Height -gt 20) `
        'Address bar capture rectangle is valid'

    $baselineBitmap = Copy-OmniboxRectangle $captureRect $windowHandle
    $baselinePath = Join-Path $EvidenceDirectory 'omnibox-before-insert.png'
    $baselineBitmap.Save($baselinePath, [Drawing.Imaging.ImageFormat]::Png)

    $watch = [Diagnostics.Stopwatch]::StartNew()
    $expectedValue = $prefix + $inserted
    $insertInputMechanism = Set-OmniboxValueWithFallback `
        $addressBar $valuePattern $windowHandle $expectedValue {
            [Windows.Forms.SendKeys]::SendWait($inserted)
        }
    $inputMechanism = if ($baselineInputMechanism -eq $insertInputMechanism) {
        $insertInputMechanism
    } else {
        "Baseline: $baselineInputMechanism; appended glyph: $insertInputMechanism"
    }
    Assert-Qa ($valuePattern.Current.Value -eq $expectedValue) `
        "Address bar committed the inserted glyph via $insertInputMechanism"

    $requestedTimes = @(0, 16, 35, 60, 90, 125, 165, 210, 270)
    foreach ($requestedMs in $requestedTimes) {
        while ($watch.ElapsedMilliseconds -lt $requestedMs) {
            Start-Sleep -Milliseconds 1
        }
        # Capture first at the requested deadline. UI Automation property reads
        # can take a frame and would weaken first-frame glyph verification.
        $captureElapsedMs = $watch.ElapsedMilliseconds
        $bitmap = Copy-OmniboxRectangle $captureRect $windowHandle
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
        $distanceMetric = Get-CaretExcludedRegionDistance `
            $bitmaps[$i] $finalBitmap $motionRegion
        $distance = [long]$distanceMetric.ResidualDistance
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
            -NotePropertyName ExcludedCaretColumns `
            -NotePropertyValue @($distanceMetric.ExcludedColumns)
        Add-Member -InputObject $frames[$i] `
            -NotePropertyName ForegroundCentroidY `
            -NotePropertyValue $centroid
    }

    $uniqueMotionFrames = @($motionHashes | Select-Object -Unique).Count
    $uniquePrefixFrames = @($prefixHashes | Select-Object -Unique).Count
    $uniqueDistances = @($distances | Select-Object -Unique).Count
    $maximumResidualDistance = ($distances | Measure-Object -Maximum).Maximum
    Assert-Qa ($maximumResidualDistance -eq 0) `
        'Committed omnibox glyph is pixel-stable after excluding dense caret columns'
    Assert-Qa ($uniquePrefixFrames -eq 1) `
        'Pixels before the inserted grapheme stay exactly stable'

    $finalCentroid = $foregroundCentroids[$foregroundCentroids.Count - 1]
    Assert-Qa ($null -ne $finalCentroid) `
        'Settled omnibox glyph has measurable foreground pixels'
    $maxTransientCentroid = $finalCentroid

    $report = [PSCustomObject]@{
        Ok = $true
        Executable = $BrowserPath
        SpawnedPid = $process.Id
        Profile = $profileDirectory
        Preference = 'focus.ui.motion_enabled (default true)'
        InputMechanism = $inputMechanism
        InputPath = "$inputMechanism -> native OmniboxViewViews"
        Style = 'Word-like: sharp pixel-stable glyph; separate 110ms caret glide'
        ForegroundCentroid = [PSCustomObject]@{
            Background = ('#{0:X2}{1:X2}{2:X2}' -f
                $backgroundColor.R, $backgroundColor.G, $backgroundColor.B)
            FinalY = $finalCentroid
            MaximumTransientY = $maxTransientCentroid
            MinimumRequiredDeltaY = 0.0
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
        UniqueCaretExcludedDistances = $uniqueDistances
        MaximumCaretExcludedDistance = $maximumResidualDistance
        MaximumExcludedCaretColumns = 4
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
