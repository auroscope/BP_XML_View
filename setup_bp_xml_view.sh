#!/usr/bin/env bash
# Deploy BP_XML_View (Flask) for the current user on Ubuntu.
#
# Run as a normal (sudo-capable) user from anywhere, typically your home directory:
#     chmod +x setup_bp_xml_view.sh && ./setup_bp_xml_view.sh
#
# Does:
#   1. Installs git / python3-venv if missing (sudo)
#   2. Clones (or fast-forward updates) https://github.com/auroscope/BP_XML_View
#        -> $HOME/flask/BP_XML_View
#   3. Creates $HOME/flask/BP_XML_View/.venv and installs dependencies
#        (requirements.txt if the repo has one, otherwise the packages listed in its README)
#   4. Opens TCP 5007 in ufw (sudo), for your local network only or from anywhere (you choose;
#        skip the prompt with FIREWALL_SOURCE=any|lan|<CIDR>)
#   5. Runs the app with gunicorn on 0.0.0.0:5007 as a systemd USER service
#        (starts now and at boot; survives logout via 'loginctl enable-linger')
#
# NOTE: app.py ends with app.run(host='0.0.0.0', port=5002, debug=True). Running it directly would
# expose Flask's Werkzeug debugger (remote code execution) to the network, so this script serves the
# same app object ('app:app') through gunicorn instead, on port 5007 (the README's production port).

set -euo pipefail

# --- DEFAULTS (override by exporting the variable before running) ---
REPO_URL="${REPO_URL:-https://github.com/auroscope/BP_XML_View.git}"
BRANCH="${BRANCH:-main}"
PORT="${PORT:-5007}"
BASE_DIR="${BASE_DIR:-$HOME/flask}"
APP_NAME="BP_XML_View"
SERVICE_NAME="bp_xml_view"
FALLBACK_PKGS=(Flask gunicorn pillow striprtf markdown)   # from the repo README (it has no requirements.txt)
# -----------------------------------------------------------------

APP_DIR="$BASE_DIR/$APP_NAME"

say()  { printf '\n\033[36m=== %s ===\033[0m\n' "$*"; }
ok()   { printf '\033[32m[+] %s\033[0m\n' "$*"; }
warn() { printf '\033[33m[!] %s\033[0m\n' "$*"; }
die()  { printf '\033[31m[x] %s\033[0m\n' "$*" >&2; exit 1; }

[[ $EUID -ne 0 ]] || die "Run as your normal user (not root/sudo). The app and venv are created in your home directory; sudo is used only where needed."
command -v sudo >/dev/null || die "sudo is required."
sudo -v || die "sudo authentication failed."

# =========================================================
say "0. Checking TCP port $PORT"
port_listeners() { sudo ss -Hltnp "sport = :$1" 2>/dev/null; }   # sudo so other users' processes are named too
port_in_use()    { [[ -n "$(port_listeners "$1")" ]]; }

# True when the listener on port $1 belongs to this script's own service (a previous install of this app).
port_is_ours() {
    local pid
    pid=$(systemctl --user show -p MainPID --value "$SERVICE_NAME" 2>/dev/null || true)
    [[ -n "$pid" && "$pid" != "0" ]] && port_listeners "$1" | grep -Eq "pid=${pid}[,)]"
}

next_free_port() {
    local p
    for ((p = $1 + 1; p <= $1 + 100 && p <= 65535; p++)); do
        port_in_use "$p" || { echo "$p"; return; }
    done
}

while true; do
    if [[ ! "$PORT" =~ ^[0-9]+$ ]] || (( PORT < 1024 || PORT > 65535 )); then
        warn "Port '$PORT' is not usable. Choose 1024-65535 (ports below 1024 need root)."
    elif ! port_in_use "$PORT"; then
        ok "Port $PORT is free."; break
    elif port_is_ours "$PORT"; then
        ok "Port $PORT is held by an earlier install of $APP_NAME; it will be restarted."; break
    else
        warn "Port $PORT is already in use by another process:"
        port_listeners "$PORT" | sed 's/^/      /' || true
    fi
    base="$PORT"; [[ "$base" =~ ^[0-9]+$ ]] || base=5007
    suggestion=$(next_free_port "$base" 2>/dev/null || true)
    reply=""
    read -r -p "Enter another port, or q to quit [${suggestion:-none}]: " reply || die "No interactive input available; set PORT=<free port> and re-run."
    reply="${reply:-$suggestion}"
    [[ "$reply" == "q" || "$reply" == "Q" || -z "$reply" ]] && die "Aborted; nothing was changed."
    PORT="$reply"
