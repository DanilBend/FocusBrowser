# Copyright 2026 The Focus Browser Authors
#
# Runtime proof for Focus Browser's native omnibox edit motion. This script is
# deliberately self-contained and owns every process/profile that it creates.
# It never reads or writes the OS clipboard: the multi-grapheme case is queued
# as one UTF-16 SendInput batch, including a ZWJ emoji sequence.

[CmdletBinding()]
param(
    [string]$BrowserPath,
    [string]$EvidenceDirectory,
    [ValidateRange(10, 90)]
    [int]$StartupTimeoutSeconds = 35,
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
if ([string]::IsNullOrWhiteSpace($EvidenceDirectory)) {
    $EvidenceDirectory = Join-Path ([IO.Path]::GetTempPath()) `
        "FocusBrowser-QA-Omnibox-Edit-Caret-Evidence-$runId"
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
        $leaf.StartsWith('FocusBrowser-QA-Omnibox-', [StringComparison]::Ordinal)
    ) 'Disposable profile is an exact Focus Browser QA child of TEMP'
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

function Get-RegionHash(
    [Drawing.Bitmap]$Bitmap,
    [Drawing.Rectangle]$Region
) {
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

function Get-DifferenceBounds(
    [Drawing.Bitmap]$Before,
    [Drawing.Bitmap]$After,
    [int]$Threshold = 18
) {
    if ($Before.Width -ne $After.Width -or $Before.Height -ne $After.Height) {
        throw 'Compared captures must have identical dimensions.'
    }
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

function Wait-CaptureDeadline(
    [Diagnostics.Stopwatch]$Watch,
    [int]$TargetMilliseconds
) {
    while ($Watch.Elapsed.TotalMilliseconds -lt $TargetMilliseconds) {
        $remaining = $TargetMilliseconds - $Watch.Elapsed.TotalMilliseconds
        if ($remaining -gt 3.0) {
            [Threading.Thread]::Sleep(1)
        } else {
            [Threading.Thread]::SpinWait(160)
        }
    }
}

Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;

public static class FocusOmniboxNativeMethods {
  [StructLayout(LayoutKind.Sequential)]
  private struct INPUT {
    public UInt32 type;
    public INPUTUNION data;
  }

  [StructLayout(LayoutKind.Explicit)]
  private struct INPUTUNION {
    [FieldOffset(0)] public KEYBDINPUT keyboard;
    // SendInput validates sizeof(INPUT), which is governed by the largest
    // native union member. MOUSEINPUT is 32 bytes on x64 (KEYBDINPUT is 24).
    [FieldOffset(0)] public MOUSEINPUT mouse;
  }

  [StructLayout(LayoutKind.Sequential)]
  private struct KEYBDINPUT {
    public UInt16 virtualKey;
    public UInt16 scanCode;
    public UInt32 flags;
    public UInt32 time;
    public UIntPtr extraInfo;
  }

  [StructLayout(LayoutKind.Sequential)]
  private struct MOUSEINPUT {
    public Int32 dx;
    public Int32 dy;
    public UInt32 mouseData;
    public UInt32 flags;
    public UInt32 time;
    public UIntPtr extraInfo;
  }

  private const UInt32 INPUT_KEYBOARD = 1;
  private const UInt32 KEYEVENTF_KEYUP = 0x0002;
  private const UInt32 KEYEVENTF_UNICODE = 0x0004;

  [DllImport("user32.dll", SetLastError = true)]
  private static extern UInt32 SendInput(
      UInt32 inputCount, INPUT[] inputs, Int32 inputSize);

  [DllImport("user32.dll")]
  public static extern bool SetForegroundWindow(IntPtr window);

  [DllImport("user32.dll")]
  public static extern bool ShowWindowAsync(IntPtr window, int command);

  [DllImport("user32.dll", SetLastError = true)]
  public static extern bool SetWindowPos(
      IntPtr window, IntPtr insertAfter, int x, int y, int width, int height,
      UInt32 flags);

  [DllImport("shcore.dll")]
  public static extern int SetProcessDpiAwareness(int value);

  private static INPUT VirtualKeyInput(UInt16 key, bool keyUp) {
    INPUT input = new INPUT();
    input.type = INPUT_KEYBOARD;
    input.data.keyboard.virtualKey = key;
    input.data.keyboard.flags = keyUp ? KEYEVENTF_KEYUP : 0;
    return input;
  }

  private static INPUT UnicodeInput(char codeUnit, bool keyUp) {
    INPUT input = new INPUT();
    input.type = INPUT_KEYBOARD;
    input.data.keyboard.scanCode = codeUnit;
    input.data.keyboard.flags = KEYEVENTF_UNICODE |
                                (keyUp ? KEYEVENTF_KEYUP : 0);
    return input;
  }

  private static void Send(INPUT[] inputs) {
    UInt32 sent = SendInput(
        (UInt32)inputs.Length, inputs, Marshal.SizeOf(typeof(INPUT)));
    if (sent != inputs.Length) {
      throw new InvalidOperationException(
          "SendInput queued " + sent + " of " + inputs.Length +
          " events; Win32=" + Marshal.GetLastWin32Error());
    }
  }

  public static void SendVirtualKey(UInt16 key) {
    Send(new INPUT[] {
      VirtualKeyInput(key, false), VirtualKeyInput(key, true)
    });
  }

  public static void SendChord(UInt16 modifier, UInt16 key) {
    Send(new INPUT[] {
      VirtualKeyInput(modifier, false), VirtualKeyInput(key, false),
      VirtualKeyInput(key, true), VirtualKeyInput(modifier, true)
    });
  }

  // One native SendInput call for the full UTF-16 payload. Surrogate pairs and
  // ZWJ are intentionally preserved as code units in the same queued batch.
  public static void SendUnicodeBatch(string text) {
    List<INPUT> inputs = new List<INPUT>(text.Length * 2);
    foreach (char codeUnit in text) {
      inputs.Add(UnicodeInput(codeUnit, false));
      inputs.Add(UnicodeInput(codeUnit, true));
    }
    Send(inputs.ToArray());
  }
}
'@

try {
    [void][FocusOmniboxNativeMethods]::SetProcessDpiAwareness(2)
} catch {
    # The host may already have a DPI mode. Existing awareness remains valid.
}

function Start-QaBrowser(
    [string]$ProfileDirectory,
    [bool]$ReducedMotion
) {
    Assert-SafeProfilePath $ProfileDirectory
    New-Item -ItemType Directory -Path $ProfileDirectory -Force | Out-Null

    $arguments = @(
        "--user-data-dir=`"$ProfileDirectory`""
        '--no-first-run'
        '--no-default-browser-check'
        '--disable-background-networking'
        '--disable-component-update'
        '--disable-search-engine-choice-screen'
        '--window-size=1400,900'
    )
    if ($ReducedMotion) {
        $arguments += '--force-prefers-reduced-motion'
    }
    $arguments += 'chrome://newtab/'

    $startInfo = [Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $BrowserPath
    $startInfo.Arguments = $arguments -join ' '
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
    Start-Sleep -Milliseconds 450

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
    $bounds = $addressBar.Current.BoundingRectangle
    $captureRect = [Drawing.Rectangle]::FromLTRB(
        [Math]::Floor($bounds.Left), [Math]::Floor($bounds.Top),
        [Math]::Ceiling($bounds.Right), [Math]::Ceiling($bounds.Bottom))
    Assert-Qa ($captureRect.Width -gt 300 -and $captureRect.Height -gt 20) `
        'Address bar capture rectangle is valid'

    return [PSCustomObject]@{
        Process = $process
        WindowHandle = $windowHandle
        AddressBar = $addressBar
        ValuePattern = $valuePattern
        Bounds = $bounds
        CaptureRect = $captureRect
        Profile = $ProfileDirectory
        ReducedMotion = $ReducedMotion
        Stopped = $false
    }
}

function Stop-QaBrowser([object]$Context) {
    if ($null -eq $Context -or $Context.Stopped) {
        return
    }
    $Context.Stopped = $true
    if ($null -ne $Context.Process) {
        try { $Context.Process.Refresh() } catch {}
        try {
            if (-not $Context.Process.HasExited) {
                # Exact owned root PID and its child tree only; never image-name.
                & taskkill.exe /PID $Context.Process.Id /T /F 2>$null | Out-Null
            }
        } catch {}
        try { $Context.Process.Dispose() } catch {}
    }
    if (-not $KeepProfile -and
        (Test-Path -LiteralPath $Context.Profile -PathType Container)) {
        Assert-SafeProfilePath $Context.Profile
        Remove-Item -LiteralPath $Context.Profile -Recurse -Force `
            -ErrorAction SilentlyContinue
    }
}

