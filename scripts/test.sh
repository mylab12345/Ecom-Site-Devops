#!/bin/bash
set -e
BASE=${BASE:-http://localhost:8080}
FAIL=0
pass(){ echo "✅ $1"; }
fail(){ echo "❌ $1"; FAIL=1; }

echo "=== ECom Smoke Tests ==="
# health
if curl -sf $BASE/health | grep -q "healthy\|degraded"; then pass "gateway /health"; else fail "gateway /health"; fi
for svc in identity product inventory cart order payment shipping notification review; do
  code=$(curl -s -o /dev/null -w "%{http_code}" $BASE/api/products 2>/dev/null || true)
  : # we just check products via gateway
done

# product list
if curl -sf $BASE/api/products | grep -q "Wireless"; then pass "GET /api/products"; else fail "GET /api/products"; fi

# inventory
if curl -sf $BASE/api/inventory/1 | grep -q "available"; then pass "GET /api/inventory/1"; else fail "GET /api/inventory/1"; fi

# cart
curl -sf -X POST $BASE/api/cart/testuser/items -H "Content-Type: application/json" -d '{"product_id":2,"quantity":1}' >/dev/null && pass "POST /api/cart" || fail "POST /api/cart"
if curl -sf $BASE/api/cart/testuser | grep -q "testuser"; then pass "GET /api/cart/testuser"; else fail "GET /api/cart/testuser"; fi

# auth
RND=$RANDOM
if curl -sf -X POST $BASE/api/auth/register -H "Content-Type: application/json" -d "{\"email\":\"test$RND@e.com\",\"username\":\"test$RND\",\"password\":\"pass1234\"}" | grep -q "test$RND"; then pass "POST /api/auth/register"; else fail "POST /api/auth/register"; fi
if curl -sf -X POST $BASE/api/auth/login -H "Content-Type: application/x-www-form-urlencoded" -d "username=test$RND&password=pass1234" | grep -q "access_token"; then pass "POST /api/auth/login"; else fail "POST /api/auth/login"; fi
TOKEN=$(curl -s -X POST $BASE/api/auth/login -H "Content-Type: application/x-www-form-urlencoded" -d "username=test$RND&password=pass1234" | python3 -c "import sys,json; print(json.load(sys.stdin).get('access_token',''))")

# order
ORDER_RESP=$(curl -s -X POST $BASE/api/orders -H "Content-Type: application/json" -d '{"user_id":"testuser","items":[{"product_id":3,"quantity":1}]}')
if echo "$ORDER_RESP" | grep -q '"id"'; then pass "POST /api/orders"; ORDER_ID=$(echo $ORDER_RESP | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',1))"); else fail "POST /api/orders"; ORDER_ID=1; fi

# payment
PAY_RESP=$(curl -s -X POST $BASE/api/payments -H "Content-Type: application/json" -d "{\"order_id\":$ORDER_ID,\"user_id\":\"testuser\",\"amount\":24.50}")
if echo "$PAY_RESP" | grep -q "pending\|success"; then pass "POST /api/payments"; PAY_ID=$(echo $PAY_RESP | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',1))"); else fail "POST /api/payments"; PAY_ID=1; fi
if curl -sf -X POST $BASE/api/payments/$PAY_ID/process -H "Content-Type: application/json" -d '{}' | grep -q "success\|failed"; then pass "POST /api/payments/{id}/process"; else fail "POST /api/payments/{id}/process"; fi

# shipping
SHIP_RESP=$(curl -s -X POST $BASE/api/shipments -H "Content-Type: application/json" -d "{\"order_id\":$ORDER_ID,\"user_id\":\"testuser\",\"address\":{}}")
if echo "$SHIP_RESP" | grep -q "tracking"; then pass "POST /api/shipments"; else echo "$SHIP_RESP" | head -c 200; fail "POST /api/shipments"; fi

# review
if curl -sf -X POST $BASE/api/reviews -H "Content-Type: application/json" -d '{"product_id":3,"user_id":"testuser","rating":5,"title":"Great","comment":"Excellent"}' | grep -q "Great"; then pass "POST /api/reviews"; else fail "POST /api/reviews"; fi
if curl -sf $BASE/api/reviews/product/3 | grep -q "rating"; then pass "GET /api/reviews/product/3"; else fail "GET /api/reviews/product/3"; fi
if curl -sf $BASE/api/reviews/product/3/stats | grep -q "average_rating"; then pass "GET /api/reviews/product/3/stats"; else fail "GET /api/reviews/product/3/stats"; fi

# notification
if curl -sf $BASE/api/notifications/user/testuser | grep -q "testuser\|\\[\\]"; then pass "GET /api/notifications/user/testuser"; else fail "GET /api/notifications/user/testuser"; fi

echo "---"
if [ $FAIL -eq 0 ]; then echo "All smoke tests passed 🎉"; exit 0; else echo "Some tests failed"; exit 1; fi
