param(
    [string]$BaseUrl = "http://127.0.0.1:8000",
    [string]$ApiKey = $(if ($env:PROFILE_API_KEY) { $env:PROFILE_API_KEY } else { "local-development-key" }),
    [string]$TenantId = $(if ($env:PROFILE_TENANT_ID) { $env:PROFILE_TENANT_ID } else { "test-tenant" })
)

$ErrorActionPreference = "Stop"
$BaseUrl = $BaseUrl.TrimEnd("/")
$stamp = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
$userId = "smoke-$stamp"
$smokeMessage = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String("5Lul5ZCO5Zue562U55+t5LiA54K544CC"))
$headers = @{ "X-API-Key" = $ApiKey; "X-Tenant-ID" = $TenantId }

Write-Host "[1/6] liveness and readiness"
$live = Invoke-RestMethod "$BaseUrl/livez"
if ($live.status -ne "ok") { throw "liveness failed" }
$health = Invoke-RestMethod "$BaseUrl/readyz"
if ($health.services.database -ne "ok") { throw "database health failed" }

Write-Host "[2/6] capabilities and version contract"
$capabilities = Invoke-RestMethod "$BaseUrl/v1/capabilities" -Headers $headers
if ($capabilities.api_version -ne "v1" -or -not $capabilities.profile_schema_version) {
    throw "capabilities contract failed"
}

Write-Host "[3/6] initialize and read"
$initHeaders = $headers.Clone(); $initHeaders["Idempotency-Key"] = "init-$userId"
$initBody = @{ tenant_user_id=$userId; consent=@{profile=$true;sensitive_inference=$false} } | ConvertTo-Json -Depth 4
$init = Invoke-RestMethod "$BaseUrl/v1/profiles:init" -Method Post -Headers $initHeaders `
    -ContentType "application/json; charset=utf-8" -Body ([Text.Encoding]::UTF8.GetBytes($initBody))
$before = Invoke-RestMethod "$BaseUrl/v1/profiles/$userId" -Headers $headers

Write-Host "[4/6] ingest one turn"
$turnId = "turn-$stamp"
$turnHeaders = $headers.Clone(); $turnHeaders["Idempotency-Key"] = $turnId
$body = @{
    conversation_id="session-$stamp"; message_id=$turnId; expected_profile_version=$before.profile_version
    occurred_at=[DateTime]::UtcNow.ToString("o"); text=$smokeMessage; context=@{recent_turns=@()}
} | ConvertTo-Json -Depth 5
$update = Invoke-RestMethod "$BaseUrl/v1/profiles/$userId/messages:ingest" -Method Post -Headers $turnHeaders `
    -ContentType "application/json; charset=utf-8" -Body ([Text.Encoding]::UTF8.GetBytes($body))
if ($update.no_profile_change) { throw "expected the preference message to update the profile" }

Write-Host "[5/6] verify latest profile"
$after = Invoke-RestMethod "$BaseUrl/v1/profiles/$userId" -Headers $headers
if ($after.profile_version -ne $update.profile_version) { throw "profile version mismatch" }
if ($after.profile_version -le $before.profile_version) { throw "profile version did not advance" }

Write-Host "[6/6] permanently delete test profile"
$deleteHeaders = $headers.Clone(); $deleteHeaders["Idempotency-Key"] = "delete-$userId"
$deleteBody = @{ expected_profile_version=$after.profile_version; confirm=$true; reason="automated smoke-test cleanup" } | ConvertTo-Json
$deleted = Invoke-RestMethod "$BaseUrl/v1/profiles/$($userId):delete" -Method Post -Headers $deleteHeaders `
    -ContentType "application/json; charset=utf-8" -Body ([Text.Encoding]::UTF8.GetBytes($deleteBody))
if (-not $deleted.deleted) { throw "profile deletion failed" }
try {
    Invoke-RestMethod "$BaseUrl/v1/profiles/$userId" -Headers $headers | Out-Null
    throw "deleted profile is still readable"
} catch {
    if ($_.Exception.Response.StatusCode.value__ -ne 404) { throw }
}
Write-Host "Smoke test passed and test profile removed: user=$userId"
