.PHONY: mock worker webhooks e2e test

mock:
	@echo "Starting mock Terra Range API on http://localhost:8001"
	@python -m mock_api.app

worker:
	@echo "Starting lastmile worker"
	@python -m lastmile.delivery.worker

webhooks:
	@echo "Starting webhook receiver on http://localhost:8002"
	@python -m lastmile.webhooks.app

e2e:
	@echo "Running end-to-end smoke test"
	@pytest -m integration -q

test:
	@pytest -q
