PY ?= python
PROVIDER ?= mock

.PHONY: setup corpus test extract compare sweep all clean

setup:
	$(PY) -m venv .venv && .venv/bin/pip install -q -r requirements.txt
	@echo "activate with: source .venv/bin/activate"

corpus:
	$(PY) -m extraction.cli build-corpus

test:
	$(PY) -m pytest tests -q

extract:
	$(PY) -m extraction.cli extract --provider $(PROVIDER)

compare:
	$(PY) -m extraction.cli compare --provider $(PROVIDER)

sweep:
	$(PY) -m extraction.cli sweep --provider $(PROVIDER) --repetitions 5

# Everything, offline: corpus -> tests -> four prompt arms -> temperature sweep.
all: corpus test compare sweep

clean:
	rm -rf runs corpus .pytest_cache
	find . -name __pycache__ -type d -exec rm -rf {} +
