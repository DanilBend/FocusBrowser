param(
    [Parameter(Mandatory = $true)]
    [int]$ProcessId
)

$source = @'
using System;
using System.Runtime.InteropServices;

public static class ConsoleSignal {
    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern bool FreeConsole();

    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern bool AttachConsole(uint processId);

    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern bool SetConsoleCtrlHandler(IntPtr handler, bool add);

    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern bool GenerateConsoleCtrlEvent(uint ctrlEvent, uint processGroupId);
}
'@

Add-Type -TypeDefinition $source
[ConsoleSignal]::FreeConsole() | Out-Null
if (-not [ConsoleSignal]::AttachConsole([uint32]$ProcessId)) {
    throw "Unable to attach to console for process $ProcessId"
}

[ConsoleSignal]::SetConsoleCtrlHandler([IntPtr]::Zero, $true) | Out-Null
if (-not [ConsoleSignal]::GenerateConsoleCtrlEvent(0, 0)) {
    throw "Unable to send Ctrl+C to process $ProcessId"
}

Start-Sleep -Seconds 2
[ConsoleSignal]::FreeConsole() | Out-Null
