.PHONY: install run

# Virtual environment directory
PYTHON := python
PIP := $(PYTHON) -m pip
UVICORN := uvicorn

install:
	@# Ensure Python version is sufficient
	@$(PYTHON) -c 'import sys; assert sys.version_info >= (3,13), "ERROR: Python >= 3.13 is required. Use pyenv or install python3.13"; print("Python OK")'
	@echo "Creating virtualenv at '$(VENV)' (if missing) and installing dependencies with 'uv'."; \
	if [ ! -d "$(VENV)" ]; then \
		$(PYTHON) -m venv "$(VENV)"; \
	else \
		"$(VENV)/bin/python" -c 'import sys; assert sys.version_info >= (3,13)' || (rm -rf "$(VENV)" && $(PYTHON) -m venv "$(VENV)"); \
	fi; \
	"$(PIP)" -m pip install --upgrade pip setuptools wheel uv; \
	# Install runtime deps directly into the venv so dev run works reliably
	"$(PIP)" -m pip install fastapi uvicorn[standard] python-jose[cryptography] cryptography requests python-multipart; \
	if [ -f worker/requirements.txt ]; then \
		"$(PIP)" -m pip install -r worker/requirements.txt; \
	fi

lint: ## Run all linting tools
	@echo "Installing linting tools..."
	@$(PIP) install --quiet --upgrade pycln isort ruff
	@echo "Running Ruff checks..."
	@$(PYTHON) -m ruff check --fix --exit-zero
	@echo "Cleaning unused imports..."
	@$(PYTHON) -m pycln .
	@echo "Sorting imports..."
	@$(PYTHON) -m isort .
	@echo "Formatting code..."
	@$(PYTHON) -m ruff format $(SRC_DIR)
	@echo "Linting complete!"

run:
	"$(UVICORN)" app.main:service --host 0.0.0.0 --port 8890

t: ## Run odata battery tests
	@echo "Running odata battery tests (tests/test_odata_battery.py)"
	@$(PYTHON) -m pytest tests/test_odata_battery.py -q