function Focus-AddressBar([object]$Context) {
    [void][FocusOmniboxNativeMethods]::SetForegroundWindow($Context.WindowHandle)
    $Context.AddressBar.SetFocus()
    [void][FocusOmniboxNativeMethods]::SendChord([uint16]0x11, [uint16]0x4c)
    Start-Sleep -Milliseconds 35
}

function Wait-OmniboxValue(
    [object]$Context,
    [string]$Expected,
    [int]$TimeoutMilliseconds = 2500
) {
    $deadline = [DateTime]::UtcNow.AddMilliseconds($TimeoutMilliseconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        if ($Context.ValuePattern.Current.Value -ceq $Expected) {
            return $true
        }
        Start-Sleep -Milliseconds 5
    }
    return ($Context.ValuePattern.Current.Value -ceq $Expected)
}

function Set-OmniboxBaseline(
    [object]$Context,
    [string]$Text
) {
    Focus-AddressBar $Context
    [void][FocusOmniboxNativeMethods]::SendChord([uint16]0x11, [uint16]0x41)
    [void][FocusOmniboxNativeMethods]::SendVirtualKey([uint16]0x08)
    if ($Text.Length -gt 0) {
        [void][FocusOmniboxNativeMethods]::SendUnicodeBatch($Text)
    }
    Assert-Qa (Wait-OmniboxValue $Context $Text) `
        "Address bar received exact baseline '$Text'"
    Start-Sleep -Milliseconds 280
}

function Capture-EditAction(
    [object]$Context,
    [string]$Directory,
    [scriptblock]$Action
) {
    New-Item -ItemType Directory -Path $Directory -Force | Out-Null
    $before = Copy-ScreenRectangle $Context.CaptureRect
    $beforePath = Join-Path $Directory 'before.png'
    $before.Save($beforePath, [Drawing.Imaging.ImageFormat]::Png)

    $requestedTimes = @(0, 8, 16, 24, 36, 52, 72, 96, 128, 168, 216, 280, 360)
    $bitmaps = [Collections.Generic.List[Drawing.Bitmap]]::new()
    $frames = [Collections.Generic.List[object]]::new()
    $watch = [Diagnostics.Stopwatch]::StartNew()
    & $Action
    foreach ($requestedMs in $requestedTimes) {
        Wait-CaptureDeadline $watch $requestedMs
        $elapsedMs = [Math]::Round($watch.Elapsed.TotalMilliseconds, 3)
        $bitmap = Copy-ScreenRectangle $Context.CaptureRect
        $bitmaps.Add($bitmap)
        $index = $bitmaps.Count - 1
        $elapsedName = [int][Math]::Round($elapsedMs)
        $framePath = Join-Path $Directory `
            ('frame-{0:D2}-{1:D4}ms.png' -f $index, $elapsedName)
        $bitmap.Save($framePath, [Drawing.Imaging.ImageFormat]::Png)
        $frames.Add([PSCustomObject]@{
            Index = $index
            RequestedMs = $requestedMs
            ElapsedMs = $elapsedMs
            Path = $framePath
            Hash = Get-BitmapHash $bitmap
        })
    }
    return [PSCustomObject]@{
        BeforeBitmap = $before
        BeforePath = $beforePath
        Bitmaps = $bitmaps
        Frames = $frames
    }
}

