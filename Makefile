.PHONY: dev down seed test
dev:
	docker compose up --build
down:
	docker compose down
seed:
	docker compose exec api python -m services.api.seed
test:
	python -m pytest services/api/tests tests/geometry_pdf
	npm run typecheck
