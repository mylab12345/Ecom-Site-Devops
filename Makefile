.PHONY: help up down logs build ps clean restart test seed health

help: ## Show help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'

up: ## Start all services (docker compose up -d --build)
	docker compose up -d --build
	@echo "Waiting 40s for health checks..."
	sleep 40
	make health

down: ## Stop and remove containers
	docker compose down

logs: ## Tail logs
	docker compose logs -f --tail=200

build: ## Build all images with buildx (multi-arch ready)
	docker buildx build --platform linux/amd64,linux/arm64 -f services/identity/Dockerfile services/identity --tag ecom-identity:local --load || docker compose build

ps: ## List containers
	docker compose ps

clean: ## Remove volumes and orphans
	docker compose down -v --remove-orphans
	docker system prune -f

restart: ## Restart a service: make restart s=product
	docker compose restart $(s)

health: ## Check health of all services via gateway
	@echo "=== Gateway aggregated health ==="
	curl -s http://localhost:8080/health | python3 -m json.tool || curl -s http://localhost:8080/health
	@echo "\n=== Individual ==="
	@for p in 8001 8002 8003 8004 8005 8006 8007 8008 8009 8080; do echo -n "$$p: "; curl -s http://localhost:$$p/health | head -c 120; echo; done

seed: ## Seed demo data via API (requires services up)
	./scripts/seed.sh

test: ## Run integration smoke tests
	./scripts/test.sh

fmt: ## Check python syntax
	python3 -m py_compile services/*/app/main.py && echo "syntax OK"
