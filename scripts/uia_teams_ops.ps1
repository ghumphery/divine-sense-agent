# UIA operations against the NEW Teams client (ms-teams.exe) —
# the interference-free send/read channel for the Teams desktop
# assistant (v0.9.70.0+).
#
#   read  — dump the chat document text via TextPattern (no OCR, no
#           focus, no synthetic input)
#   send  — fill the compose box via UIA ValuePattern + invoke the
#           傳送 button via InvokePattern. SAFETY CHAIN:
#             1. if the send button ALREADY exists before our fill →
#                the compose box has content (probably the USER is
#                mid-typing) → YIELD, never touch, never send
#             2. fill → verify the send button APPEARED (proof the
#                text landed — Value read-back is placeholder-biased)
#             3. Invoke the send button EXACTLY ONCE
#             4. verify the button DISAPPEARED (compose emptied) —
#                still present → report honestly, never double-invoke
#   Output: a single-line JSON object on stdout (UTF-8).
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("read", "send", "search")]
    [string]$Action,
    [string]$TextFile = ""
)
$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
$sw = [System.Diagnostics.Stopwatch]::StartNew()
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

function Out-Json($obj) {
    Write-Output ($obj | ConvertTo-Json -Compress)
    exit 0
}
function Fail($reason, $extra = @{}) {
    $extra["ok"] = $false
    $extra["reason"] = $reason
    $extra["elapsed_s"] = [math]::Round($sw.Elapsed.TotalSeconds, 2)
    Out-Json $extra
}

# ── locate the Teams main window ──
$pids = (Get-Process -Name "ms-teams" -ErrorAction SilentlyContinue).Id
if (-not $pids) { Fail "no-teams-process" }
$root = [System.Windows.Automation.AutomationElement]::RootElement
$win = $null
$all = $root.FindAll([System.Windows.Automation.TreeScope]::Children,
                     [System.Windows.Automation.Condition]::TrueCondition)
foreach ($w in $all) {
    try {
        if ($pids -contains $w.Current.ProcessId -and $w.Current.Name) {
            $win = $w; break
        }
    } catch { }
}
if (-not $win) { Fail "no-teams-window" }

# ── read mode ──
if ($Action -eq "read") {
    try {
        $docCond = New-Object System.Windows.Automation.PropertyCondition(
            [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
            [System.Windows.Automation.ControlType]::Document)
        $docs = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, $docCond)
        $text = ""
        foreach ($d in $docs) {
            try {
                $tp = $d.GetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern)
                $t = $tp.DocumentRange.GetText(6000)
                if ($t.Trim().Length -gt $text.Trim().Length) { $text = $t }
            } catch { }
        }
        if ($text.Trim().Length -eq 0) { Fail "no-readable-text" }
        Out-Json @{ ok = $true
                    action = "read"
                    chars = $text.Length
                    text = $text
                    window = $win.Current.Name }
    } catch {
        Fail ("read-failed: " + $_.Exception.Message)
    }
}

