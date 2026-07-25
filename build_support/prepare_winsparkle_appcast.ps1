[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ToolPath,

    [Parameter(Mandatory = $true)]
    [string]$PrivateKeyPath,

    [Parameter(Mandatory = $true)]
    [string]$PublicKey,

    [Parameter(Mandatory = $true)]
    [string]$PayloadPath,

    [Parameter(Mandatory = $true)]
    [string]$OutputPath,

    [Parameter(Mandatory = $true)]
    [string]$Version,

    [Parameter(Mandatory = $true)]
    [string]$ShortVersion,

    [Parameter(Mandatory = $true)]
    [string]$ReleaseTag,

    [string]$Repository = 'DanilBend/FocusBrowser',

    [DateTimeOffset]$PublishedAt = [DateTimeOffset]::UtcNow
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Resolve-RequiredFile([string]$Path, [string]$Label) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "$Label does not exist: $Path"
    }
    return (Resolve-Path -LiteralPath $Path).Path
}

function Escape-Xml([string]$Value) {
    return [Security.SecurityElement]::Escape($Value)
}

if ($Version -notmatch '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$') {
    throw "Version must contain exactly four numeric components"
}
if ($ShortVersion -notmatch '^[0-9]+\.[0-9]+\.[0-9]+$') {
    throw "ShortVersion must contain exactly three numeric components"
}
if ($Version -cne "$($ShortVersion).0") {
    throw "Version must equal ShortVersion with a zero platform revision"
}
if ($ReleaseTag -cne "v$ShortVersion") {
    throw "ReleaseTag must equal v$ShortVersion"
}
if ($Repository -notmatch '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$') {
    throw "Repository must be an owner/name pair"
}
if ($PublicKey -notmatch '^[A-Za-z0-9+/]{43}=$') {
    throw "PublicKey is not a canonical Base64 Ed25519 public key"
}

$resolvedTool = Resolve-RequiredFile $ToolPath 'winsparkle-tool'
$resolvedKey = Resolve-RequiredFile $PrivateKeyPath 'Private key'
$resolvedPayload = Resolve-RequiredFile $PayloadPath 'Update payload'
$expectedPayloadName =
    "FocusBrowser_${ShortVersion}_x64-mini-installer.exe"
if ([IO.Path]::GetFileName($resolvedPayload) -cne $expectedPayloadName) {
    throw "Payload must be named exactly $expectedPayloadName"
}

$repoRoot = (Resolve-Path -LiteralPath (
    Join-Path $PSScriptRoot '..')).Path.TrimEnd('\') + '\'
if ($resolvedKey.StartsWith(
        $repoRoot,
        [StringComparison]::OrdinalIgnoreCase)) {
    throw "The private key must remain outside the repository"
}

$toolVersion = ((& $resolvedTool --version) | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $toolVersion -cne '0.9.3') {
    throw "Expected winsparkle-tool 0.9.3"
}

$payloadLength = (Get-Item -LiteralPath $resolvedPayload).Length
if ($payloadLength -lt 1MB) {
    throw "Update payload is unexpectedly small"
}

$stream = [IO.File]::OpenRead($resolvedPayload)
try {
    $reader = [IO.BinaryReader]::new($stream)
    if ($reader.ReadUInt16() -ne 0x5A4D) {
        throw "Update payload is not a PE file"
    }
    $stream.Position = 0x3C
    $peOffset = $reader.ReadInt32()
    if ($peOffset -lt 0x40 -or $peOffset -gt ($stream.Length - 6)) {
        throw "Invalid PE header offset"
    }
    $stream.Position = $peOffset
    if ($reader.ReadUInt32() -ne 0x00004550) {
        throw "Invalid PE signature"
    }
    if ($reader.ReadUInt16() -ne 0x8664) {
        throw "Update payload is not Windows x64"
    }
} finally {
    $stream.Dispose()
}

$fragment = ((& $resolvedTool sign --verbose `
    --private-key-file $resolvedKey `
    $resolvedPayload) | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or
    $fragment -notmatch
        '^sparkle:edSignature="([A-Za-z0-9+/]{86}==)" length="([0-9]+)"$') {
    throw "Unexpected winsparkle-tool sign output"
}
$signature = $Matches[1]
$signedLength = [int64]$Matches[2]
if ($signedLength -ne $payloadLength) {
    throw "Signed length does not match payload length"
}

& $resolvedTool verify `
    --public-key $PublicKey `
    --signature $signature `
    $resolvedPayload
if ($LASTEXITCODE -ne 0) {
    throw "WinSparkle signature verification failed"
}

$assetUrl =
    "https://github.com/$Repository/releases/download/$ReleaseTag/" +
    $expectedPayloadName
$releaseUrl = "https://github.com/$Repository/releases/tag/$ReleaseTag"
$pubDate = $PublishedAt.ToUniversalTime().ToString(
    'r',
    [Globalization.CultureInfo]::InvariantCulture)
$xml = @"
<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0" xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle">
  <channel>
    <title>Focus Browser updates (x64)</title>
    <link>$(Escape-Xml $releaseUrl)</link>
    <description>Stable updates for Focus Browser x64</description>
    <language>ru</language>
    <item>
      <title>Focus Browser $ShortVersion</title>
      <pubDate>$pubDate</pubDate>
      <link>$(Escape-Xml $releaseUrl)</link>
      <enclosure
        url="$(Escape-Xml $assetUrl)"
        sparkle:version="$Version"
        sparkle:shortVersionString="$ShortVersion"
        sparkle:os="windows-x64"
        sparkle:edSignature="$signature"
        length="$signedLength"
        type="application/octet-stream" />
    </item>
  </channel>
</rss>
"@

$resolvedOutputParent = Split-Path -Parent (
    [IO.Path]::GetFullPath($OutputPath))
New-Item -ItemType Directory -Path $resolvedOutputParent -Force | Out-Null
$resolvedOutput = Join-Path $resolvedOutputParent (
    Split-Path -Leaf $OutputPath)
[IO.File]::WriteAllText(
    $resolvedOutput,
    $xml.TrimStart(),
    [Text.UTF8Encoding]::new($false))

$settings = [Xml.XmlReaderSettings]::new()
$settings.DtdProcessing = [Xml.DtdProcessing]::Prohibit
$settings.XmlResolver = $null
$xmlReader = [Xml.XmlReader]::Create($resolvedOutput, $settings)
try {
    $document = [Xml.XmlDocument]::new()
    $document.XmlResolver = $null
    $document.Load($xmlReader)
} finally {
    $xmlReader.Dispose()
}
if (@($document.SelectNodes('/rss/channel/item/enclosure')).Count -ne 1) {
    throw "Generated appcast does not contain exactly one enclosure"
}

$payloadSha256 =
    (Get-FileHash -Algorithm SHA256 -LiteralPath $resolvedPayload).Hash
$appcastSha256 =
    (Get-FileHash -Algorithm SHA256 -LiteralPath $resolvedOutput).Hash
[pscustomobject]@{
    Version = $Version
    ReleaseTag = $ReleaseTag
    Payload = $resolvedPayload
    PayloadLength = $payloadLength
    PayloadSha256 = $payloadSha256
    Appcast = $resolvedOutput
    AppcastSha256 = $appcastSha256
}
