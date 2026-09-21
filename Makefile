# AI4DRR Chatbot — operator and developer entry points.  `make` / `make help` lists them.
#
# ENV selects the Compose configuration: dev (default) → docker-compose.yml,
# prod → docker-compose.prod.yml. prod is never implicit: always `ENV=prod`.
.DEFAULT_GOAL := help
SHELL := /bin/bash

ENV ?= dev
ifeq ($(ENV),dev)
  COMPOSE_FILE_ := docker-compose.yml
  PROJECT_ := chatbot
  CONTAINER_ := undrr-chatbot-dev
else ifeq ($(ENV),prod)
  COMPOSE_FILE_ := docker-compose.prod.yml
  PROJECT_ := chatbot-prod
  CONTAINER_ := undrr-chatbot-prod
else
  $(error Unknown ENV '$(ENV)'. Use ENV=dev (default) or ENV=prod)
endif
# Distinct Compose project names: both files live in one directory, so without
# them `docker compose -f docker-compose.prod.yml …` would act on the dev stack.
COMPOSE := docker compose -p $(PROJECT_) -f $(COMPOSE_FILE_)
APP := chatbot-app
# ssh/admin address the container by the name the compose file fixes, never by
# project lookup, so they cannot land in the other environment's container.
EXEC := docker exec

.PHONY: help up down ps logs ssh _running admin install format lint typecheck test check docker-check

help:
	@echo "AI4DRR Chatbot — make targets   (ENV=dev is the default; production needs ENV=prod explicitly)"
	@echo
	@echo "  make up    [ENV=dev|prod]                 build and start the stack in the background ($(COMPOSE_FILE_))"
	@echo "  make down  [ENV=dev|prod]                 stop and remove the stack's containers"
	@echo "  make ps    [ENV=dev|prod]                 show the stack's containers"
	@echo "  make logs  [ENV=dev|prod]                 follow the application log"
	@echo "  make ssh   [ENV=dev|prod]                 open a shell in the running $(APP) container"
	@echo "  make admin [ENV=dev|prod] EMAIL=<address> make <address> an administrator (see below)"
	@echo
	@echo "  make admin: an existing user is promoted; an unknown address gets a one-time account-setup"
	@echo "  link (printed here, valid INVITATION_TTL_HOURS) that creates the account as administrator."
	@echo "  Add SEND=1 to e-mail the link through EMAIL_TRANSPORT instead. Needs the stack up."
	@echo
	@echo "  Developer:  make install | format | lint | typecheck | test | check | docker-check"
	@echo
	@echo "  Selected: ENV=$(ENV)  →  $(COMPOSE_FILE_)  (project $(PROJECT_), container $(CONTAINER_))"

up:
	@echo "[$(ENV)] $(COMPOSE) up --build -d"
	$(COMPOSE) up --build -d

down:
	@echo "[$(ENV)] $(COMPOSE) down"
	$(COMPOSE) down

ps:
	@echo "[$(ENV)] $(COMPOSE) ps"
	$(COMPOSE) ps

logs:
	$(COMPOSE) logs -f $(APP)

ssh: _running
	@echo "[$(ENV)] shell in $(CONTAINER_) — exit with Ctrl-D"
	$(EXEC) -it $(CONTAINER_) bash

_running:
	@[ "$$(docker inspect -f '{{.State.Running}}' $(CONTAINER_) 2>/dev/null)" = "true" ] \
	  || { echo "Container $(CONTAINER_) (ENV=$(ENV)) is not running. Start it first: make up ENV=$(ENV)"; exit 1; }

# First-administrator bootstrap (app/db/admin_ops.py bootstrap). Runs inside the
# app container, where DATABASE_URL and APP_BASE_URL are configured; nothing
# secret is printed except the one-time setup link itself. prod asks first.
admin:
ifeq ($(strip $(EMAIL)),)
	$(error EMAIL is required: make admin EMAIL=user@example.org [ENV=dev|prod] [SEND=1])
endif
	@case "$(EMAIL)" in *@*.*) ;; *) echo "EMAIL '$(EMAIL)' does not look like an e-mail address"; exit 2;; esac
	@echo "=============================================================="
	@echo " Environment : $(ENV)  ($(COMPOSE_FILE_), project $(PROJECT_), container $(CONTAINER_))"
	@echo " Action      : make $(EMAIL) an administrator$(if $(SEND), (send the setup e-mail),)"
	@echo "=============================================================="
ifeq ($(ENV),prod)
	@if [ -z "$(YES)" ]; then \
	  read -r -p "This is PRODUCTION. Type 'prod' to continue: " answer; \
	  [ "$$answer" = "prod" ] || { echo "Aborted."; exit 1; }; \
	fi
endif
	@$(MAKE) --no-print-directory _running ENV=$(ENV)
	$(EXEC) -i $(CONTAINER_) python -m app.db.admin_ops bootstrap "$(EMAIL)" $(if $(SEND),--send,)

# --- developer targets (unchanged) ---------------------------------------------
# Run inside the project's Python 3.11 image when no local 3.11 toolchain exists: make docker-check
install:
	pip install -r requirements-dev.txt

format:
	ruff format app tests tools
	ruff check --fix app tests
	ruff check --fix tools --ignore T201

lint:
	ruff format --check app tests tools
	ruff check app tests
	ruff check tools --ignore T201

typecheck:
	mypy

test:
	pytest

check: lint typecheck test

# Same checks, executed in a throwaway container built from this project's image.
docker-check:
	docker build -t undrr-chatbot:dev . >/dev/null
	docker run --rm -v "$(CURDIR):/work" -w /work \
		-e PYTHONDONTWRITEBYTECODE=1 -e RUFF_CACHE_DIR=/tmp/ruff -e MYPY_CACHE_DIR=/tmp/mypy \
		undrr-chatbot:dev \
		sh -c "pip install -q -r requirements-dev.txt && ruff format --check app tests tools && ruff check app tests && ruff check tools --ignore T201 && mypy && pytest -p no:cacheprovider"
