# neon / neon-the-g1 — canonical entrypoint.
# Targets are short verbs. Run `make help` for the menu.

SHELL       := /usr/bin/env bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

VENV        ?= .venv
# Auto-detect if we need sudo for docker (true if user not in docker group)
DOCKER      ?= $(shell groups | grep -qw docker && echo "docker" || echo "sudo docker")
PY          ?= $(VENV)/bin/python
PIP         ?= $(VENV)/bin/pip
IFACE       ?= eth0
SDK_DIR     ?= $(CURDIR)/unitree_sdk2_python
SDK_URL     ?= https://github.com/unitreerobotics/unitree_sdk2_python.git
CDDS_URI    ?= $(firstword $(wildcard /home/unitree/cyclonedds_ws/cyclonedds.xml $(CURDIR)/cyclonedds.xml))

export G1_IFACE       := $(IFACE)
export CYCLONEDDS_URI := $(CDDS_URI)
export PYTHONPATH     := $(SDK_DIR):$(PYTHONPATH)

# ─── user-facing ──────────────────────────────────────────────────────────

.PHONY: help
help: ## show this menu
	@awk -F':|##' '/^[a-zA-Z_-]+:.*##/ {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$NF}' $(MAKEFILE_LIST)

.PHONY: setup
setup: ## 👋 first-run onboarding: create .env, then run + persist to systemd
	@if [ ! -f .env ]; then \
		cp .env.example .env; \
		echo "📝 created .env from .env.example"; \
		echo ""; \
		echo "   → EDIT .env now and fill in your secrets:"; \
		echo "       AWS_BEARER_TOKEN_BEDROCK, OPENAI_API_KEY, (optional TELEGRAM_BOT_TOKEN)"; \
		echo ""; \
		echo "   Then re-run:  make setup"; \
		exit 0; \
	fi
	@echo "✅ .env present — building + starting neon (docker) …"
	@$(DOCKER) compose up --build -d
	@echo ""
	@echo "🔒 persisting to systemd so it survives reboots …"
	@$(MAKE) install-service-docker
	@echo ""
	@echo "🎉 neon is live and will auto-start on boot."
	@echo "   REPL:      make run        (attach to the agent)"
	@echo "   logs:      make service-logs"
	@echo "   dashboard: https://$$(hostname -I | awk '{print $$1}'):8080"

.PHONY: persist
persist: install-service-docker ## persist the docker stack to systemd (survives reboots)

.PHONY: run
run: ## start the agent in docker (canonical entrypoint — auto-starts g1_speak)
	@echo "🤖 neon docker — building if needed, then attaching to REPL"
	@$(DOCKER) compose up --build -d
	@$(DOCKER) compose attach neon-agent || $(DOCKER) compose exec neon-agent python agent.py

.PHONY: dashboard
dashboard: ## start ONLY the neon-dashboard container (FastAPI + React UI)
	@echo "🌐 neon-dashboard → https://<robot-ip>:8080"
	@$(DOCKER) compose up --build -d neon-dashboard
	@$(DOCKER) compose logs -f --tail=30 neon-dashboard

.PHONY: dashboard-logs
dashboard-logs: ## tail neon-dashboard logs
	@$(DOCKER) compose logs -f --tail=80 neon-dashboard

.PHONY: run-bare
run-bare: venv sdk ## start the agent on host venv (DEV ONLY — pyrealsense2 may break on aarch64)
	@echo "🤖 neon bare-metal — IFACE=$(IFACE)  CYCLONEDDS_URI=$(CYCLONEDDS_URI)"
	@$(PY) agent.py

.PHONY: shell
shell: venv sdk ## drop into a python shell with tools loaded
	@$(PY) -ic "from tools import *; print('loaded G1_ALL_TOOLS (%d tools)' % len(G1_ALL_TOOLS))"

# ─── setup ────────────────────────────────────────────────────────────────

.PHONY: venv
venv: $(VENV)/.installed ## create .venv + install requirements.txt

