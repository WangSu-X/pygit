PYTHON ?= python3

.PHONY: test
test:
	$(PYTHON) -m pytest tests/ -v

.PHONY: test-quick
test-quick:
	$(PYTHON) -m pytest tests/ -q

.PHONY: clean
clean:
	rm -rf build/ dist/ *.egg-info __pycache__ .pytest_cache
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name '*.pyc' -delete

.PHONY: build
build:
	$(PYTHON) build_executable.py

# 向后兼容
.PHONY: check
check: test
