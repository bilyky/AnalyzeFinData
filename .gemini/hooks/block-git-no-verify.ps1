# 1. Read input payload from stdin
$inputJson = $input | Out-String | ConvertFrom-Json

# 2. Extract target command
$command = $inputJson.tool_input.command

# 3. Block ANY git commit attempting to bypass hooks
if ($command -match "(?i)git commit.*(--no-verify|-n\b)") {
    [Console]::Error.WriteLine("[Security Hook] Blocked attempt to bypass git pre-commit hooks: '$command'")

    $response = @{
        decision      = "deny"
        reason        = "CRITICAL MANDATE: You are strictly forbidden from using --no-verify or bypassing pre-commit hooks. You must let the validation script run and fix any errors it finds."
        systemMessage = "🔒 SECURITY HOOK: Git --no-verify bypass blocked."
    }
    $response | ConvertTo-Json -Compress
    exit 0
}

# Command is safe, allow execution
@{ decision = "allow" } | ConvertTo-Json -Compress
exit 0
