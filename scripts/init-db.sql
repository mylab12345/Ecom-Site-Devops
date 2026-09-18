-- Initialize 9 microservice databases on single Postgres instance (local dev)
-- Postgres 15: create databases via \l checks
SELECT 'CREATE DATABASE identity_db' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'identity_db')\gexec
SELECT 'CREATE DATABASE product_db' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'product_db')\gexec
SELECT 'CREATE DATABASE inventory_db' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'inventory_db')\gexec
SELECT 'CREATE DATABASE order_db' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'order_db')\gexec
SELECT 'CREATE DATABASE payment_db' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'payment_db')\gexec
SELECT 'CREATE DATABASE shipping_db' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'shipping_db')\gexec
SELECT 'CREATE DATABASE notification_db' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'notification_db')\gexec
SELECT 'CREATE DATABASE review_db' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'review_db')\gexec

-- Grant privileges
GRANT ALL PRIVILEGES ON DATABASE identity_db TO ecom;
GRANT ALL PRIVILEGES ON DATABASE product_db TO ecom;
GRANT ALL PRIVILEGES ON DATABASE inventory_db TO ecom;
GRANT ALL PRIVILEGES ON DATABASE order_db TO ecom;
GRANT ALL PRIVILEGES ON DATABASE payment_db TO ecom;
GRANT ALL PRIVILEGES ON DATABASE shipping_db TO ecom;
GRANT ALL PRIVILEGES ON DATABASE notification_db TO ecom;
GRANT ALL PRIVILEGES ON DATABASE review_db TO ecom;

-- Cart uses Redis, no DB needed, but create cart_db for optional fallback
SELECT 'CREATE DATABASE cart_db' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'cart_db')\gexec
GRANT ALL PRIVILEGES ON DATABASE cart_db TO ecom;
