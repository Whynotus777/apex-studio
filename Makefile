SHELL := /bin/bash
.DEFAULT_GOAL := dev

.PHONY: dev seed api web clean test

dev:
	@bash scripts/dev.sh

seed:
	@bash scripts/seed.sh

api:
	@bash scripts/run_api.sh

web:
	@bash scripts/run_web.sh

clean:
	@bash scripts/clean.sh

test:
	@bash scripts/smoke_test.sh
