PY ?= python
PROVIDER ?= mock

.PHONY: setup corpus test extract compare sweep reasoning route vote judge prototypes all clean

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

reasoning:
	$(PY) -m extraction.cli reasoning --provider $(PROVIDER)

route:
	$(PY) -m extraction.cli route --provider $(PROVIDER)

vote:
	$(PY) -m extraction.cli vote --provider $(PROVIDER)

judge:
	$(PY) -m extraction.cli judge --provider $(PROVIDER)

prototypes:
	$(PY) -m extraction.cli all --provider $(PROVIDER)

# Corpus -> tests -> every prototype in syllabus order.
all: corpus test prototypes

clean:
	rm -rf runs corpus .pytest_cache
	find . -name __pycache__ -type d -exec rm -rf {} +