$(VENV)/.installed: requirements.txt
	@test -d $(VENV) || $(shell command -v python3.11 || command -v python3) -m venv $(VENV)
	@$(PIP) install --quiet --upgrade pip
	@$(PIP) install --quiet -r requirements.txt
	@touch $@
	@echo "✅ venv ready: $(VENV)"

.PHONY: sdk
sdk: $(SDK_DIR)/.cloned ## clone + pip install -e unitree_sdk2_python

$(SDK_DIR)/.cloned:
	@test -d $(SDK_DIR) || git clone --depth 1 $(SDK_URL) $(SDK_DIR)
	@$(PIP) install --quiet -e $(SDK_DIR)
	@touch $@
	@echo "✅ SDK installed editable at $(SDK_DIR)"

# ─── docker ───────────────────────────────────────────────────────────────

.PHONY: build
build: ## build the docker image (slow first time — librealsense from source)
	@$(DOCKER) compose build

.PHONY: up
up: ## $(DOCKER) compose up (detached)
	@$(DOCKER) compose up -d
	@$(DOCKER) compose logs -f --tail=20

.PHONY: down
down: stop ## stop EVERYTHING — docker + voice + tg + thinker + bt
	@$(DOCKER) compose down 2>/dev/null || true
	@echo "✅ all services down"

.PHONY: docker-down
docker-down: ## $(DOCKER) compose down (docker only)
	@$(DOCKER) compose down

.PHONY: stop
stop: voice-stop tg-stop thinker-stop bt-stop ## stop all bare-metal listeners (voice, tg, thinker, bt)
	@echo "✅ bare-metal listeners stopped"

.PHONY: voice-stop
voice-stop: ## stop the voice listener (systemd + bare-metal)
	@sudo systemctl stop neon-voice.service 2>/dev/null && echo "🔇 systemd neon-voice stopped" || true
	@set +u; pids=$$(pgrep -af "python.*g1_speech_listener.py" 2>/dev/null | grep -v "sh -c" | grep -v "make " | awk '{print $$1}' || true); \
	if [ -n "$$pids" ]; then \
		echo "$$pids" | xargs -r kill -TERM 2>/dev/null || true; \
		sleep 1; \
		echo "$$pids" | xargs -r kill -KILL 2>/dev/null || true; \
		echo "🔇 voice listener killed (PIDs: $$pids)"; \
	else \
		echo "ℹ️  no bare-metal voice listener"; \
	fi

.PHONY: tg-stop
tg-stop: ## stop the telegram listener (systemd + bare-metal)
	@sudo systemctl stop neon-telegram.service 2>/dev/null && echo "💬 systemd neon-telegram stopped" || true
	@set +u; pids=$$(pgrep -af "python.*telegram_listener.py" 2>/dev/null | grep -v "sh -c" | grep -v "make " | awk '{print $$1}' || true); \
	if [ -n "$$pids" ]; then \
		echo "$$pids" | xargs -r kill -TERM 2>/dev/null || true; \
		sleep 1; \
		echo "$$pids" | xargs -r kill -KILL 2>/dev/null || true; \
		echo "💬 telegram listener killed (PIDs: $$pids)"; \
	else \
		echo "ℹ️  no bare-metal telegram listener"; \
	fi

.PHONY: thinker-stop
thinker-stop: ## stop the slow-thinker loop (systemd + bare-metal)
	@sudo systemctl stop neon-thinker.service 2>/dev/null && echo "🧠 systemd neon-thinker stopped" || true
	@set +u; pids=$$(pgrep -af "python.*thinker_loop.py" 2>/dev/null | grep -v "sh -c" | grep -v "make " | awk '{print $$1}' || true); \
	if [ -n "$$pids" ]; then \
		echo "$$pids" | xargs -r kill -TERM 2>/dev/null || true; \
		sleep 1; \
		echo "$$pids" | xargs -r kill -KILL 2>/dev/null || true; \
		echo "🧠 thinker killed (PIDs: $$pids)"; \
	else \
		echo "ℹ️  no bare-metal thinker"; \
	fi

