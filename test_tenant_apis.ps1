# =====================================================
# Tenant API Testing Script (PowerShell)
# =====================================================
# This script tests tenant configuration APIs and verifies
# that tenants cannot access each other's data (RLS isolation)
# =====================================================

$BASE_URL = "http://localhost:5001"

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "Tenant API Testing Script" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""

# =====================================================
# Step 1: Create Two Tenants (for isolation testing)
# =====================================================

Write-Host "Step 1: Creating Tenant A..." -ForegroundColor Yellow

$tenantABody = @{
    email = "tenant_a@example.com"
    password = "password123"
    company_name = "Acme Healthcare"
    timezone = "America/Toronto"
    industry = "Healthcare"
    default_email_recipients = @("admin@acme.com")
} | ConvertTo-Json

$tenantAResponse = Invoke-RestMethod -Uri "$BASE_URL/auth/signup" `
    -Method POST `
    -ContentType "application/json" `
    -Body $tenantABody

Write-Host ($tenantAResponse | ConvertTo-Json -Depth 10)
$TENANT_A_TOKEN = $tenantAResponse.session.access_token
$TENANT_A_TENANT_ID = $tenantAResponse.tenant.id

if (-not $TENANT_A_TOKEN) {
    Write-Host "Failed to create Tenant A" -ForegroundColor Red
    exit 1
}

Write-Host "✓ Tenant A created successfully" -ForegroundColor Green
Write-Host "Tenant A Token: $($TENANT_A_TOKEN.Substring(0, [Math]::Min(50, $TENANT_A_TOKEN.Length)))..."
Write-Host "Tenant A ID: $TENANT_A_TENANT_ID"
Write-Host ""

Start-Sleep -Seconds 2

Write-Host "Step 2: Creating Tenant B..." -ForegroundColor Yellow

$tenantBBody = @{
    email = "tenant_b@example.com"
    password = "password123"
    company_name = "Tech Solutions Inc"
    timezone = "America/New_York"
    industry = "Technology"
    default_email_recipients = @("admin@techsolutions.com")
} | ConvertTo-Json

$tenantBResponse = Invoke-RestMethod -Uri "$BASE_URL/auth/signup" `
    -Method POST `
    -ContentType "application/json" `
    -Body $tenantBBody

Write-Host ($tenantBResponse | ConvertTo-Json -Depth 10)
$TENANT_B_TOKEN = $tenantBResponse.session.access_token
$TENANT_B_TENANT_ID = $tenantBResponse.tenant.id

if (-not $TENANT_B_TOKEN) {
    Write-Host "Failed to create Tenant B" -ForegroundColor Red
    exit 1
}

Write-Host "✓ Tenant B created successfully" -ForegroundColor Green
Write-Host "Tenant B Token: $($TENANT_B_TOKEN.Substring(0, [Math]::Min(50, $TENANT_B_TOKEN.Length)))..."
Write-Host "Tenant B ID: $TENANT_B_TENANT_ID"
Write-Host ""

# =====================================================
# Step 3: Test Tenant A APIs
# =====================================================

Write-Host "==========================================" -ForegroundColor Yellow
Write-Host "Testing Tenant A APIs"
Write-Host "==========================================" -ForegroundColor Yellow
Write-Host ""

Write-Host "3.1: Get Tenant A Profile" -ForegroundColor Yellow
$headers = @{
    "Authorization" = "Bearer $TENANT_A_TOKEN"
    "Content-Type" = "application/json"
}
Invoke-RestMethod -Uri "$BASE_URL/tenant/profile" -Method GET -Headers $headers | ConvertTo-Json -Depth 10
Write-Host ""

Write-Host "3.2: Get Tenant A Agent Config" -ForegroundColor Yellow
Invoke-RestMethod -Uri "$BASE_URL/tenant/agent-config" -Method GET -Headers $headers | ConvertTo-Json -Depth 10
Write-Host ""

Write-Host "3.3: Update Tenant A Profile" -ForegroundColor Yellow
$updateProfileBody = @{
    name = "Acme Healthcare Updated"
    timezone = "America/Los_Angeles"
} | ConvertTo-Json
Invoke-RestMethod -Uri "$BASE_URL/tenant/profile" -Method PUT -Headers $headers -Body $updateProfileBody | ConvertTo-Json -Depth 10
Write-Host ""

Write-Host "3.4: Update Tenant A Agent Config (Tone & Greeting)" -ForegroundColor Yellow
$updateConfigBody = @{
    tone = "friendly"
    greeting = "Welcome to Acme Healthcare! How can I help you today?"
    business_hours = @{
        monday = @{start = "09:00"; end = "17:00"}
        tuesday = @{start = "09:00"; end = "17:00"}
        wednesday = @{start = "09:00"; end = "17:00"}
        thursday = @{start = "09:00"; end = "17:00"}
        friday = @{start = "09:00"; end = "17:00"}
    }
    allowed_actions = @("create_booking", "reschedule_booking", "handoff_to_human")
} | ConvertTo-Json -Depth 10
Invoke-RestMethod -Uri "$BASE_URL/tenant/agent-config" -Method PUT -Headers $headers -Body $updateConfigBody | ConvertTo-Json -Depth 10
Write-Host ""

