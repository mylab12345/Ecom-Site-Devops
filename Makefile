.PHONY: help up down logs build ps clean restart test seed health fmt \
        ci ci-deps ci-lint ci-test ci-test-docker ci-build ci-push ci-scan ci-smoke ci-all ci-clean \
        gitops-bump gitops-dry ci-plan doctor

# ── Phase 1: local stack ─────────────────────────────────────────────────────
help: ## Show help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-16s\033[0m %s\n", $$1, $$2}'

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
	@curl -s http://localhost:8080/health | python3 -m json.tool || curl -s http://localhost:8080/health
	@echo "\n=== Individual ==="
	@for p in 8001 8002 8003 8004 8005 8006 8007 8008 8009 8080; do echo -n "$$p: "; curl -s http://localhost:$$p/health | head -c 120; echo; done

seed: ## Seed demo data via API (requires services up)
	./scripts/seed.sh

test: ## Run integration smoke tests
	./scripts/test.sh

fmt: ## Check python syntax
	python3 -m py_compile services/*/app/main.py && echo "syntax OK"

# ── Phase 2: CI pipeline, same scripts Jenkins runs ─────────────────────────
# Any variable can be overridden: make ci-build SERVICES=product REGISTRY=docker.io/myhub
SERVICES ?= all
REGISTRY ?=
IMAGE_TAG ?=
PARALLEL ?= 4

doctor: ## Toolchain check (docker/buildx/trivy/hadolint/python)
	@bash scripts/ci/all.sh --doctor

ci: ci-lint ci-test ## Lint + tests, no docker needed (fast pre-push gate)

ci-deps: ## Install the CI tooling (pytest, ruff, PyYAML) into the active python
	python3 -m pip install -r requirements-dev.txt

ci-lint: ## Stage 1: ruff, hadolint, shellcheck, compose + structure contracts
	@bash scripts/ci/lint.sh

ci-test: ## Stage 2: pytest on the host (structure tier always, app tier if deps present)
	@bash scripts/ci/unit-tests.sh

ci-test-docker: ## Stage 2 exactly as Jenkins runs it (python:3.12-slim + all service deps)
	@ECOM_TEST_DOCKER=1 ECOM_TEST_TIER=all bash scripts/ci/unit-tests.sh

ci-plan: ## Show the resolved build matrix (services, tags, platforms) — no docker needed
	@bash scripts/ci/build.sh --print-plan --services "$(SERVICES)" --registry "$(REGISTRY)" --tag "$(IMAGE_TAG)"

ci-build: ## Stage 3: buildx --load for the host arch, then smoke test each image
	@bash scripts/ci/build.sh --services "$(SERVICES)" --load --parallel "$(PARALLEL)" --tag "$(IMAGE_TAG)"
	@bash scripts/ci/smoke.sh --services "$(SERVICES)" --registry '' --tag "$(IMAGE_TAG)"

ci-multiarch: ## Stage 3 as Jenkins runs it: amd64+arm64 (needs QEMU/binfmt, see jenkins/setup.md §4)
	@bash scripts/ci/build.sh --services "$(SERVICES)" --platforms linux/amd64,linux/arm64 --load --parallel "$(PARALLEL)"

ci-push: ## Stage 3+4: multi-arch build, push, then Trivy gate (needs REGISTRY=...)
	@test -n "$(REGISTRY)" || { echo "usage: make ci-push REGISTRY=docker.io/mylab12345 [IMAGE_TAG=v1]"; exit 1; }
	@bash scripts/ci/build.sh --services "$(SERVICES)" --push --registry "$(REGISTRY)" \
	  --platforms linux/amd64,linux/arm64 --cache --parallel "$(PARALLEL)" --tag "$(IMAGE_TAG)"
	@bash scripts/ci/scan.sh --services "$(SERVICES)" --registry "$(REGISTRY)" --mode gate --tag "$(IMAGE_TAG)"

ci-scan: ## Stage 4: Trivy against the tree (deps + Dockerfiles), report-only
	@bash scripts/ci/scan.sh --fs --mode report

ci-smoke: ## Stage 3.5: run each locally built image and probe /health + /metrics
	@bash scripts/ci/smoke.sh --services "$(SERVICES)" --registry '' --tag "$(IMAGE_TAG)"

ci-all: ## All seven stages in order, on this machine (Jenkins parity)
	@bash scripts/ci/all.sh --keep-going $(if $(REGISTRY),--registry "$(REGISTRY)",)

gitops-dry: ## Show what the GitOps bump would change (no commit)
	@bash scripts/ci/gitops-bump.sh --dry-run

gitops-bump: ## Commit (and optionally push) helm values image tags from .ci-output/images.txt
	@bash scripts/ci/gitops-bump.sh --commit $(if $(PUSH),--push,)

ci-clean: ## Remove CI artifacts (never touches the buildx layer cache)
	@rm -rf .ci-output && echo "removed .ci-output/"