.PHONY: ps
ps: ## show all neon background processes (bare-metal + systemd + docker)
	@echo "── bare-metal listeners ──"
	@ps -ef | grep -E '(g1_speech_listener|telegram_listener|thinker_loop|agent\.py)' | grep -v grep || echo "  (none)"
	@echo "── systemd services ──"
	@for svc in neon-voice neon-telegram neon-thinker; do \
		state=$$(systemctl is-active $$svc.service 2>/dev/null || true); \
		[ -z "$$state" ] && state="not-installed"; \
		echo "  $$svc.service: $$state"; \
	done
	@echo "── docker ──"
	@$(DOCKER) compose ps 2>/dev/null || true

.PHONY: logs
logs: ## tail container logs
	@$(DOCKER) compose logs -f --tail=100

.PHONY: exec
exec: ## exec into the running container
	@$(DOCKER) compose exec neon-agent bash

# ─── systemd (on the robot) ───────────────────────────────────────────────

.PHONY: install-service
install-service: ## install systemd unit (bare-metal mode)
	@sudo cp neon.service /etc/systemd/system/
	@sudo systemctl daemon-reload
	@sudo systemctl enable --now neon
	@echo "✅ installed; follow with:  make service-logs"

.PHONY: install-service-docker
install-service-docker: ## install systemd unit (docker-compose mode) — survives reboots
	@sed "s#__WORKDIR__#$(CURDIR)#g" neon-docker.service | sudo tee /etc/systemd/system/neon-docker.service >/dev/null
	@sudo systemctl daemon-reload
	@sudo systemctl enable --now neon-docker
	@echo "✅ neon-docker.service installed + enabled (WorkingDirectory=$(CURDIR))"
	@echo "   it will auto-start the compose stack on every boot."

.PHONY: service-logs
service-logs: ## tail the systemd service log (neon + neon-docker)
	@sudo journalctl -u neon -u neon-docker -f -n 100

.PHONY: uninstall-service
uninstall-service: ## stop + remove both service units
	@sudo systemctl disable --now neon neon-docker 2>/dev/null || true
	@sudo rm -f /etc/systemd/system/neon.service /etc/systemd/system/neon-docker.service
	@sudo systemctl daemon-reload


.PHONY: clean
clean: ## nuke venv + sdk clone
	@rm -rf $(VENV) $(SDK_DIR) .pytest_cache tools/__pycache__ tests/__pycache__
	@find . -name '*.pyc' -delete
	@echo "🧹 clean"

.PHONY: info
info: ## print env resolved by the Makefile
	@echo "IFACE:          $(IFACE)"
	@echo "VENV:           $(VENV)"
	@echo "SDK_DIR:        $(SDK_DIR)"
	@echo "CYCLONEDDS_URI: $(CYCLONEDDS_URI)"
	@echo "PYTHONPATH:     $(PYTHONPATH)"

.PHONY: voice
voice: venv ## run the bidi voice listener in foreground (Brio mic → G1 chest speaker)
	@$(PY) g1_speech_listener.py

.PHONY: tg
tg: venv ## run the telegram listener in foreground (multi-turn agent + voice control)
	@$(PY) telegram_listener.py

.PHONY: voice-bg
voice-bg: venv ## launch voice listener in background (logs → .logs/voice.log)
	@mkdir -p .logs
	@nohup $(PY) g1_speech_listener.py > .logs/voice.log 2>&1 &
	@echo "🎙 voice listener started (PID=$$!), tail .logs/voice.log"

.PHONY: tg-bg
tg-bg: venv ## launch telegram listener in background (logs → .logs/tg.log)
	@mkdir -p .logs
	@nohup $(PY) telegram_listener.py > .logs/tg.log 2>&1 &
	@echo "💬 telegram listener started (PID=$$!), tail .logs/tg.log"

.PHONY: thinker
thinker: venv ## run the slow-thinker reflective loop (every 30s) in foreground
	@$(PY) thinker_loop.py

