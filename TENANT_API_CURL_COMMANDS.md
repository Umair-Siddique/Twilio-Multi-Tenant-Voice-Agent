# Tenant API Testing - cURL Commands

This document contains individual cURL commands to test all tenant configuration APIs.

## Prerequisites

1. **Start your Flask server:**
   ```bash
   python run.py
   ```

2. **Set your base URL** (default: `http://localhost:5001`)

---

## Step 1: Create Tenants (Signup)

### Create Tenant A
```bash
curl -X POST http://localhost:5001/auth/signup \
  -H "Content-Type: application/json" \
  -d '{
    "email": "tenant_a@example.com",
    "password": "password123",
    "company_name": "Acme Healthcare",
    "timezone": "America/Toronto",
    "industry": "Healthcare",
    "default_email_recipients": ["admin@acme.com"]
  }'
```

**Save the `access_token` from the response as `TENANT_A_TOKEN`**

### Create Tenant B (for isolation testing)
```bash
curl -X POST http://localhost:5001/auth/signup \
  -H "Content-Type: application/json" \
  -d '{
    "email": "tenant_b@example.com",
    "password": "password123",
    "company_name": "Tech Solutions Inc",
    "timezone": "America/New_York",
    "industry": "Technology",
    "default_email_recipients": ["admin@techsolutions.com"]
  }'
```

**Save the `access_token` from the response as `TENANT_B_TOKEN`**

---

## Step 2: Sign In (Get Access Token)

If you already have a tenant, sign in to get a new token:

```bash
curl -X POST http://localhost:5001/auth/signin \
  -H "Content-Type: application/json" \
  -d '{
    "email": "tenant_a@example.com",
    "password": "password123"
  }'
```

**Save the `access_token` from the response**

---

## Step 3: Tenant Profile APIs

### Get Tenant Profile
```bash
curl -X GET http://localhost:5001/tenant/profile \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN" \
  -H "Content-Type: application/json"
```

### Update Tenant Profile (Owner/Admin only)
```bash
curl -X PUT http://localhost:5001/tenant/profile \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Acme Healthcare Updated",
    "timezone": "America/Los_Angeles",
    "industry": "Healthcare",
    "default_email_recipients": ["admin@acme.com", "support@acme.com"]
  }'
```

---

## Step 4: Agent Configuration APIs

### Get Agent Configuration
```bash
curl -X GET http://localhost:5001/tenant/agent-config \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN" \
  -H "Content-Type: application/json"
```

### Update Agent Tone
```bash
curl -X PUT http://localhost:5001/tenant/agent-config \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "tone": "friendly"
  }'
```

### Update Agent Greeting
```bash
curl -X PUT http://localhost:5001/tenant/agent-config \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "greeting": "Welcome to Acme Healthcare! How can I help you today?"
  }'
```

### Update Business Hours
```bash
curl -X PUT http://localhost:5001/tenant/agent-config \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "business_hours": {
      "monday": {"start": "09:00", "end": "17:00"},
      "tuesday": {"start": "09:00", "end": "17:00"},
      "wednesday": {"start": "09:00", "end": "17:00"},
      "thursday": {"start": "09:00", "end": "17:00"},
      "friday": {"start": "09:00", "end": "17:00"},
      "saturday": {"start": "10:00", "end": "14:00"},
      "sunday": null
    }
  }'
```

### Update Allowed Actions/Tools
```bash
curl -X PUT http://localhost:5001/tenant/agent-config \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "allowed_actions": [
      "create_booking",
      "reschedule_booking",
      "cancel_booking",
      "schedule_meeting",
      "create_lead",
      "handoff_to_human"
    ]
  }'
```

### Complete Agent Configuration Update
```bash
curl -X PUT http://localhost:5001/tenant/agent-config \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "tone": "friendly",
    "greeting": "Welcome to Acme Healthcare! How can I help you today?",
    "business_hours": {
      "monday": {"start": "09:00", "end": "17:00"},
      "tuesday": {"start": "09:00", "end": "17:00"},
      "wednesday": {"start": "09:00", "end": "17:00"},
      "thursday": {"start": "09:00", "end": "17:00"},
      "friday": {"start": "09:00", "end": "17:00"}
    },
    "escalation_rules": {
      "urgent_keywords": ["emergency", "urgent", "critical"],
      "transfer_to_human": true,
      "after_hours_action": "voicemail"
    },
    "allowed_actions": [
      "create_booking",
      "reschedule_booking",
      "handoff_to_human"
    ],
    "store_transcripts": true,
    "store_recordings": true,
    "retention_days": 90
  }'
```

---

## Step 5: Phone Numbers API

### Get Phone Numbers
```bash
curl -X GET http://localhost:5001/tenant/phone-numbers \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN" \
  -H "Content-Type: application/json"
```

---

## Step 6: Tenant Users API

### Get Tenant Users
```bash
curl -X GET http://localhost:5001/tenant/users \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN" \
  -H "Content-Type: application/json"
```

### Invite User to Tenant (Owner/Admin only)
```bash
curl -X POST http://localhost:5001/tenant/users \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "USER_UUID_HERE",
    "role": "admin"
  }'
```

**Note:** The user must already exist in Supabase Auth. The `user_id` is the UUID from `auth.users` table.

---

## Step 7: Testing Tenant Isolation (Security)

