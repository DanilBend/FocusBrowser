param([Parameter(Mandatory = $true)][int]$ProcessId)

Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class FocusBrowserPowerState {
    [DllImport("kernel32.dll")]
    public static extern uint SetThreadExecutionState(uint flags);
}
'@

$continuous = 0x80000000
$systemRequired = 0x00000001
[FocusBrowserPowerState]::SetThreadExecutionState($continuous -bor $systemRequired) | Out-Null

try {
    Wait-Process -Id $ProcessId -ErrorAction SilentlyContinue
}
finally {
    [FocusBrowserPowerState]::SetThreadExecutionState($continuous) | Out-Null
}