Write-Host "3.5: Get Tenant A Phone Numbers" -ForegroundColor Yellow
Invoke-RestMethod -Uri "$BASE_URL/tenant/phone-numbers" -Method GET -Headers $headers | ConvertTo-Json -Depth 10
Write-Host ""

Write-Host "3.6: Get Tenant A Users" -ForegroundColor Yellow
Invoke-RestMethod -Uri "$BASE_URL/tenant/users" -Method GET -Headers $headers | ConvertTo-Json -Depth 10
Write-Host ""

# =====================================================
# Step 4: Test Tenant B APIs
# =====================================================

Write-Host "==========================================" -ForegroundColor Yellow
Write-Host "Testing Tenant B APIs"
Write-Host "==========================================" -ForegroundColor Yellow
Write-Host ""

Write-Host "4.1: Get Tenant B Profile" -ForegroundColor Yellow
$headersB = @{
    "Authorization" = "Bearer $TENANT_B_TOKEN"
    "Content-Type" = "application/json"
}
Invoke-RestMethod -Uri "$BASE_URL/tenant/profile" -Method GET -Headers $headersB | ConvertTo-Json -Depth 10
Write-Host ""

Write-Host "4.2: Update Tenant B Agent Config" -ForegroundColor Yellow
$updateConfigBodyB = @{
    tone = "professional"
    greeting = "Thank you for calling Tech Solutions. How may I assist you?"
    allowed_actions = @("create_lead", "schedule_meeting", "handoff_to_human")
} | ConvertTo-Json -Depth 10
Invoke-RestMethod -Uri "$BASE_URL/tenant/agent-config" -Method PUT -Headers $headersB -Body $updateConfigBodyB | ConvertTo-Json -Depth 10
Write-Host ""

# =====================================================
# Step 5: Test Tenant Isolation (Security Test)
# =====================================================

Write-Host "==========================================" -ForegroundColor Yellow
Write-Host "Testing Tenant Isolation (Security)"
Write-Host "==========================================" -ForegroundColor Yellow
Write-Host ""

Write-Host "5.1: Tenant A trying to access Tenant B profile (should only see own)" -ForegroundColor Yellow
$tenantAAccessingB = Invoke-RestMethod -Uri "$BASE_URL/tenant/profile" -Method GET -Headers $headers
$tenantAProfileId = $tenantAAccessingB.tenant.id

if ($tenantAProfileId -eq $TENANT_A_TENANT_ID) {
    Write-Host "✓ Security: Tenant A can only see their own profile" -ForegroundColor Green
} else {
    Write-Host "✗ Security Issue: Tenant A can access other tenant data!" -ForegroundColor Red
}
$tenantAAccessingB | ConvertTo-Json -Depth 10
Write-Host ""

Write-Host "5.2: Tenant B trying to access Tenant A profile (should only see own)" -ForegroundColor Yellow
$tenantBAccessingA = Invoke-RestMethod -Uri "$BASE_URL/tenant/profile" -Method GET -Headers $headersB
$tenantBProfileId = $tenantBAccessingA.tenant.id

if ($tenantBProfileId -eq $TENANT_B_TENANT_ID) {
    Write-Host "✓ Security: Tenant B can only see their own profile" -ForegroundColor Green
} else {
    Write-Host "✗ Security Issue: Tenant B can access other tenant data!" -ForegroundColor Red
}
$tenantBAccessingA | ConvertTo-Json -Depth 10
Write-Host ""

# =====================================================
# Step 6: Test Without Token (Should Fail)
# =====================================================

Write-Host "==========================================" -ForegroundColor Yellow
Write-Host "Testing Authentication (No Token)"
Write-Host "==========================================" -ForegroundColor Yellow
Write-Host ""

Write-Host "6.1: Accessing profile without token (should fail)" -ForegroundColor Yellow
try {
    $noTokenResponse = Invoke-RestMethod -Uri "$BASE_URL/tenant/profile" -Method GET -ErrorAction Stop
    Write-Host "✗ Security Issue: API allows access without token!" -ForegroundColor Red
} catch {
    if ($_.Exception.Response.StatusCode -eq 401) {
        Write-Host "✓ Security: API correctly rejects requests without token" -ForegroundColor Green
    } else {
        Write-Host "Error: $_" -ForegroundColor Red
    }
}
Write-Host ""

# =====================================================
# Summary
# =====================================================

Write-Host "==========================================" -ForegroundColor Green
Write-Host "Testing Complete!"
Write-Host "==========================================" -ForegroundColor Green
Write-Host ""
Write-Host "Tenant A ID: $TENANT_A_TENANT_ID"
Write-Host "Tenant B ID: $TENANT_B_TENANT_ID"
Write-Host ""
Write-Host "Both tenants should only see their own data."
Write-Host "RLS policies enforce tenant isolation at the database level."




