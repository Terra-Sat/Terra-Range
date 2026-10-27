PYTHON ?= python3
PIPELINE_DIR := pipeline
BACKEND_DIR := backend

.PHONY: grid vci load briefing help

help:
	@echo "Available targets: make grid | make vci | make load | make briefing"

grid:
	@echo "[grid] Building analysis grid..."
	@$(PYTHON) -m terrarange.grid.build_grid

vci:
	@echo "[vci] Running rangeland VCI indicators..."
	@$(PYTHON) -m terrarange.indicators.rangeland

load:
	@echo "[load] Loading finished GeoParquet run into the Django app..."
	@cd $(BACKEND_DIR) && python manage.py load_run --help

briefing:
	@echo "[briefing] Generating briefing pack..."
	@$(PYTHON) -m briefings.build_briefing
