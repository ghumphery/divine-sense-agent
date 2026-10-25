$ErrorActionPreference = "Continue"
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class W {
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int cmd);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
}
"@

function Find-TeamsWin {
    $pids = (Get-Process -Name ms-teams -ErrorAction SilentlyContinue).Id
    if (-not $pids) { return $null }
    $root = [System.Windows.Automation.AutomationElement]::RootElement
    $all = $root.FindAll([System.Windows.Automation.TreeScope]::Children,
                         [System.Windows.Automation.Condition]::TrueCondition)
    foreach ($w in $all) {
        try {
            if ($pids -contains $w.Current.ProcessId -and $w.Current.Name) { return $w }
        } catch { }
    }
    return $null
}

$win = Find-TeamsWin
if (-not $win) { Write-Output "no-teams-window"; exit 1 }
$hwnd = [IntPtr]$win.Current.NativeWindowHandle
Write-Output ("window: {0}" -f $win.Current.Name)

function Read-Chat {
    $docCond = New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
        [System.Windows.Automation.ControlType]::Document)
    $docs = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, $docCond)
    foreach ($d in $docs) {
        try {
            $tp = $d.GetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern)
            $t = $tp.DocumentRange.GetText(6000)
            if ($t.Trim().Length -gt 0) { return $t }
        } catch { }
    }
    return ""
}

# state 1: current (foreground or covered)
$t1 = Read-Chat
Write-Output ("state1 current: chars={0}" -f $t1.Length)

# state 2: minimized (user31 SW_MINIMIZE=6), wait for renderer idle
[W]::ShowWindow($hwnd, 6) | Out-Null
Start-Sleep -Milliseconds 1500
$t2 = Read-Chat
Write-Output ("state2 minimized: chars={0}" -f $t2.Length)

# state 3: restore
[W]::ShowWindow($hwnd, 9) | Out-Null   # SW_RESTORE
Start-Sleep -Milliseconds 800
$t3 = Read-Chat
Write-Output ("state3 restored: chars={0}" -f $t3.Length)

if ($t2.Trim().Length -gt 0) { Write-Output "VERDICT: UIA read works even MINIMIZED ( Teams 可收在背景，不必在最上層)" }
else { Write-Output "VERDICT: minimized read EMPTY — UIA 樹退化，建議保持視窗開啟（被蓋住可、最小化不可）" }