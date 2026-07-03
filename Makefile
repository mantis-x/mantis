.PHONY: help up down migrate ingest detect enrich deliver execute test lint contracts clean

help:
	@echo ""
	@echo "  Mantis — Scout + Execute"
	@echo "  ─────────────────────────────────"
	@echo "  make up        Start postgres + redis"
	@echo "  make migrate   Run DB migrations"
	@echo "  make ingest    Start ingestion worker"
	@echo "  make detect    Start detection worker"
	@echo "  make enrich    Start enrichment worker"
	@echo "  make deliver   Start Telegram/Discord/LINE delivery (Track 2)"
	@echo "  make execute   Start Mantis Execute agent (Track 6)"
	@echo "  make all       Start full pipeline (all workers)"
	@echo "  make test      Run all test suites"
	@echo "  make lint      Run ruff linter across all packages"
	@echo "  make contracts Deploy contracts to Mantle Sepolia"
	@echo "  make backtest  Replay historical data through detection"
	@echo "  make clean     Stop and remove containers + volumes"
	@echo ""

up:
	docker-compose up -d postgres redis

down:
	docker-compose down

clean:
	docker-compose down -v

migrate:
	cd packages/shared && python -m src.db.migrations

ingest:
	cd packages/ingestion && python -m src.worker

detect:
	cd packages/detection && python -m src.detector

enrich:
	cd packages/enrichment && python -m src.worker

deliver:
	cd packages/delivery && python -m src.worker

execute:
	cd packages/executor && python -m src.worker

all:
	docker-compose up

test:
	cd packages/ingestion  && python -m pytest tests/ -v
	cd packages/detection  && python -m pytest tests/ -v
	cd packages/enrichment && python -m pytest tests/ -v
	cd packages/delivery   && python -m pytest tests/ -v
	cd packages/executor   && python -m pytest tests/ -v

lint:
	ruff check packages/

contracts:
	cd contracts && npx hardhat run scripts/deploy_audit_log.js --network mantleSepolia
	cd contracts && npx hardhat run scripts/deploy_agent_identity.js --network mantleSepolia

verify:
	cd contracts && npx hardhat run scripts/verify.js --network mantleSepolia

backtest:
	python scripts/backtest_signals.py

health:
	python scripts/check_rpc_health.py
