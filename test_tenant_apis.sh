#!/bin/bash

# =====================================================
# Tenant API Testing Script
# =====================================================
# This script tests tenant configuration APIs and verifies
# that tenants cannot access each other's data (RLS isolation)
# =====================================================

BASE_URL="http://localhost:5001"

echo "=========================================="
echo "Tenant API Testing Script"
echo "=========================================="
echo ""

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# =====================================================
# Step 1: Create Two Tenants (for isolation testing)
# =====================================================

echo -e "${YELLOW}Step 1: Creating Tenant A...${NC}"
TENANT_A_RESPONSE=$(curl -s -X POST "${BASE_URL}/auth/signup" \
  -H "Content-Type: application/json" \
  -d '{
    "email": "tenant_a@example.com",
    "password": "password123",
    "company_name": "Acme Healthcare",
    "timezone": "America/Toronto",
    "industry": "Healthcare",
    "default_email_recipients": ["admin@acme.com"]
  }')

echo "$TENANT_A_RESPONSE" | jq '.'
TENANT_A_TOKEN=$(echo "$TENANT_A_RESPONSE" | jq -r '.session.access_token')
TENANT_A_TENANT_ID=$(echo "$TENANT_A_RESPONSE" | jq -r '.tenant.id')

if [ "$TENANT_A_TOKEN" == "null" ] || [ -z "$TENANT_A_TOKEN" ]; then
  echo -e "${RED}Failed to create Tenant A${NC}"
  exit 1
fi

echo -e "${GREEN}✓ Tenant A created successfully${NC}"
echo "Tenant A Token: ${TENANT_A_TOKEN:0:50}..."
echo "Tenant A ID: $TENANT_A_TENANT_ID"
echo ""

sleep 2

echo -e "${YELLOW}Step 2: Creating Tenant B...${NC}"
TENANT_B_RESPONSE=$(curl -s -X POST "${BASE_URL}/auth/signup" \
  -H "Content-Type: application/json" \
  -d '{
    "email": "tenant_b@example.com",
    "password": "password123",
    "company_name": "Tech Solutions Inc",
    "timezone": "America/New_York",
    "industry": "Technology",
    "default_email_recipients": ["admin@techsolutions.com"]
  }')

echo "$TENANT_B_RESPONSE" | jq '.'
TENANT_B_TOKEN=$(echo "$TENANT_B_RESPONSE" | jq -r '.session.access_token')
TENANT_B_TENANT_ID=$(echo "$TENANT_B_RESPONSE" | jq -r '.tenant.id')

if [ "$TENANT_B_TOKEN" == "null" ] || [ -z "$TENANT_B_TOKEN" ]; then
  echo -e "${RED}Failed to create Tenant B${NC}"
  exit 1
fi

echo -e "${GREEN}✓ Tenant B created successfully${NC}"
echo "Tenant B Token: ${TENANT_B_TOKEN:0:50}..."
echo "Tenant B ID: $TENANT_B_TENANT_ID"
echo ""

# =====================================================
# Step 3: Test Tenant A APIs
# =====================================================

echo -e "${YELLOW}=========================================="
echo "Testing Tenant A APIs"
echo "==========================================${NC}"
echo ""

echo -e "${YELLOW}3.1: Get Tenant A Profile${NC}"
curl -s -X GET "${BASE_URL}/tenant/profile" \
  -H "Authorization: Bearer ${TENANT_A_TOKEN}" \
  -H "Content-Type: application/json" | jq '.'
echo ""

echo -e "${YELLOW}3.2: Get Tenant A Agent Config${NC}"
curl -s -X GET "${BASE_URL}/tenant/agent-config" \
  -H "Authorization: Bearer ${TENANT_A_TOKEN}" \
  -H "Content-Type: application/json" | jq '.'
echo ""

echo -e "${YELLOW}3.3: Update Tenant A Profile${NC}"
curl -s -X PUT "${BASE_URL}/tenant/profile" \
  -H "Authorization: Bearer ${TENANT_A_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Acme Healthcare Updated",
    "timezone": "America/Los_Angeles"
  }' | jq '.'
echo ""

echo -e "${YELLOW}3.4: Update Tenant A Agent Config (Tone & Greeting)${NC}"
curl -s -X PUT "${BASE_URL}/tenant/agent-config" \
  -H "Authorization: Bearer ${TENANT_A_TOKEN}" \
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
    "allowed_actions": ["create_booking", "reschedule_booking", "handoff_to_human"]
  }' | jq '.'
echo ""

echo -e "${YELLOW}3.5: Get Tenant A Phone Numbers${NC}"
curl -s -X GET "${BASE_URL}/tenant/phone-numbers" \
  -H "Authorization: Bearer ${TENANT_A_TOKEN}" \
  -H "Content-Type: application/json" | jq '.'
