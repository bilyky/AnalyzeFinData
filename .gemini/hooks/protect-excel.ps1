# 1. Read input payload from stdin
$inputJson = $input | Out-String | ConvertFrom-Json

# 2. Extract target command
$command = $inputJson.tool_input.command

# 3. Block ANY command trying to touch or kill Excel
if ($command -match "(?i)(excel)") -and ($command -match "(?i)(Stop-Process|taskkill|kill|Terminate)") {
    [Console]::Error.WriteLine("[Security Hook] Blocked attempt to kill or modify excel.exe: '$command'")

    $response = @{
        decision      = "deny"
        reason        = "CRITICAL MANDATE: You are strictly and absolutely forbidden from touching or killing excel.exe. Do not attempt this again."
        systemMessage = "🔒 SECURITY HOOK: Excel process termination blocked."
    }
    $response | ConvertTo-Json -Compress
    exit 0
}

# 4. Broadly block ALL process killing commands just to be safe
$blockedPatterns = "\b(taskkill|Stop-Process|killall|pkill)\b"
if ($command -match $blockedPatterns) {
    [Console]::Error.WriteLine("[Security Hook] Blocked destructive process command: '$command'")

    $response = @{
        decision      = "deny"
        reason        = "Execution blocked: Process termination is strictly prohibited without explicit user approval."
        systemMessage = "🔒 SECURITY HOOK: Process-killing command blocked."
    }
    $response | ConvertTo-Json -Compress
    exit 0
}

# Command is safe, allow execution
@{ decision = "allow" } | ConvertTo-Json -Compress
exit 0