done

# =========================================================
say "0. Firewall scope for TCP $PORT"
# Non-interactive override: FIREWALL_SOURCE=any | lan | <CIDR such as 192.168.10.0/24>
detect_lan_cidr() {
    local dev
    dev=$(ip -4 route show default 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="dev"){print $(i+1); exit}}')
    [[ -n "$dev" ]] && ip -4 route show dev "$dev" scope link 2>/dev/null | awk '{print $1; exit}'
}
valid_cidr() {
    [[ "$1" =~ ^([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})/([0-9]|[12][0-9]|3[0-2])$ ]] || return 1
    (( 10#${BASH_REMATCH[1]} <= 255 && 10#${BASH_REMATCH[2]} <= 255 && 10#${BASH_REMATCH[3]} <= 255 && 10#${BASH_REMATCH[4]} <= 255 ))
}

LAN_CIDR=$(detect_lan_cidr || true)
FW_SOURCE="${FIREWALL_SOURCE:-}"
if [[ -z "$FW_SOURCE" ]]; then
    echo "  1) Local network only${LAN_CIDR:+ ($LAN_CIDR detected)}   [recommended]"
    echo "  2) From anywhere"
    choice=""; read -r -p "Choose 1 or 2 [1]: " choice || true
    case "${choice:-1}" in
        2) FW_SOURCE="any" ;;
        *) FW_SOURCE="lan" ;;
    esac
fi
if [[ "$FW_SOURCE" == "lan" ]]; then
    FW_SOURCE="$LAN_CIDR"
    reply=""; read -r -p "Local network CIDR [${FW_SOURCE:-e.g. 192.168.10.0/24}]: " reply || true
    FW_SOURCE="${reply:-$FW_SOURCE}"
fi
if [[ "$FW_SOURCE" != "any" ]] && ! valid_cidr "$FW_SOURCE"; then
    die "Invalid network '$FW_SOURCE'. Use a CIDR like 192.168.10.0/24, or 'any'."
fi
ok "Port $PORT will be allowed from: $FW_SOURCE"