# ── search mode (v0.9.105.1) ── pure-UIA navigation to a target chat ──
# no synthetic keyboard/mouse: expand-search (Invoke) → ValuePattern fill →
# poll result items → SelectionItem/Invoke → doc read-back for verification.
if ($Action -eq "search") {
    if ($TextFile -eq "" -or -not (Test-Path $TextFile)) { Fail "text-file-missing" }
    $target = [System.IO.File]::ReadAllText($TextFile, [System.Text.Encoding]::UTF8).Trim()
    if ($target.Length -eq 0) { Fail "empty-target" }

    function Find-SearchBox {
        # 桌面版搜尋＝ComboBox（aid=ms-searchux-input）；展開後或摺疊態都找得到
        foreach ($x in $win.FindAll([System.Windows.Automation.TreeScope]::Descendants,
                     [System.Windows.Automation.Condition]::TrueCondition)) {
            try {
                $aid = $x.Current.AutomationId
                if ($aid -eq "ms-searchux-input") { return $x }
            } catch { }
        }
        return $null
    }

    function Get-SearchValue {
        param($el)
        try { return $el.GetCurrentPattern(
            [System.Windows.Automation.ValuePattern]::Pattern) } catch { }
        # ComboBox 不支援 ValuePattern 時找底下的 Edit
        $sc = New-Object System.Windows.Automation.PropertyCondition(
            [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
            [System.Windows.Automation.ControlType]::Edit)
        foreach ($e in $el.FindAll([System.Windows.Automation.TreeScope]::Descendants, $sc)) {
            try { return $e.GetCurrentPattern(
                [System.Windows.Automation.ValuePattern]::Pattern) } catch { }
        }
        return $null
    }

    function Expand-Search {
        # 名稱掃描全控制型；ExpandCollapse 優先、Invoke 次之
        foreach ($x in $win.FindAll([System.Windows.Automation.TreeScope]::Descendants,
                     [System.Windows.Automation.Condition]::TrueCondition)) {
            try {
                $n = $x.Current.Name
                if (-not $n -or $n -notmatch "展開搜尋|Expand search") { continue }
                try {
                    ($x.GetCurrentPattern(
                        [System.Windows.Automation.ExpandCollapsePattern]::Pattern)).Expand()
                    Start-Sleep -Milliseconds 600
                    return $true
                } catch {
                    try {
                        ($x.GetCurrentPattern(
                            [System.Windows.Automation.InvokePattern]::Pattern)).Invoke()
                        Start-Sleep -Milliseconds 700
                        return $true
                    } catch { }
                }
            } catch { }
        }
        return $false
    }

    $box = Find-SearchBox
    if (-not $box) {
        $null = Expand-Search
        $box = Find-SearchBox
    }
    if (-not $box) { Fail "search-box-missing" }

    try {
        $svp = $null
        $svp = Get-SearchValue $box
    } catch { $svp = $null }
    if (-not $svp) { Fail "search-value-unsupported" }
    try {
        $svp.SetValue("")
        $svp.SetValue($target)
    } catch { Fail ("search-set-failed: " + $_.Exception.Message) }

    # poll for result items naming the target; the results dropdown sits
    # top-center → prefer the candidate closest to the window top-center
    $wr = $win.Current.BoundingRectangle
    $wcx = $wr.X + $wr.Width / 2
    $lct = [System.Windows.Automation.ControlType]::ListItem
    $dct = [System.Windows.Automation.ControlType]::DataItem
    $lc = New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::ControlTypeProperty, $lct)
    $dc = New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::ControlTypeProperty, $dct)
    $ic = New-Object System.Windows.Automation.OrCondition($lc, $dc)
    $picked = $null
    $candidates = 0
    $deadline = (Get-Date).AddSeconds(5)
    while ((Get-Date) -lt $deadline -and -not $picked) {
        Start-Sleep -Milliseconds 400
        $best = $null
        $bestD = [double]::MaxValue
        foreach ($it in $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, $ic)) {
            try {
                $n = $it.Current.Name
                if ($n -and $n -match [regex]::Escape($target)) {
                    $candidates++
                    $r2 = $it.Current.BoundingRectangle
                    if ($r2.Width -le 0) { continue }
                    $dist = [Math]::Abs(($r2.X + $r2.Width / 2) - $wcx) +
                            [Math]::Abs($r2.Y - ($wr.Y + 50))
                    if ($dist -lt $bestD) { $bestD = $dist; $best = $it }
                }
            } catch { }
        }
        if ($best) { $picked = $best }
    }
    if (-not $picked) {
        Fail "no-search-results" @{ searched = $target; candidates = $candidates }
    }

    $how = "none"
    try {
        ($picked.GetCurrentPattern(
            [System.Windows.Automation.SelectionItemPattern]::Pattern)).Select()
        $how = "select"
    } catch {
        try {
            ($picked.GetCurrentPattern(
                [System.Windows.Automation.InvokePattern]::Pattern)).Invoke()
            $how = "invoke"
        } catch {
            Fail "result-unselectable" @{ picked = $picked.Current.Name }
        }
    }
    Start-Sleep -Milliseconds 1200

    # doc read-back (same channel as read) for caller-side verification
    $rc = New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
        [System.Windows.Automation.ControlType]::Document)
    $text = ""
    foreach ($d in $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, $rc)) {
        try {
            $tp = $d.GetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern)
            $t = $tp.DocumentRange.GetText(6000)
            if ($t.Trim().Length -gt $text.Trim().Length) { $text = $t }
        } catch { }
    }
    Out-Json @{ ok = $true
                action = "search"
                navigated = $how
                picked = $picked.Current.Name
                doc_chars = $text.Length
                window = $win.Current.Name
                elapsed_s = [math]::Round($sw.Elapsed.TotalSeconds, 2) }
}

# ── send mode ──
if ($TextFile -eq "" -or -not (Test-Path $TextFile)) { Fail "text-file-missing" }
$text = [System.IO.File]::ReadAllText($TextFile, [System.Text.Encoding]::UTF8)
if ($text.Trim().Length -eq 0) { Fail "empty-text" }

$editCond = New-Object System.Windows.Automation.PropertyCondition(
    [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
    [System.Windows.Automation.ControlType]::Edit)
$edits = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, $editCond)
$compose = $null
foreach ($e in $edits) {
    try {
        if ($e.Current.Name -match "輸入|訊息|message|Message" -or
            $e.Current.AutomationId -match "^new-message-") {
            $compose = $e; break
        }
    } catch { }
}
if (-not $compose) { Fail "no-compose-box" }
try {
    $vp = $compose.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
} catch {
    Fail "value-pattern-unsupported"
}