.PHONY: thinker-bg
thinker-bg: venv ## launch slow-thinker in background (logs → .logs/thinker.log)
	@mkdir -p .logs
	@nohup $(PY) thinker_loop.py >> .logs/thinker.log 2>&1 & echo "thinker pid=$$!"

.PHONY: thinker-once
thinker-once: venv ## run a SINGLE thinker cycle and exit (for debugging)
	@$(PY) -c "from thinker_loop import _build, cycle; a=_build(); cycle(a)"


.PHONY: mute
mute: venv ## mute the voice agent (mic input → silence)
	@$(PY) -c "from tools.memory import memory; memory(action='kv_set', key='voice.muted', value='true'); print('🔇 muted')"

.PHONY: unmute
unmute: venv ## unmute the voice agent
	@$(PY) -c "from tools.memory import memory; memory(action='kv_set', key='voice.muted', value='false'); print('🎙 unmuted')"

.PHONY: voice-status
voice-status: venv ## show voice mute state
	@$(PY) -c "from tools.memory import memory; v=memory(action='kv_get', key='voice.muted'); print('🔇 MUTED' if str(v).strip().lower() in ('1','true','yes','on') else '🟢 LIVE')"

.PHONY: voice-push
voice-push: venv ## push manual briefing: make voice-push MSG="hi"
	@$(PY) -c "from tools.voice_bridge import push; print(push('manual', '$(MSG)', importance=1))"

.PHONY: voice-bridge
voice-bridge: venv ## show pending briefings count
	@$(PY) -c "from tools.voice_bridge import stats; print(stats())"

.PHONY: log-stats
log-stats: venv ## unified agent_log statistics
	@$(PY) -c "from tools.agent_log import stats; print(stats())"

.PHONY: log-show
log-show: venv ## show last 30 turns from unified agent log
	@$(PY) -c "from tools.agent_log import format_for_prompt; print(format_for_prompt(limit=30))"

.PHONY: log-clear
log-clear: venv ## wipe unified agent log
	@$(PY) -c "from tools.agent_log import clear; print(clear(), 'rows deleted')"

.PHONY: prompts-list
prompts-list: venv ## list all persona prompts (default vs override)
	@$(PY) -c "from tools.prompts import prompts; print(prompts(action='list'))"

.PHONY: prompts-get
prompts-get: venv ## show one persona's effective prompt: make prompts-get P=voice
	@$(PY) -c "from tools.prompts import prompts; print(prompts(action='get', persona='$(P)'))"

.PHONY: prompts-reset
prompts-reset: venv ## reset persona to default: make prompts-reset P=voice
	@$(PY) -c "from tools.prompts import prompts; print(prompts(action='reset', persona='$(P)'))"

.PHONY: test-tg
test-tg: venv ## test telegram connectivity (requires TELEGRAM_BOT_TOKEN)
	@$(PY) -c "from tools.telegram import telegram; print(telegram(action='ping'))"

.PHONY: test-voice
test-voice: venv ## verify voice provider config without actually starting audio
	@$(PY) -c "from g1 import _build_bidi_model; import os; m=_build_bidi_model(os.getenv('VOICE_PROVIDER','openai')); print('✓ provider OK:', type(m).__name__)"

.PHONY: ask
ask: venv ## one-shot agent query: make ask Q="status?"
	@$(PY) agent.py "$(Q)"

.PHONY: prune
prune: venv ## prune old SQLite rows (voice_bridge + agent_log)
	@$(PY) -c "from tools.voice_bridge import prune as p1; from tools.agent_log import prune as p2; print('voice_bridge:', p1()); print('agent_log:', p2(), 'rows deleted')"

.PHONY: memory-stats
memory-stats: venv ## summary of all SQLite tables
	@$(PY) -c "from tools.agent_log import stats as a; from tools.voice_bridge import stats as v; from tools.memory import _init; c=_init(); kv=c.execute('SELECT COUNT(*) FROM kv').fetchone()[0]; logs=c.execute('SELECT COUNT(*) FROM log').fetchone()[0]; print(f'kv:{kv} log:{logs} agent_log:{a()} voice_bridge:{v()}')"