# =========================================================
say "1. Prerequisites"
need=()
command -v git     >/dev/null || need+=(git)
command -v python3 >/dev/null || need+=(python3)
python3 -c 'import venv, ensurepip' >/dev/null 2>&1 || need+=(python3-venv python3-pip)
command -v curl    >/dev/null || need+=(curl)
if ((${#need[@]})); then
    sudo apt-get update
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y "${need[@]}"
fi
PYVER=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')
ok "git $(git --version | awk '{print $3}'), python $PYVER"
if python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,13) else 1)'; then :; else
    warn "The project README targets Python 3.13+; this system has $PYVER. Continuing, but if the app fails to start, install a newer Python."
fi

# =========================================================
say "2. Fetching $APP_NAME into $APP_DIR"
mkdir -p "$BASE_DIR"
if [[ -d "$APP_DIR/.git" ]]; then
    git -C "$APP_DIR" fetch --quiet origin "$BRANCH"
    git -C "$APP_DIR" pull --ff-only origin "$BRANCH"
    ok "Existing clone updated."
elif [[ -e "$APP_DIR" ]]; then
    die "$APP_DIR exists but is not a git clone. Move or remove it and re-run."
else
    git clone --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
    ok "Cloned."
fi
[[ -f "$APP_DIR/app.py" ]] || die "app.py not found in $APP_DIR."

# =========================================================
say "3. Python virtual environment (.venv) and dependencies"
if [[ ! -x "$APP_DIR/.venv/bin/python" ]]; then
    python3 -m venv "$APP_DIR/.venv"
    ok "Created $APP_DIR/.venv"
else
    ok "Reusing existing $APP_DIR/.venv"
fi
VENV_PY="$APP_DIR/.venv/bin/python"
"$VENV_PY" -m pip install --quiet --upgrade pip
if [[ -f "$APP_DIR/requirements.txt" ]]; then
    "$VENV_PY" -m pip install -r "$APP_DIR/requirements.txt"
    ok "Installed requirements.txt"
else
    warn "No requirements.txt in the repo; installing the packages listed in its README: ${FALLBACK_PKGS[*]}"
    "$VENV_PY" -m pip install "${FALLBACK_PKGS[@]}"
fi
"$VENV_PY" -m pip install --quiet gunicorn          # no-op if already present; needed to serve the app
"$VENV_PY" -c "import flask, PIL, striprtf, markdown, gunicorn" \
    || die "A required module failed to import after install."

# =========================================================
say "4. Firewall (ufw): allow TCP $PORT from $FW_SOURCE"
if command -v ufw >/dev/null; then
    # Re-runs: drop this script's earlier rules (tagged with the app name) so the scope is replaced, not stacked.
    mapfile -t old_rules < <(sudo ufw status numbered | sed -n "s/^\[ *\([0-9]\+\)\].*# $APP_NAME\$/\1/p" | sort -rn)
    for n in "${old_rules[@]:-}"; do
        if [[ -n "$n" ]]; then sudo ufw --force delete "$n" >/dev/null; fi
    done
    if [[ "$FW_SOURCE" == "any" ]]; then
        sudo ufw allow "$PORT/tcp" comment "$APP_NAME"
    else
        sudo ufw allow from "$FW_SOURCE" to any port "$PORT" proto tcp comment "$APP_NAME"
    fi
    if sudo ufw status | grep -q '^Status: active'; then
        ok "Rule added; ufw is active."
    else
        warn "Rule added, but ufw is not active so nothing is being filtered. Enable it with 'sudo ufw enable' (make sure SSH is allowed first)."
    fi
else
    warn "ufw is not installed; skipping firewall rule. Install with 'sudo apt-get install ufw'."
fi

# =========================================================
say "5. systemd user service ($SERVICE_NAME)"
UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$UNIT_DIR"

# Last-moment safety net (the port was already checked in step 0).
if port_in_use "$PORT" && ! port_is_ours "$PORT"; then
    die "TCP $PORT was taken by another process during setup. Re-run to pick a different port."
fi

cat > "$UNIT_DIR/$SERVICE_NAME.service" <<EOF
[Unit]
Description=Gunicorn instance serving $APP_NAME (Flask)
After=network.target

[Service]
WorkingDirectory=$APP_DIR
Environment="PATH=$APP_DIR/.venv/bin"
ExecStart=$APP_DIR/.venv/bin/gunicorn --workers 3 --threads 3 --timeout 300 --bind 0.0.0.0:$PORT app:app
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF

# Keep user services running after logout and start them at boot.
sudo loginctl enable-linger "$USER"

if ! systemctl --user daemon-reload 2>/dev/null; then
    warn "No user systemd session available in this shell (try logging in via SSH or a console session)."
    warn "Unit file written to $UNIT_DIR/$SERVICE_NAME.service. Then run: systemctl --user enable --now $SERVICE_NAME"
    exit 1
fi
systemctl --user enable "$SERVICE_NAME" >/dev/null 2>&1
systemctl --user restart "$SERVICE_NAME"

# =========================================================
say "6. Verification"
code="000"
for _ in {1..15}; do
    code=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/" || true)
    [[ "$code" != "000" ]] && break
    sleep 1
done
systemctl --user --no-pager --lines=0 status "$SERVICE_NAME" | sed -n '1,3p' || true
if [[ "$code" == "000" ]]; then
    warn "App did not respond on port $PORT. Check: journalctl --user -u $SERVICE_NAME -n 50"
    exit 1
fi
ok "App responded on http://127.0.0.1:$PORT/ (HTTP $code)."

HOST_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
printf '\n\033[32m=========================================================\n'
echo " $APP_NAME is running"
echo " Location   : $APP_DIR"
echo " Venv       : $APP_DIR/.venv"
echo " URL        : http://${HOST_IP:-<host-ip>}:$PORT/"
echo " Service    : systemctl --user status|restart|stop $SERVICE_NAME"
echo " Logs       : journalctl --user -u $SERVICE_NAME -f"
echo " Firewall   : TCP $PORT allowed from $FW_SOURCE (ufw)"
echo "=========================================================\033[0m"
if [[ "$FW_SOURCE" == "any" ]]; then
    warn "Port $PORT is open to any address that can reach this host, and the app handles patient data."
    warn "To limit it later, re-run this script and choose 'Local network only'."
fi
