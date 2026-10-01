.PHONY: venv install seed serve test eval health user recommend

VENV ?= .venv
PY   ?= $(VENV)/bin/python
ID ?= 15
LIMIT ?= 5

venv:
	python3 -m venv $(VENV)
	$(PY) -m pip install -q -r requirements.txt
	@echo "venv ready: source $(VENV)/bin/activate"

install:
	$(PY) -m pip install -r requirements.txt

seed:
	$(PY) -m app.seed

serve:
	$(PY) -m uvicorn app.api:app --reload --port 8000

test:
	$(PY) -m pytest -q

eval:
	$(PY) scripts/eval_loo.py

# CLI (in-process; ID=15 LIMIT=5 DEBUG=1; add URL=http://localhost:8000 to go through a running server)
CLI = $(PY) -m app.cli $(if $(URL),--url $(URL),)

health:
	$(CLI) health

user:
	$(CLI) user $(ID)

recommend:
	$(CLI) recommend $(ID) --limit $(LIMIT) $(if $(DEBUG),--debug,)
