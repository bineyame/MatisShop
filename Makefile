# =============================================================================
# Mati Retail Platform - developer commands
#
#   make up         start everything
#   make bootstrap  create and populate the demo database
#   make verify     run the full end-to-end verification
#   make reset      return to a known demo state
#
# Windows users without GNU make: use ./make.ps1 <target> instead.
# =============================================================================

SHELL := /bin/bash
COMPOSE ?= docker compose
ODOO_DB ?= odoo
ADDONS ?= mati_demo

.DEFAULT_GOAL := help
.PHONY: help up down restart logs logs-odoo logs-gateway ps build bootstrap seed reset \
        verify test test-gateway test-odoo shell-odoo shell-gateway psql-odoo psql-gateway \
        demo-reset \
        health fiscal-fail fiscal-ok clean nuke lint env

help: ## Show this help
	@echo "Mati Retail Platform"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'
	@echo ""

env: ## Create .env from .env.example if missing
	@test -f .env || (cp .env.example .env && echo "created .env from .env.example")

build: env ## Build the Odoo and gateway images
	$(COMPOSE) build

up: env ## Start postgres, odoo and the gateway
	$(COMPOSE) up -d --build
	@echo ""
	@echo "  Odoo    http://localhost:8069"
	@echo "  Gateway http://localhost:8000  (docs at /docs)"
	@echo ""
	@echo "  Next:  make bootstrap"

down: ## Stop all services (keeps data)
	$(COMPOSE) down

restart: ## Restart odoo and the gateway
	$(COMPOSE) restart odoo gateway

ps: ## Show service status
	$(COMPOSE) ps

logs: ## Tail all logs
	$(COMPOSE) logs -f --tail=100

logs-odoo: ## Tail Odoo logs
	$(COMPOSE) logs -f --tail=100 odoo

logs-gateway: ## Tail gateway logs
	$(COMPOSE) logs -f --tail=100 gateway

bootstrap: up ## Create the demo database and install every module
	./scripts/bootstrap.sh

seed: ## Re-run demo seeding (idempotent)
	./scripts/seed_demo.sh

reset: ## Restore the seeded opening state
	./scripts/reset_demo.sh

demo-reset: ## Full demo restore: re-seed, reset stock, restore mock providers
	./scripts/demo_reset.sh

verify: ## Run the full end-to-end verification
	./scripts/verify.sh

test: test-gateway test-odoo ## Run every test suite

test-gateway: ## Run the gateway test suite
	$(COMPOSE) run --rm --no-deps -e GATEWAY_DATABASE_URL=sqlite+aiosqlite:///./test.db \
		gateway test -q

test-odoo: ## Run the Odoo addon tests
	$(COMPOSE) run --rm odoo odoo -d $(ODOO_DB) --test-enable --stop-after-init \
		--test-tags /et_fiscal_odoo,/external_payment_gateway,/external_delivery_gateway,/mati_demo \
		-u et_fiscal_odoo,external_payment_gateway,external_delivery_gateway,mati_demo \
		--log-level=test

shell-odoo: ## Open an Odoo shell
	$(COMPOSE) exec odoo mati-entrypoint.sh odoo shell -d $(ODOO_DB) --no-http

shell-gateway: ## Open a shell in the gateway container
	$(COMPOSE) exec gateway /bin/sh

psql-odoo: ## Open psql on the Odoo database
	$(COMPOSE) exec postgres psql -U $${POSTGRES_USER:-odoo} -d $(ODOO_DB)

psql-gateway: ## Open psql on the gateway database
	$(COMPOSE) exec postgres psql -U $${POSTGRES_USER:-odoo} -d integration_gateway

health: ## Check every service health endpoint
	./scripts/health.sh

fiscal-fail: ## Force the mock fiscal provider to fail
	@curl -sS -X POST http://localhost:8000/api/v1/admin/mock/fiscal/failure-mode \
		-H "X-API-Key: $$(grep -E '^GATEWAY_API_KEY=' .env | cut -d= -f2-)" \
		-H "Content-Type: application/json" -d '{"enabled": true}'
	@echo ""

fiscal-ok: ## Restore the mock fiscal provider
	@curl -sS -X POST http://localhost:8000/api/v1/admin/mock/fiscal/failure-mode \
		-H "X-API-Key: $$(grep -E '^GATEWAY_API_KEY=' .env | cut -d= -f2-)" \
		-H "Content-Type: application/json" -d '{"enabled": false}'
	@echo ""

lint: ## Lint the gateway
	$(COMPOSE) run --rm --no-deps gateway sh -c "pip install -q ruff && ruff check app tests"

clean: ## Stop services and remove containers (keeps volumes)
	$(COMPOSE) down --remove-orphans

nuke: ## Remove containers AND all data. Destructive.
	$(COMPOSE) down -v --remove-orphans
	@echo "all containers and volumes removed; run 'make up && make bootstrap' to rebuild"