function Dispose-CaptureSet([object]$Capture) {
    if ($null -eq $Capture) { return }
    if ($null -ne $Capture.BeforeBitmap) {
        $Capture.BeforeBitmap.Dispose()
    }
    foreach ($bitmap in $Capture.Bitmaps) {
        if ($null -ne $bitmap) { $bitmap.Dispose() }
    }
}

function Analyze-EditCapture(
    [object]$Capture,
    [int]$LeftExpansion = 4
) {
    $finalBitmap = $Capture.Bitmaps[$Capture.Bitmaps.Count - 1]
    $differenceBounds = Get-DifferenceBounds $Capture.BeforeBitmap $finalBitmap
    if ($null -eq $differenceBounds) {
        return [PSCustomObject]@{ Valid = $false; Reason = 'No final pixel difference' }
    }

    $motionRegion = [Drawing.Rectangle]::FromLTRB(
        [Math]::Max(0, $differenceBounds.Left - $LeftExpansion),
        [Math]::Max(0, $differenceBounds.Top - 4),
        [Math]::Min($finalBitmap.Width, $differenceBounds.Right + 5),
        [Math]::Min($finalBitmap.Height, $differenceBounds.Bottom + 5))
    $prefixRegion = [Drawing.Rectangle]::FromLTRB(
        0, 0, [Math]::Max(1, $motionRegion.Left - 2), $finalBitmap.Height)
    $backgroundColor = Get-MostFrequentRegionColor $finalBitmap $motionRegion

    $motionHashes = [Collections.Generic.List[string]]::new()
    $prefixHashes = [Collections.Generic.List[string]]::new()
    $distanceToFinal = [Collections.Generic.List[long]]::new()
    $distanceFromBefore = [Collections.Generic.List[long]]::new()
    $centroids = [Collections.Generic.List[object]]::new()
    for ($i = 0; $i -lt $Capture.Bitmaps.Count; $i++) {
        $bitmap = $Capture.Bitmaps[$i]
        $motionHash = Get-RegionHash $bitmap $motionRegion
        $prefixHash = Get-RegionHash $bitmap $prefixRegion
        $toFinal = Get-RegionDistance $bitmap $finalBitmap $motionRegion
        $fromBefore = Get-RegionDistance $bitmap $Capture.BeforeBitmap $motionRegion
        $centroid = Get-ForegroundVerticalCentroid `
            $bitmap $motionRegion $backgroundColor
        $motionHashes.Add($motionHash)
        $prefixHashes.Add($prefixHash)
        $distanceToFinal.Add($toFinal)
        $distanceFromBefore.Add($fromBefore)
        $centroids.Add($centroid)
        Add-Member -InputObject $Capture.Frames[$i] `
            -NotePropertyName MotionHash -NotePropertyValue $motionHash
        Add-Member -InputObject $Capture.Frames[$i] `
            -NotePropertyName PrefixHash -NotePropertyValue $prefixHash
        Add-Member -InputObject $Capture.Frames[$i] `
            -NotePropertyName DistanceToFinal -NotePropertyValue $toFinal
        Add-Member -InputObject $Capture.Frames[$i] `
            -NotePropertyName DistanceFromBefore -NotePropertyValue $fromBefore
        Add-Member -InputObject $Capture.Frames[$i] `
            -NotePropertyName ForegroundCentroidY -NotePropertyValue $centroid
    }

    $firstChangedIndex = -1
    for ($i = 0; $i -lt $distanceFromBefore.Count; $i++) {
        if ($distanceFromBefore[$i] -gt 0) {
            $firstChangedIndex = $i
            break
        }
    }
    $finalCentroid = $centroids[$centroids.Count - 1]
    $transientCentroids = [Collections.Generic.List[double]]::new()
    for ($i = 0; $i -lt ($centroids.Count - 2); $i++) {
        if ($null -ne $centroids[$i] -and
            $distanceFromBefore[$i] -gt 0 -and $distanceToFinal[$i] -gt 0) {
            $transientCentroids.Add([double]$centroids[$i])
        }
    }
    $maximumTransientCentroid = $null
    $centroidDelta = 0.0
    if ($transientCentroids.Count -gt 0 -and $null -ne $finalCentroid) {
        $maximumTransientCentroid = (
            $transientCentroids | Measure-Object -Maximum).Maximum
        $centroidDelta = [double]$maximumTransientCentroid - [double]$finalCentroid
    }

    $postCommitHashes = @()
    if ($firstChangedIndex -ge 0) {
        $postCommitHashes = @(
            $motionHashes | Select-Object -Skip $firstChangedIndex |
                Select-Object -Unique)
    }

    return [PSCustomObject]@{
        Valid = $true
        DifferenceBounds = [PSCustomObject]@{
            Left = $differenceBounds.Left; Top = $differenceBounds.Top
            Width = $differenceBounds.Width; Height = $differenceBounds.Height
        }
        MotionRegion = [PSCustomObject]@{
            Left = $motionRegion.Left; Top = $motionRegion.Top
            Width = $motionRegion.Width; Height = $motionRegion.Height
        }
        Background = ('#{0:X2}{1:X2}{2:X2}' -f
            $backgroundColor.R, $backgroundColor.G, $backgroundColor.B)
        UniqueMotionFrames = @($motionHashes | Select-Object -Unique).Count
        UniquePrefixFrames = @($prefixHashes | Select-Object -Unique).Count
        UniqueDistancesToFinal = @($distanceToFinal | Select-Object -Unique).Count
        UniquePostCommitFrames = $postCommitHashes.Count
        FinalStable = (
            $motionHashes[$motionHashes.Count - 1] -eq
            $motionHashes[$motionHashes.Count - 2])
        FirstChangedIndex = $firstChangedIndex
        FirstChangedElapsedMs = if ($firstChangedIndex -ge 0) {
            $Capture.Frames[$firstChangedIndex].ElapsedMs
        } else { $null }
        FinalCentroidY = $finalCentroid
        MaximumTransientCentroidY = $maximumTransientCentroid
        TransientCentroidDeltaY = $centroidDelta
        Frames = $Capture.Frames
    }
}

