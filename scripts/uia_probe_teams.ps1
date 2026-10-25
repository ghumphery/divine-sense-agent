# UIA probe for the NEW Teams (ms-teams.exe / WebView2) compose box.
# SAFETY: never invokes the send button. The optional SetValue test
# only runs on an EMPTY compose box and restores it to "" afterwards.
param([switch]$DoSetValue)
$ErrorActionPreference = "Continue"
$sw = [System.Diagnostics.Stopwatch]::StartNew()
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

function Info($m) { Write-Host ("[uia {0,6:N1}s] {1}" -f $sw.Elapsed.TotalSeconds, $m) }

$teammPids = (Get-Process -Name "ms-teams" -ErrorAction SilentlyContinue).Id
if (-not $teammPids) { Info "no ms-teams process"; exit 1 }
Info ("ms-teams pids: " + ($teammPids -join ","))

$root = [System.Windows.Automation.AutomationElement]::RootElement

# ── 1. top-level windows of ms-teams ──
$wins = @()
$all = $root.FindAll([System.Windows.Automation.TreeScope]::Children,
                     [System.Windows.Automation.Condition]::TrueCondition)
Info ("top-level windows seen: " + $all.Count)
foreach ($w in $all) {
    try {
        if ($teammPids -contains $w.Current.ProcessId) {
            $c = $w.Current
            Info ("win: pid={0} name='{1}' class={2} offscreen={3}" -f
                  $c.ProcessId, $c.Name, $c.ClassName, $c.IsOffscreen)
            $wins += $w
        }
    } catch { }
}
if ($wins.Count -eq 0) { Info "NO top-level Teams window found"; exit 2 }

# use the first (main) window
$win = $wins[0]

# ── 2. enumerate Edit + Document descendants ──
function Dump-ByType($type, $label, $max) {
    $cond = New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
        $type)
    $found = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, $cond)
    Info ("{0}: {1} found" -f $label, $found.Count)
    $i = 0
    foreach ($e in $found) {
        if ($i -ge $max) { Info ("{0}: ... ({1} more)" -f $label, ($found.Count - $i)); break }
        try {
            $c = $e.Current
            $pats = ($e.GetSupportedPatterns() | ForEach-Object { $_.ProgrammaticName.Replace("PatternIdentifiers.Pattern", "") }) -join ","
            $val = ""
            try {
                $vp = $e.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
                $val = " value='" + $vp.Current.Value + "'"
            } catch { }
            Info ("  {0}[{1}]: name='{2}' id='{3}' offscreen={4} patterns=[{5}]{6}" -f
                  $label, $i, $c.Name, $c.AutomationId, $c.IsOffscreen, $pats, $val)
            $i++
        } catch { $i++ }
    }
    return $found
}
$edits = Dump-ByType ([System.Windows.Automation.ControlType]::Edit) "Edit" 15
$docs  = Dump-ByType ([System.Windows.Automation.ControlType]::Document) "Doc" 5
Dump-ByType ([System.Windows.Automation.ControlType]::Group) "GroupFirst" 0 | Out-Null

# ── 3. locate the compose box candidate ──
$compose = $null
foreach ($e in $edits) {
    try {
        $n = $e.Current.Name
        if ($n -match "輸入|訊息|message|Message|type|Type|草稿|撰寫") { $compose = $e; break }
    } catch { }
}
if (-not $compose -and $edits.Count -gt 0) {
    # fall back: the LAST edit is typically the compose box
    $compose = $edits[$edits.Count - 1]
    Info ("compose fallback: last Edit name='{0}'" -f $compose.Current.Name)
}
if (-not $compose) { Info "NO compose candidate"; exit 3 }
$c = $compose.Current
Info ("COMPOSE: name='{0}' id='{1}' offscreen={2}" -f $c.Name, $c.AutomationId, $c.IsOffscreen)
$pats = ($compose.GetSupportedPatterns() | ForEach-Object { $_.ProgrammaticName }) -join ","
Info ("COMPOSE patterns: {0}" -f $pats)

# ── 4. optional SetValue test (empty box only, restore after) ──
if ($DoSetValue) {
    try {
        $vp = $compose.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
    } catch {
        Info "SetValue-TEST: ValuePattern NOT supported → UIA 直接填入不可行"
        exit 4
    }
    $before = $vp.Current.Value
    # Chromium quirk: an EMPTY compose box reports the PLACEHOLDER as
    # the value ('輸入訊息\n'). Treat "value == name (modulo ws)" or
    # truly-empty as EMPTY — never touch a box with real content.
    $emptyLike = (($before.Trim().Length -eq 0) -or ($before.Trim() -eq $compose.Current.Name.Trim()))
    if (-not $emptyLike) {
        Info ("SetValue-TEST: SKIPPED — compose box has real content (len={0}), 不打擾" -f $before.Length)
        exit 0
    }
    try {
        $vp.SetValue("UIA spike 測試")
        Start-Sleep -Milliseconds 400
        $after = $vp.Current.Value
        Info ("SetValue-TEST: written, read-back='{0}' (len={1})" -f $after, $after.Length)
        # does the send button appear & is it invocable? (DO NOT invoke)
        $btnCond = New-Object System.Windows.Automation.PropertyCondition(
            [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
            [System.Windows.Automation.ControlType]::Button)
        $btns = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, $btnCond)
        Info ("buttons seen after fill: " + $btns.Count)
        $send = $null
        foreach ($b in $btns) {
            try { if ($b.Current.Name -match "傳送|送出|Send|send") {
                $sp = ($b.GetSupportedPatterns() | ForEach-Object { $_.ProgrammaticName }) -join ","
                Info ("  SEND-BTN: name='{0}' id='{1}' enabled={2} patterns=[{3}]" -f
                      $b.Current.Name, $b.Current.AutomationId, $b.Current.IsEnabled, $sp)
                $send = $b
            } } catch { }
        }
        if (-not $send) { Info "  SEND-BTN: none matched 傳送/Send (可能未出現)" }
        # restore
        $vp.SetValue("")
        Start-Sleep -Milliseconds 200
        Info ("SetValue-TEST: restored to '{0}'" -f $vp.Current.Value)
    } catch {
        Info ("SetValue-TEST FAILED: " + $_.Exception.Message)
        exit 5
    }
}
Info "probe done"