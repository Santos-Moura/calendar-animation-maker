PYTHON ?= python
RUN_ID ?= cayde-final-216f-6fps-cyan-magenta-rdate-126x72-36s-01
PROFILE ?= account-b
INPUT ?= input.mp4
MODE ?= header_preserved_fill
RESOLUTION ?= 1512x864

.DEFAULT_GOAL := help

.PHONY: help setup install lint format format-check typecheck test check run \
	plan-216 preflight-216 upload-216 capture-216 recompose-216 compose-216 mux-216

help: ## List common development and final-pipeline commands.
	@printf '%s\n' \
	  'Development' \
	  '  make setup          Install the package and development tools.' \
	  '  make check          Run lint, formatting, typing, and tests.' \
	  '  make run            Show the CLI help.' \
	  '' \
	  'Final 216-frame workflow' \
	  '  make plan-216       Build plans locally; no Google access.' \
	  '  make preflight-216  Run the read-only Google preflight.' \
	  '  make upload-216     Upload with confirmation and resume.' \
	  '  make capture-216    Capture all weeks read-only with resume.' \
	  '  make recompose-216  Add the approved Calendar toolbar locally.' \
	  '  make compose-216    Build the silent MP4 locally.' \
	  '  make mux-216        Add the exact source-audio clip locally.' \
	  '' \
	  'Override defaults with NAME=value, for example PROFILE=account-b.'

setup install: ## Install the editable package with development dependencies.
	$(PYTHON) -m pip install -e ".[dev]"

lint: ## Run Ruff lint checks.
	$(PYTHON) -m ruff check .

format: ## Format Python source and tests.
	$(PYTHON) -m ruff format .

format-check: ## Check formatting without changing files.
	$(PYTHON) -m ruff format --check .

typecheck: ## Run strict mypy checks.
	$(PYTHON) -m mypy src

test: ## Run the offline test suite.
	$(PYTHON) -m pytest

check: lint format-check typecheck test ## Run every quality gate.

run: ## Show the top-level CLI help.
	$(PYTHON) -m calendar_anim --help

plan-216: ## Build the locked 216-frame plan locally.
	$(PYTHON) -m calendar_anim calendar prepare-cayde-216 --run-id $(RUN_ID) --input $(INPUT)

preflight-216: ## Verify account, calendar, and clean weeks; read-only Google API.
	$(PYTHON) -m calendar_anim calendar preflight-cayde-216 --run-id $(RUN_ID) --profile $(PROFILE) --execute

upload-216: ## Upload recurring parents; real writes, confirmation, and resume enabled.
	$(PYTHON) -m calendar_anim calendar upload-cayde-216-recurrence --run-id $(RUN_ID) --profile $(PROFILE) --input $(INPUT) --resume --execute

capture-216: ## Capture all 216 weeks through the persistent browser profile.
	$(PYTHON) -m calendar_anim calendar capture-final-cayde-216 --run-id $(RUN_ID) --profile $(PROFILE) --frames 1-216 --mode $(MODE) --resolution $(RESOLUTION) --resume --execute

recompose-216: ## Add the Calendar toolbar to completed captures locally.
	$(PYTHON) -m calendar_anim calendar recompose-final-cayde-216-calendar-toolbar --run-id $(RUN_ID) --profile $(PROFILE) --frames 1-216 --mode $(MODE) --resolution $(RESOLUTION) --execute

compose-216: ## Compose the approved toolbar frames into a silent MP4.
	$(PYTHON) -m calendar_anim calendar compose-final-cayde-216-calendar-toolbar --run-id $(RUN_ID) --mode $(MODE) --resolution $(RESOLUTION)

mux-216: ## Mux the exact source audio into the composed MP4.
	$(PYTHON) -m calendar_anim calendar mux-final-cayde-216-calendar-toolbar-audio --run-id $(RUN_ID) --mode $(MODE) --resolution $(RESOLUTION) --source-video $(INPUT)
