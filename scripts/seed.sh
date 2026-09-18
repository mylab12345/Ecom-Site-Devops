#!/bin/bash
set -e
BASE=${GATEWAY:-http://localhost:8080}
echo "Seeding via $BASE"

# 1. Register user
echo ">> Register"
curl -s -X POST $BASE/api/auth/register -H "Content-Type: application/json" -d '{"email":"demo@ecom.test","username":"demo","password":"demo1234","full_name":"Demo User"}' | head -c 500; echo

echo ">> Login"
TOKEN=$(curl -s -X POST $BASE/api/auth/login -H "Content-Type: application/x-www-form-urlencoded" -d "username=demo&password=demo1234" | python3 -c "import sys,json; print(json.load(sys.stdin).get('access_token',''))")
echo "TOKEN=${TOKEN:0:20}..."

# Products are auto-seeded, verify
echo ">> Products"
curl -s $BASE/api/products?limit=3 | python3 -m json.tool | head -n 40

# Add to cart
echo ">> Add to cart"
curl -s -X POST $BASE/api/cart/demo/items -H "Content-Type: application/json" -d '{"product_id":1,"quantity":2}' | python3 -m json.tool | head -n 40

# Create order
echo ">> Create order"
curl -s -X POST $BASE/api/orders -H "Content-Type: application/json" -d '{"user_id":"demo","items":[{"product_id":1,"quantity":2}],"shipping_address":{"street":"123 Main","city":"NYC","zip":"10001"}}' | python3 -m json.tool | head -n 60

echo "Seed done"