function Test-ContextGeometry([object]$Context) {
    $currentBounds = $Context.AddressBar.Current.BoundingRectangle
    return (
        [Math]::Abs($currentBounds.Left - $Context.Bounds.Left) -lt 0.5 -and
        [Math]::Abs($currentBounds.Top - $Context.Bounds.Top) -lt 0.5 -and
        [Math]::Abs($currentBounds.Width - $Context.Bounds.Width) -lt 0.5 -and
        [Math]::Abs($currentBounds.Height - $Context.Bounds.Height) -lt 0.5)
}

function Invoke-EditScenario(
    [object]$Context,
    [string]$Name,
    [string]$Baseline,
    [string]$Expected,
    [scriptblock]$Prepare,
    [scriptblock]$Action,
    [int]$MinimumUniqueMotionFrames,
    [bool]$RequireTranslateY,
    [bool]$RequireStatic,
    [int]$LeftExpansion = 4,
    [int]$MaximumAttempts = 1
) {
    $scenarioRoot = Join-Path $EvidenceDirectory $Name
    New-Item -ItemType Directory -Path $scenarioRoot -Force | Out-Null
    $chosen = $null
    for ($attempt = 1; $attempt -le $MaximumAttempts; $attempt++) {
        Set-OmniboxBaseline $Context $Baseline
        & $Prepare
        Start-Sleep -Milliseconds 150
        $attemptDirectory = Join-Path $scenarioRoot ("attempt-$attempt")
        $capture = $null
        try {
            $capture = Capture-EditAction $Context $attemptDirectory $Action
            $valueOk = Wait-OmniboxValue $Context $Expected
            $geometryOk = Test-ContextGeometry $Context
            $metrics = Analyze-EditCapture $capture $LeftExpansion
            $candidate = [PSCustomObject]@{
                Name = $Name
                Attempt = $attempt
                Baseline = $Baseline
                Expected = $Expected
                Actual = $Context.ValuePattern.Current.Value
                ValueExact = $valueOk
                GeometryStable = $geometryOk
                Metrics = $metrics
            }

            $candidateOk = $valueOk -and $geometryOk -and $metrics.Valid -and
                ($metrics.UniquePrefixFrames -eq 1) -and $metrics.FinalStable
            if ($RequireStatic) {
                $candidateOk = $candidateOk -and
                    ($metrics.UniquePostCommitFrames -le 2) -and
                    ([Math]::Abs([double]$metrics.TransientCentroidDeltaY) -lt 0.20)
            } else {
                $candidateOk = $candidateOk -and
                    ($metrics.UniqueMotionFrames -ge $MinimumUniqueMotionFrames)
                if ($RequireTranslateY) {
                    $candidateOk = $candidateOk -and
                        ($metrics.TransientCentroidDeltaY -ge 0.20) -and
                        ($null -ne $metrics.FirstChangedElapsedMs) -and
                        ($metrics.FirstChangedElapsedMs -le 160)
                }
            }
            if ($candidateOk) {
                $chosen = $candidate
                break
            }
            $chosen = $candidate
        } finally {
            Dispose-CaptureSet $capture
        }
    }

    Assert-Qa ($chosen.ValueExact) "$Name produced the exact expected native omnibox value"
    Assert-Qa ($chosen.GeometryStable) "$Name kept address-bar geometry fixed"
    Assert-Qa ($chosen.Metrics.Valid) "$Name changed a bounded native omnibox pixel region"
    Assert-Qa ($chosen.Metrics.UniquePrefixFrames -eq 1) `
        "$Name left the unaffected prefix pixels exactly stable"
    Assert-Qa ($chosen.Metrics.FinalStable) "$Name settled to identical final frames"
    if ($RequireStatic) {
        Assert-Qa ($chosen.Metrics.UniquePostCommitFrames -le 2) `
            "$Name has no multi-frame glyph transition under reduced motion"
        Assert-Qa ([Math]::Abs([double]$chosen.Metrics.TransientCentroidDeltaY) -lt 0.20) `
            "$Name has no translated transient glyph under reduced motion"
    } else {
        Assert-Qa (
            $chosen.Metrics.UniqueMotionFrames -ge $MinimumUniqueMotionFrames
        ) "$Name exposes multiple native edit-motion frames"
        if ($RequireTranslateY) {
            Assert-Qa ($chosen.Metrics.FirstChangedElapsedMs -le 160) `
                "$Name captured the committed glyph before the settle ended"
            Assert-Qa ($chosen.Metrics.TransientCentroidDeltaY -ge 0.20) `
                "$Name proves non-fade translateY through foreground centroid movement"
        }
    }
    return $chosen
}

function Get-CaretEndpoints(
    [Drawing.Bitmap]$FromBitmap,
    [Drawing.Bitmap]$TargetBitmap
) {
    $differenceBounds = Get-DifferenceBounds $FromBitmap $TargetBitmap 12
    if ($null -eq $differenceBounds -or $differenceBounds.Width -lt 12) {
        return [PSCustomObject]@{ Valid = $false }
    }
    $midpoint = ($differenceBounds.Left + $differenceBounds.Right) / 2.0
    [double]$leftWeightedX = 0
    [double]$leftWeight = 0
    [double]$rightWeightedX = 0
    [double]$rightWeight = 0
    for ($x = $differenceBounds.Left; $x -lt $differenceBounds.Right; $x++) {
        [double]$columnWeight = 0
        for ($y = $differenceBounds.Top; $y -lt $differenceBounds.Bottom; $y++) {
            $a = $FromBitmap.GetPixel($x, $y)
            $b = $TargetBitmap.GetPixel($x, $y)
            $delta = [Math]::Abs([int]$a.R - [int]$b.R) +
                [Math]::Abs([int]$a.G - [int]$b.G) +
                [Math]::Abs([int]$a.B - [int]$b.B)
            if ($delta -gt 12) { $columnWeight += $delta }
        }
        if ($x -lt $midpoint) {
            $leftWeightedX += ($x + 0.5) * $columnWeight
            $leftWeight += $columnWeight
        } else {
            $rightWeightedX += ($x + 0.5) * $columnWeight
            $rightWeight += $columnWeight
        }
    }
    if ($leftWeight -le 0 -or $rightWeight -le 0) {
        return [PSCustomObject]@{ Valid = $false }
    }
    return [PSCustomObject]@{
        Valid = $true
        TargetX = $leftWeightedX / $leftWeight
        FromX = $rightWeightedX / $rightWeight
        DifferenceBounds = [PSCustomObject]@{
            Left = $differenceBounds.Left; Top = $differenceBounds.Top
            Width = $differenceBounds.Width; Height = $differenceBounds.Height
        }
    }
}

function Find-IntermediateCaretX(
    [Drawing.Bitmap]$Bitmap,
    [Drawing.Color]$Background,
    [object]$Endpoints
) {
    $left = [Math]::Ceiling($Endpoints.TargetX + 3.0)
    $right = [Math]::Floor($Endpoints.FromX - 3.0)
    $top = [Math]::Max(0, $Endpoints.DifferenceBounds.Top - 2)
    $bottom = [Math]::Min(
        $Bitmap.Height, $Endpoints.DifferenceBounds.Top +
        $Endpoints.DifferenceBounds.Height + 2)
    if ($right -le $left -or $bottom -le $top) { return $null }

    $signals = [Collections.Generic.List[object]]::new()
    for ($x = $left; $x -le $right; $x++) {
        [double]$score = 0
        [int]$foregroundPixels = 0
        for ($y = $top; $y -lt $bottom; $y++) {
            $pixel = $Bitmap.GetPixel($x, $y)
            $distance = [Math]::Abs([int]$pixel.R - [int]$Background.R) +
                [Math]::Abs([int]$pixel.G - [int]$Background.G) +
                [Math]::Abs([int]$pixel.B - [int]$Background.B)
            if ($distance -gt 30) {
                $score += ($distance - 30)
                $foregroundPixels++
            }
        }
        $signals.Add([PSCustomObject]@{
            X = $x + 0.5; Score = $score; ForegroundPixels = $foregroundPixels
        })
    }
    $best = $signals | Sort-Object Score -Descending | Select-Object -First 1
    $sortedScores = @($signals.Score | Sort-Object)
    $median = [double]$sortedScores[[Math]::Floor($sortedScores.Count / 2)]
    $broadColumns = @(
        $signals | Where-Object {
            $_.Score -gt 0 -and $_.Score -ge ($best.Score * 0.55)
        }).Count
    $maximumBroadColumns = [Math]::Max(7, [Math]::Floor($signals.Count * 0.14))
    $isNarrowCaret = $best.ForegroundPixels -ge 7 -and
        $best.Score -ge ($median * 2.0 + 450) -and
        $broadColumns -le $maximumBroadColumns
    if (-not $isNarrowCaret) { return $null }
    return [PSCustomObject]@{
        X = $best.X
        Score = $best.Score
        ForegroundPixels = $best.ForegroundPixels
        MedianColumnScore = $median
        BroadColumns = $broadColumns
    }
}

function Invoke-CaretScenario(
    [object]$Context,
    [string]$Name,
    [bool]$ExpectIntermediate,
    [int]$MaximumAttempts = 3
) {
    $scenarioRoot = Join-Path $EvidenceDirectory $Name
    New-Item -ItemType Directory -Path $scenarioRoot -Force | Out-Null
    $selected = $null
    $caretText = 'M' + ('.' * 20)

    for ($attempt = 1; $attempt -le $MaximumAttempts; $attempt++) {
        $attemptDirectory = Join-Path $scenarioRoot ("attempt-$attempt")
        New-Item -ItemType Directory -Path $attemptDirectory -Force | Out-Null
        Set-OmniboxBaseline $Context $caretText
        [void][FocusOmniboxNativeMethods]::SendVirtualKey([uint16]0x23) # End
        Start-Sleep -Milliseconds 130
        $fromBitmap = Copy-ScreenRectangle $Context.CaptureRect
        $fromPath = Join-Path $attemptDirectory 'caret-from-end.png'
        $fromBitmap.Save($fromPath, [Drawing.Imaging.ImageFormat]::Png)

        [void][FocusOmniboxNativeMethods]::SendVirtualKey([uint16]0x24) # Home
        [void][FocusOmniboxNativeMethods]::SendVirtualKey([uint16]0x27) # Right
        Start-Sleep -Milliseconds 130
        $targetBitmap = Copy-ScreenRectangle $Context.CaptureRect
        $targetPath = Join-Path $attemptDirectory 'caret-target-reference.png'
        $targetBitmap.Save($targetPath, [Drawing.Imaging.ImageFormat]::Png)
        $endpoints = Get-CaretEndpoints $fromBitmap $targetBitmap

        # Put the selection focus back at the old endpoint, then select the
        # entire tail. Deleting it is one user edit, so the caret layer must
        # travel from the old endpoint to the target while the tail vanishes.
        [void][FocusOmniboxNativeMethods]::SendVirtualKey([uint16]0x23) # End
        [void][FocusOmniboxNativeMethods]::SendVirtualKey([uint16]0x24) # Home
        [void][FocusOmniboxNativeMethods]::SendVirtualKey([uint16]0x27) # Right
        [void][FocusOmniboxNativeMethods]::SendChord([uint16]0x10, [uint16]0x23) # Shift+End
        Start-Sleep -Milliseconds 45

        $capture = $null
        try {
            $capture = Capture-EditAction $Context $attemptDirectory {
                [void][FocusOmniboxNativeMethods]::SendVirtualKey([uint16]0x08)
            }
            $valueOk = Wait-OmniboxValue $Context 'M'
            $finalBitmap = $capture.Bitmaps[$capture.Bitmaps.Count - 1]
            $scanRegion = [Drawing.Rectangle]::FromLTRB(
                [Math]::Max(0, [Math]::Floor($endpoints.TargetX) - 4),
                [Math]::Max(0, $endpoints.DifferenceBounds.Top - 3),
                [Math]::Min($finalBitmap.Width, [Math]::Ceiling($endpoints.FromX) + 4),
                [Math]::Min($finalBitmap.Height,
                    $endpoints.DifferenceBounds.Top +
                    $endpoints.DifferenceBounds.Height + 3))
            $background = Get-MostFrequentRegionColor $finalBitmap $scanRegion
            $detections = [Collections.Generic.List[object]]::new()
            for ($i = 0; $i -lt $capture.Bitmaps.Count; $i++) {
                $candidate = Find-IntermediateCaretX `
                    $capture.Bitmaps[$i] $background $endpoints
                if ($null -ne $candidate) {
                    $detections.Add([PSCustomObject]@{
                        FrameIndex = $i
                        ElapsedMs = $capture.Frames[$i].ElapsedMs
                        X = $candidate.X
                        Score = $candidate.Score
                        ForegroundPixels = $candidate.ForegroundPixels
                        MedianColumnScore = $candidate.MedianColumnScore
                        BroadColumns = $candidate.BroadColumns
                    })
                }
            }
            $uniqueIntermediateXs = @(
                $detections | ForEach-Object { [Math]::Round($_.X, 1) } |
                    Select-Object -Unique)
            $selected = [PSCustomObject]@{
                Name = $Name
                Attempt = $attempt
                ValueExact = $valueOk
                GeometryStable = Test-ContextGeometry $Context
                EndpointReferenceValid = $endpoints.Valid
                FromX = $endpoints.FromX
                TargetX = $endpoints.TargetX
                EndpointSeparation = $endpoints.FromX - $endpoints.TargetX
                IntermediateDetections = $detections
                UniqueIntermediateXs = $uniqueIntermediateXs
                Frames = $capture.Frames
                FromReference = $fromPath
                TargetReference = $targetPath
            }
            $pass = $valueOk -and $selected.GeometryStable -and
                $endpoints.Valid -and ($selected.EndpointSeparation -gt 30)
            if ($ExpectIntermediate) {
                $pass = $pass -and ($uniqueIntermediateXs.Count -ge 1)
            } else {
                $pass = $pass -and ($uniqueIntermediateXs.Count -eq 0)
            }
            if ($pass) { break }
        } finally {
            Dispose-CaptureSet $capture
            $fromBitmap.Dispose()
            $targetBitmap.Dispose()
        }
    }

    Assert-Qa ($selected.ValueExact) "$Name produced the exact caret target value"
    Assert-Qa ($selected.GeometryStable) "$Name kept address-bar geometry fixed"
    Assert-Qa ($selected.EndpointReferenceValid -and
        $selected.EndpointSeparation -gt 30) `
        "$Name measured distinct caret endpoints"
    if ($ExpectIntermediate) {
        Assert-Qa ($selected.UniqueIntermediateXs.Count -ge 1) `
            "$Name proves the compositor caret occupied an intermediate X"
    } else {
        Assert-Qa ($selected.UniqueIntermediateXs.Count -eq 0) `
            "$Name proves reduced motion snaps the caret without intermediate X"
    }
    return $selected
}

New-Item -ItemType Directory -Path $EvidenceDirectory -Force | Out-Null
$normalProfile = Join-Path ([IO.Path]::GetTempPath()) `
    "FocusBrowser-QA-Omnibox-$runId-normal"
$reducedProfile = Join-Path ([IO.Path]::GetTempPath()) `
    "FocusBrowser-QA-Omnibox-$runId-reduced"
$normalContext = $null
$reducedContext = $null

try {
    $normalContext = Start-QaBrowser $normalProfile $false
    $ordinary = Invoke-EditScenario $normalContext 'ordinary-character' `
        'MotionProbe' 'MotionProbeZ' {} {
            [void][FocusOmniboxNativeMethods]::SendUnicodeBatch('Z')
        } 3 $true $false 4 3

    $zwjEmoji = [char]::ConvertFromUtf32(0x1F469) +
        [string]([char]0x200D) + [char]::ConvertFromUtf32(0x1F4BB)
    $zwjPayload = 'A' + $zwjEmoji + 'B'
    $zwjPaste = Invoke-EditScenario $normalContext 'zwj-multigrapheme-batch' `
        'Batch:' ('Batch:' + $zwjPayload) {} {
            [void][FocusOmniboxNativeMethods]::SendUnicodeBatch($zwjPayload)
        } 3 $false $false 5 2

    $backspace = Invoke-EditScenario $normalContext 'backspace' `
        'EraseXYZ' 'EraseXZ' {
            [void][FocusOmniboxNativeMethods]::SendVirtualKey([uint16]0x25)
        } {
            [void][FocusOmniboxNativeMethods]::SendVirtualKey([uint16]0x08)
        } 2 $false $false 14 2

    $delete = Invoke-EditScenario $normalContext 'delete' `
        'EraseXYZ' 'EraseXZ' {
            [void][FocusOmniboxNativeMethods]::SendVirtualKey([uint16]0x25)
            [void][FocusOmniboxNativeMethods]::SendVirtualKey([uint16]0x25)
        } {
            [void][FocusOmniboxNativeMethods]::SendVirtualKey([uint16]0x2e)
        } 2 $false $false 14 2

    $caret = Invoke-CaretScenario $normalContext 'caret-glide' $true 3
    $normalPid = $normalContext.Process.Id
    Stop-QaBrowser $normalContext

    $reducedContext = Start-QaBrowser $reducedProfile $true
    $reducedGlyph = Invoke-EditScenario $reducedContext 'reduced-motion-glyph' `
        'ReducedProbe' 'ReducedProbeR' {} {
            [void][FocusOmniboxNativeMethods]::SendUnicodeBatch('R')
        } 1 $false $true 4 2
    $reducedCaret = Invoke-CaretScenario `
        $reducedContext 'reduced-motion-caret' $false 2
    $reducedPid = $reducedContext.Process.Id

    $report = [PSCustomObject]@{
        Ok = $true
        Executable = $BrowserPath
        ExecutableSha256 = (Get-FileHash -LiteralPath $BrowserPath -Algorithm SHA256).Hash.ToLowerInvariant()
        EvidenceDirectory = $EvidenceDirectory
        ProcessOwnership = [PSCustomObject]@{
            NormalRootPid = $normalPid
            ReducedMotionRootPid = $reducedPid
            Cleanup = 'Exact spawned root PID + child tree; never image-name'
            NormalProfile = $normalProfile
            ReducedMotionProfile = $reducedProfile
        }
        Clipboard = [PSCustomObject]@{
            Touched = $false
            MultiGraphemeTransport = 'one Win32 SendInput UTF-16 batch'
            ZWJIncluded = $true
            PayloadCodeUnits = $zwjPayload.Length
            PayloadTextElements = 3
        }
        MotionContract = [PSCustomObject]@{
            OrdinaryCharacter = 'PASS'
            ZWJMultiGraphemeBatch = 'PASS'
            Backspace = 'PASS'
            Delete = 'PASS'
            CaretIntermediateX = 'PASS'
            GlyphNonFadeTranslateYCentroid = 'PASS'
            ReducedMotionGlyph = 'PASS'
            ReducedMotionCaret = 'PASS'
        }
        OrdinaryCharacter = $ordinary
        ZWJMultiGraphemeBatch = $zwjPaste
        Backspace = $backspace
        Delete = $delete
        CaretGlide = $caret
        GlyphNonFade = [PSCustomObject]@{
            Method = 'foreground vertical centroid in bounded changed-glyph region'
            ToleratesLateFirstFrame = $true
            MinimumDeltaY = 0.20
            ObservedDeltaY = $ordinary.Metrics.TransientCentroidDeltaY
            FirstChangedElapsedMs = $ordinary.Metrics.FirstChangedElapsedMs
        }
        ReducedMotion = [PSCustomObject]@{
            CommandLine = '--force-prefers-reduced-motion'
            Glyph = $reducedGlyph
            Caret = $reducedCaret
        }
    }
    $reportPath = Join-Path $EvidenceDirectory 'omnibox-edit-caret-report.json'
    $report | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $reportPath `
        -Encoding utf8
    Write-Host ($report | ConvertTo-Json -Depth 12)
} finally {
    Stop-QaBrowser $normalContext
    Stop-QaBrowser $reducedContext
}