function Find-SendBtn {
    $btnCond = New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
        [System.Windows.Automation.ControlType]::Button)
    $btns = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, $btnCond)
    foreach ($b in $btns) {
        try {
            if ($b.Current.Name -match "傳送|送出|Send" -and $b.Current.IsEnabled) {
                return $b
            }
        } catch { }
    }
    return $null
}

# safety chain step 1 (v0.9.105.1) — New Teams renders the send button
# permanently (IsEnabled even when empty) → the REAL emptiness probe =
# the compose Edit's value vs the placeholder text (e.g. 「輸入訊息」).
function Get-ComposeValueProbe {
    $ec2 = [System.Windows.Automation.AutomationElement]::ControlTypeProperty
    $sc2 = New-Object System.Windows.Automation.PropertyCondition(
        $ec2, [System.Windows.Automation.ControlType]::Edit)
    foreach ($e in $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, $sc2)) {
        try {
            $aid = $e.Current.AutomationId
            if ($aid -like "new-message-*") { return $e }
        } catch { }
    }
    return $null
}

$isPlaceholder = {
    param($v)
    if ([string]::IsNullOrWhiteSpace($v)) { return $true }
    $t = $v.Trim()
    return ($t -eq "輸入訊息" -or $t -eq "輸入訊息\r\n" -or
            $t -eq "Type a message" -or $t -eq "輸入訊息\n")
}

$composeProbe = Get-ComposeValueProbe
if ($composeProbe) {
    try {
        $probeVP = $composeProbe.GetCurrentPattern(
            [System.Windows.Automation.ValuePattern]::Pattern)
        $probeVal = $probeVP.Current.Value
        $isPlace = (& $isPlaceholder $probeVal)
        if (-not $isPlace) {
            Fail "compose-not-empty-yield" @{
                compose_value = $probeVal.Substring(
                    0, [Math]::Min(60, $probeVal.Length))
                note = "撰寫框有內容（可能為使用者草稿）— 絕不觸碰、不送出"
            }
        }
    } catch { }
}

# step 2 — fill via ValuePattern（在 composeProbe 上）；落地判據＝值變為 text
# （v0.9.105.1：New Teams 的傳送鈕常駐，「按鈕出現」不再是落地證據）
try { $vp.SetValue($text) } catch {
    Fail ("set-value-failed: " + $_.Exception.Message)
}
Start-Sleep -Milliseconds 300
try { $filledNow = $vp.Current.Value } catch { $filledNow = "" }
if (-not ($filledNow -match [regex]::Escape($text))) {
    # text did not land (e.g. UIA tree changed) — nothing was sent
    try { $vp.SetValue("") } catch { }
    Fail "fill-not-landing" @{ sent = $false
                               value = $filledNow.Substring(
                                   0, [Math]::Min(60, $filledNow.Length)) }
}

# step 3 — invoke the send button EXACTLY ONCE
$btnSend = Find-SendBtn
if ($btnSend -eq $null) {
    # 找不到可 invoke 的傳送鈕 → 留值還原（清空）回報
    try { $vp.SetValue("") } catch { }
    Fail "send-button-missing" @{ sent = $false }
}
$invoked = $false
try {
    $ip = $btnSend.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
    $ip.Invoke()
    $invoked = $true
} catch {
    # leave the text in the box (recoverable by the user) — report
    Fail ("invoke-failed: " + $_.Exception.Message) @{ sent = $false
                                                       text_left_in_box = $true }
}

# step 4 (v0.9.105.1) — verify the compose value returned to placeholder
Start-Sleep -Milliseconds 900
try { $finalVal = $vp.Current.Value } catch { $finalVal = "unreadable" }
$isPlaceFinal = (& $isPlaceholder $finalVal)
if (-not $isPlaceFinal) {
    Out-Json @{ ok = $false
                reason = "compose-still-text"
                sent = $false
                invoked = $invoked
                value = $finalVal.Substring(0, [Math]::Min(60, $finalVal.Length))
                note = "送出後撰寫框仍有文字 — 未確認送出，絕不重複觸發；請人工確認"
                elapsed_s = [math]::Round($sw.Elapsed.TotalSeconds, 2) }
}
Out-Json @{ ok = $true
            action = "send"
            sent = $true
            chars = $text.Length
            invoked = $invoked
            window = $win.Current.Name
            elapsed_s = [math]::Round($sw.Elapsed.TotalSeconds, 2) }
Out-Json @{ ok = $true
            action = "send"
            sent = $true
            chars = $text.Length
            invoked = $invoked
            window = $win.Current.Name
            elapsed_s = [math]::Round($sw.Elapsed.TotalSeconds, 2) }