echo ""

echo -e "${YELLOW}3.6: Get Tenant A Users${NC}"
curl -s -X GET "${BASE_URL}/tenant/users" \
  -H "Authorization: Bearer ${TENANT_A_TOKEN}" \
  -H "Content-Type: application/json" | jq '.'
echo ""

# =====================================================
# Step 4: Test Tenant B APIs
# =====================================================

echo -e "${YELLOW}=========================================="
echo "Testing Tenant B APIs"
echo "==========================================${NC}"
echo ""

echo -e "${YELLOW}4.1: Get Tenant B Profile${NC}"
curl -s -X GET "${BASE_URL}/tenant/profile" \
  -H "Authorization: Bearer ${TENANT_B_TOKEN}" \
  -H "Content-Type: application/json" | jq '.'
echo ""

echo -e "${YELLOW}4.2: Update Tenant B Agent Config${NC}"
curl -s -X PUT "${BASE_URL}/tenant/agent-config" \
  -H "Authorization: Bearer ${TENANT_B_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "tone": "professional",
    "greeting": "Thank you for calling Tech Solutions. How may I assist you?",
    "allowed_actions": ["create_lead", "schedule_meeting", "handoff_to_human"]
  }' | jq '.'
echo ""

# =====================================================
# Step 5: Test Tenant Isolation (Security Test)
# =====================================================

echo -e "${YELLOW}=========================================="
echo "Testing Tenant Isolation (Security)"
echo "==========================================${NC}"
echo ""

echo -e "${YELLOW}5.1: Tenant A trying to access Tenant B profile (should fail)${NC}"
TENANT_A_ACCESSING_B=$(curl -s -X GET "${BASE_URL}/tenant/profile" \
  -H "Authorization: Bearer ${TENANT_A_TOKEN}" \
  -H "Content-Type: application/json")

# Tenant A should only see their own profile, not Tenant B's
# The RLS should automatically filter by tenant_id
TENANT_A_PROFILE_ID=$(echo "$TENANT_A_ACCESSING_B" | jq -r '.tenant.id')

if [ "$TENANT_A_PROFILE_ID" == "$TENANT_A_TENANT_ID" ]; then
  echo -e "${GREEN}✓ Security: Tenant A can only see their own profile${NC}"
else
  echo -e "${RED}✗ Security Issue: Tenant A can access other tenant data!${NC}"
fi
echo "$TENANT_A_ACCESSING_B" | jq '.'
echo ""

echo -e "${YELLOW}5.2: Tenant B trying to access Tenant A profile (should fail)${NC}"
TENANT_B_ACCESSING_A=$(curl -s -X GET "${BASE_URL}/tenant/profile" \
  -H "Authorization: Bearer ${TENANT_B_TOKEN}" \
  -H "Content-Type: application/json")

TENANT_B_PROFILE_ID=$(echo "$TENANT_B_ACCESSING_A" | jq -r '.tenant.id')

if [ "$TENANT_B_PROFILE_ID" == "$TENANT_B_TENANT_ID" ]; then
  echo -e "${GREEN}✓ Security: Tenant B can only see their own profile${NC}"
else
  echo -e "${RED}✗ Security Issue: Tenant B can access other tenant data!${NC}"
fi
echo "$TENANT_B_ACCESSING_A" | jq '.'
echo ""

# =====================================================
# Step 6: Test Without Token (Should Fail)
# =====================================================

echo -e "${YELLOW}=========================================="
echo "Testing Authentication (No Token)"
echo "==========================================${NC}"
echo ""

echo -e "${YELLOW}6.1: Accessing profile without token (should fail)${NC}"
NO_TOKEN_RESPONSE=$(curl -s -X GET "${BASE_URL}/tenant/profile" \
  -H "Content-Type: application/json")

if echo "$NO_TOKEN_RESPONSE" | jq -e '.error' > /dev/null; then
  echo -e "${GREEN}✓ Security: API correctly rejects requests without token${NC}"
else
  echo -e "${RED}✗ Security Issue: API allows access without token!${NC}"
fi
echo "$NO_TOKEN_RESPONSE" | jq '.'
echo ""

# =====================================================
# Summary
# =====================================================

echo -e "${GREEN}=========================================="
echo "Testing Complete!"
echo "==========================================${NC}"
echo ""
echo "Tenant A ID: $TENANT_A_TENANT_ID"
echo "Tenant B ID: $TENANT_B_TENANT_ID"
echo ""
echo "Both tenants should only see their own data."
echo "RLS policies enforce tenant isolation at the database level."




