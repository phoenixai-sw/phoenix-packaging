.PHONY: dev down seed test
dev:
	docker compose up --build
down:
	docker compose down
seed:
	@test -n "$(EMAIL)" || (echo 'Usage: make seed EMAIL=your-google-account@example.com'; exit 1)
	docker compose exec api python -m services.api.seed --email "$(EMAIL)"
test:
	python -m pytest services/api/tests services/api/billing/tests tests/geometry_pdf
	npm test
	npm run typecheck