### Test 1: Tenant A accessing their own profile (should work)
```bash
curl -X GET http://localhost:5001/tenant/profile \
  -H "Authorization: Bearer TENANT_A_TOKEN" \
  -H "Content-Type: application/json"
```

**Expected:** Returns Tenant A's profile

### Test 2: Tenant A trying to access Tenant B's data (should be blocked by RLS)
```bash
curl -X GET http://localhost:5001/tenant/profile \
  -H "Authorization: Bearer TENANT_A_TOKEN" \
  -H "Content-Type: application/json"
```

**Expected:** Still returns Tenant A's profile (RLS automatically filters by tenant_id)

### Test 3: Access without token (should fail)
```bash
curl -X GET http://localhost:5001/tenant/profile \
  -H "Content-Type: application/json"
```

**Expected:** Returns `401 Unauthorized` error

### Test 4: Access with invalid token (should fail)
```bash
curl -X GET http://localhost:5001/tenant/profile \
  -H "Authorization: Bearer invalid_token_here" \
  -H "Content-Type: application/json"
```

**Expected:** Returns `401 Unauthorized` error

---

## Step 8: Get Current User Info

### Get Current User and Tenant Info
```bash
curl -X GET http://localhost:5001/auth/me \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN" \
  -H "Content-Type: application/json"
```

---

## Step 9: Refresh Token

### Refresh Access Token
```bash
curl -X POST http://localhost:5001/auth/refresh \
  -H "Content-Type: application/json" \
  -d '{
    "refresh_token": "YOUR_REFRESH_TOKEN"
  }'
```

---

## Step 10: Sign Out

### Sign Out
```bash
curl -X POST http://localhost:5001/auth/signout \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN" \
  -H "Content-Type: application/json"
```

---

## Quick Test Script (Bash)

Save this as `quick_test.sh` and run it:

```bash
#!/bin/bash

# Replace with your actual tokens after signup
TENANT_A_TOKEN="your_tenant_a_token_here"
TENANT_B_TOKEN="your_tenant_b_token_here"

BASE_URL="http://localhost:5001"

echo "Testing Tenant A Profile..."
curl -X GET "${BASE_URL}/tenant/profile" \
  -H "Authorization: Bearer ${TENANT_A_TOKEN}" \
  -H "Content-Type: application/json" | jq '.'

echo ""
echo "Testing Tenant A Agent Config..."
curl -X GET "${BASE_URL}/tenant/agent-config" \
  -H "Authorization: Bearer ${TENANT_A_TOKEN}" \
  -H "Content-Type: application/json" | jq '.'

echo ""
echo "Updating Tenant A Agent Config..."
curl -X PUT "${BASE_URL}/tenant/agent-config" \
  -H "Authorization: Bearer ${TENANT_A_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "tone": "friendly",
    "greeting": "Welcome! How can I help?",
    "allowed_actions": ["create_booking", "handoff_to_human"]
  }' | jq '.'

echo ""
echo "Testing Tenant B Profile (should be different from Tenant A)..."
curl -X GET "${BASE_URL}/tenant/profile" \
  -H "Authorization: Bearer ${TENANT_B_TOKEN}" \
  -H "Content-Type: application/json" | jq '.'
```

---

## Expected Responses

### Successful Profile Get
```json
{
  "tenant": {
    "id": "uuid",
    "name": "Acme Healthcare",
    "timezone": "America/Toronto",
    "industry": "Healthcare",
    "status": "active",
    "default_email_recipients": ["admin@acme.com"],
    "created_at": "2026-02-10T12:00:00Z",
    "updated_at": "2026-02-10T12:00:00Z"
  },
  "user_role": "owner"
}
```

### Successful Agent Config Get
```json
{
  "id": "uuid",
  "tenant_id": "uuid",
  "greeting": "Thank you for calling. How may I assist you today?",
  "tone": "professional",
  "business_hours": {},
  "escalation_rules": {},
  "allowed_actions": [],
  "store_transcripts": true,
  "store_recordings": true,
  "retention_days": 90,
  "created_at": "2026-02-10T12:00:00Z",
  "updated_at": "2026-02-10T12:00:00Z"
}
```

### Error Response (No Token)
```json
{
  "error": "Authorization token required"
}
```

### Error Response (Invalid Token)
```json
{
  "error": "Invalid token"
}
```

---

## Security Notes

✅ **Tenant Isolation is Enforced:**
- Each tenant can only see their own data
- RLS policies automatically filter queries by `tenant_id`
- Even if a tenant tries to access another tenant's endpoint, they'll only see their own data

✅ **Authentication Required:**
- All tenant APIs require a valid JWT token
- Tokens are obtained through signup or signin
- Tokens expire and can be refreshed

✅ **Role-Based Access:**
- Only `owner` and `admin` roles can update tenant profile and agent config
- `agent` and `viewer` roles have read-only access

---

## Troubleshooting

### "Authorization token required"
- Make sure you're including the `Authorization: Bearer TOKEN` header
- Verify the token is valid (not expired)

### "Invalid token"
- Token may have expired
- Use `/auth/refresh` to get a new token
- Or sign in again with `/auth/signin`

### "Insufficient permissions"
- You need `owner` or `admin` role to update tenant profile/agent config
- Check your role with `/auth/me` endpoint

### "Tenant not found"
- Verify you're using the correct token
- Token should be from a user who belongs to a tenant