# ─── WebXR teleop (Meta Quest 3 → G1) ─────────────────────────────────────

.PHONY: xr-cert
xr-cert: ## generate a self-signed TLS cert for WebXR (Quest needs HTTPS)
	@ROBOT_IP=$${ROBOT_IP:-} bash neon/teleop/make_cert.sh

.PHONY: xr-teleop
xr-teleop: ## start the WebXR teleop bridge (IK→DDS). vars: ARM=G1_29 IFACE=eth0
	@$(PY) -m neon.teleop.xr_bridge --arm $${ARM:-G1_29} --network-interface $${IFACE:-eth0} \
		--input-mode $${INPUT:-hand} --frequency $${HZ:-30}

.PHONY: xr-teleop-dry
xr-teleop-dry: ## start the bridge in DRY-RUN (stream poses, no robot/DDS)
	@$(PY) -m neon.teleop.xr_bridge --dry-run --input-mode $${INPUT:-hand}

.PHONY: xr-teleop-ctrl
xr-teleop-ctrl: ## teleop via controllers + locomotion (thumbstick walk, A=stop)
	@$(PY) -m neon.teleop.xr_bridge --input-mode controller --motion \
		--arm $${ARM:-G1_29} --network-interface $${IFACE:-eth0}

# ─── WiFi (companion wlan0 — robot DDS on eth0 is untouched) ───────────────

.PHONY: wifi
wifi: ## connect to a WiFi network (interactive picker). Or: make wifi SSID=x PASS=y
	@bash scripts/wifi.sh $${SSID:-} $${PASS:-}

.PHONY: wifi-status
wifi-status: ## show the current WiFi connection
	@bash scripts/wifi.sh --status

.PHONY: wifi-scan
wifi-scan: ## list nearby WiFi networks
	@bash scripts/wifi.sh --scan

# ─── Auth (WebAuthn passkey store) ────────────────────────────────────────

AUTH_STORE ?= .memory/.neon_auth.json

.PHONY: auth-clear
auth-clear: ## wipe passkeys → dashboard reopens "create passkey" setup (auto-refreshes service token)
	@if [ -f "$(AUTH_STORE)" ]; then \
		rm -f "$(AUTH_STORE)" && echo "🔓 auth store removed ($(AUTH_STORE)) — next visit shows setup"; \
	else \
		echo "auth store already absent ($(AUTH_STORE))"; \
	fi
	@# restart the dashboard so it mints a fresh JWT secret in a new store
	@$(DOCKER) restart neon-dashboard >/dev/null 2>&1 && echo "🔄 neon-dashboard restarted" || \
		echo "(dashboard container not running — will apply on next start)"
	@sleep 3
	@$(MAKE) --no-print-directory token-refresh

.PHONY: token-refresh
token-refresh: ## re-sync NEON_CAMERA_PROXY_TOKEN into .env + restart camera consumers
	@$(PY) scripts/refresh_token.py
	@$(DOCKER) compose up -d --force-recreate neon-thinker neon-telegram >/dev/null 2>&1 \
		&& echo "🔄 thinker + telegram recreated with fresh token" \
		|| echo "(consumers not running — token written, will apply on next start)"

.PHONY: auth-status
auth-status: ## show enrolled passkeys / setup state
	@curl -sk https://localhost:$${DASHBOARD_PORT:-8080}/auth/status | $(PY) -m json.tool 2>/dev/null || \
		echo "dashboard not reachable on :$${DASHBOARD_PORT:-8080}"

.PHONY: service-token
service-token: ## mint/print the long-lived thinker service token (for NEON_CAMERA_PROXY_TOKEN)
	@$(PY) -c "import sys; sys.path.insert(0,'docs/dashboard'); import auth; print(auth.service_token('thinker'))"
