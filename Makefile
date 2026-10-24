PY := .venv/bin/python

.PHONY: webapp webapp-install webapp-test webapp-sample

## Launch the review webapp (creates .venv and installs on first run).
webapp:
	./scripts/run_webapp.sh

webapp-install:
	test -x $(PY) || python3 -m venv .venv
	$(PY) -m pip install -e '.[perception,reasoning,webapp,dev]'

webapp-test:
	$(PY) -m pytest -q tests/webapp

## Rebuild the synthetic sample run in runs/exit-test-01.
webapp-sample:
	$(PY) -m foresmoke.webapp.sample --force
