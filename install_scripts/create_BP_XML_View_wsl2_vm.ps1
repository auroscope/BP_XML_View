# Hardened WSL2 Ubuntu + SSH + BP_XML_View (Flask/gunicorn) provisioning script
# Based on create_wsl2_ubuntu_sandbox.ps1, extended to deploy https://github.com/auroscope/BP_XML_View.
# Run from an elevated PowerShell. Shortcut target:
#   C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe -NoExit -ExecutionPolicy Bypass -File "<path-to-script>\create_BP_XML_View_wsl2_vm.ps1"

$ErrorActionPreference = 'Stop'

# --- DEFAULTS (each can be overridden interactively) ---
$BaseDistro     = "Ubuntu"            # image to install from 'wsl --list --online'
$DefaultName    = "ubuntu_bp_xml_view" # name of the NEW instance (existing instances are never touched)
$RepoUrl        = "https://github.com/auroscope/BP_XML_View.git"
$Branch         = "main"
$AppName        = "BP_XML_View"
$ServiceName    = "bp_xml_view"
$DefaultAppPort = 5007                # gunicorn port (the repo README's production port)
$DefaultUser    = "defaultuser"
$PubKeyDefault  = Join-Path $env:USERPROFILE ".ssh\id_ed25519.pub"
$WslVmCreatorId = '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}'   # fixed GUID for the WSL VM
# -------------------------------------------------------

# Pipe plain UTF-8 (no BOM) into wsl.exe
$OutputEncoding = New-Object System.Text.UTF8Encoding $false

# --- Admin enforcement (a '# Requires' comment does not enforce anything) ---
$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "This script must be run as Administrator."
}

# --- Helpers ---
function Wsl-Root {
    # Run a bash command as root in the distro; throw on failure.
    param([Parameter(Mandatory)][string]$Cmd)
    & wsl.exe -d $DistroName -u root bash -c $Cmd
    if ($LASTEXITCODE -ne 0) { throw "WSL command failed (exit $LASTEXITCODE): $Cmd" }
}

function Wsl-RootStdin {
    # Same, but feeds $Text on stdin (keeps secrets off the command line). CRs are stripped by the caller's command.
    param([Parameter(Mandatory)][string]$Text, [Parameter(Mandatory)][string]$Cmd)
    $Text | & wsl.exe -d $DistroName -u root bash -c $Cmd
    if ($LASTEXITCODE -ne 0) { throw "WSL command failed (exit $LASTEXITCODE): $Cmd" }
}

function Get-WslDistros {
    # wsl.exe --list emits UTF-16; decode it properly, then restore the console encoding.
    $old = [Console]::OutputEncoding
    try {
        [Console]::OutputEncoding = [System.Text.Encoding]::Unicode
        $out = & wsl.exe --list --quiet
    } finally {
        [Console]::OutputEncoding = $old
    }
    if ($LASTEXITCODE -ne 0 -or -not $out) { return @() }
    return @($out | ForEach-Object { ($_ -replace "`0", '').Trim() } | Where-Object { $_ })
}

function Get-WslDistroInfo {
    # Parse 'wsl --list --verbose' (UTF-16) into objects: Name, State, Version, IsDefault.
    $old = [Console]::OutputEncoding
    try {
        [Console]::OutputEncoding = [System.Text.Encoding]::Unicode
        $out = & wsl.exe --list --verbose
    } finally {
        [Console]::OutputEncoding = $old
    }
    if ($LASTEXITCODE -ne 0 -or -not $out) { return @() }
    $rows = foreach ($line in $out) {
        $clean = ($line -replace "`0", '')
        if ($clean -match '^\s*(\*)?\s*(\S+)\s+(Running|Stopped|Installing|Uninstalling|Converting)\s+(\d+)\s*$') {
            [pscustomobject]@{ Name = $Matches[2]; State = $Matches[3]; Version = $Matches[4]; IsDefault = [bool]$Matches[1] }
        }
    }
    return @($rows)
}

function ConvertTo-PlainText([System.Security.SecureString]$Secure) {
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secure)
    try { [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}

function New-RandomPassword([int]$Length = 24) {
    $chars = 'abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789!@#%^*-_=+'.ToCharArray()
    $bytes = New-Object byte[] $Length
    $rng = New-Object Security.Cryptography.RNGCryptoServiceProvider
    $rng.GetBytes($bytes); $rng.Dispose()
    -join ($bytes | ForEach-Object { $chars[$_ % $chars.Length] })
}

# =========================================================
Write-Host "=== 0. Interactive Setup ===" -ForegroundColor Cyan

# --- Existing instances: list them and optionally delete (opt-in, nothing is removed by default) ---
$Info = @(Get-WslDistroInfo)
if ($Info.Count -gt 0) {
    Write-Host "Existing WSL instances:" -ForegroundColor Cyan
    for ($i = 0; $i -lt $Info.Count; $i++) {
        $d = $Info[$i]
        $tags = @()
        if ($d.IsDefault) { $tags += 'default' }
        if ($d.Name -match '^ubuntu_sandbox\d*$') { $tags += 'previous sandbox' }
        $tagText = if ($tags) { '  [' + ($tags -join ', ') + ']' } else { '' }
        Write-Host ("  {0}. {1,-24} {2,-9} WSL{3}{4}" -f ($i + 1), $d.Name, $d.State, $d.Version, $tagText)
    }
    $Sel = Read-Host "Numbers to DELETE (e.g. 2,3), or press Enter to delete nothing"
    $ToDelete = @()
    foreach ($tok in ($Sel -split '[,\s]+' | Where-Object { $_ })) {
        if ($tok -notmatch '^\d+$' -or [int]$tok -lt 1 -or [int]$tok -gt $Info.Count) { throw "Invalid selection '$tok'." }
        $ToDelete += $Info[[int]$tok - 1]
    }
    foreach ($d in ($ToDelete | Sort-Object Name -Unique)) {
        Write-Host "[!] '$($d.Name)' will be PERMANENTLY deleted with ALL its data (cannot be undone)." -ForegroundColor Red
        if ($d.Name -match '^(docker-desktop|docker-desktop-data|rancher-desktop.*|podman-machine.*)$') {
            Write-Host "[!] '$($d.Name)' looks like a tool-managed distro; deleting it may break that tool." -ForegroundColor Yellow
        }
        $typed = Read-Host "Type the exact name '$($d.Name)' to confirm, or press Enter to skip"
        if ($typed -ceq $d.Name) {
            wsl.exe --terminate $d.Name
            wsl.exe --unregister $d.Name
            if ($LASTEXITCODE -ne 0) { throw "Failed to unregister '$($d.Name)'." }
            Write-Host "[+] Deleted '$($d.Name)'." -ForegroundColor Green
        } else {
            Write-Host "[*] Skipped '$($d.Name)'." -ForegroundColor Yellow
        }
    }
}

# New instance name. Any instance not explicitly deleted above is left untouched.
$Existing = Get-WslDistros
while ($true) {
    $DistroName = Read-Host "Name for the NEW WSL instance [$DefaultName]"
    if ([string]::IsNullOrWhiteSpace($DistroName)) { $DistroName = $DefaultName }
    if ($DistroName -notmatch '^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$') {
        Write-Host "[!] Use letters, digits, '_', '-' or '.' only." -ForegroundColor Yellow; continue
    }
    if ($Existing -contains $DistroName) {
        Write-Host "[!] An instance named '$DistroName' already exists and will not be touched. Pick another name." -ForegroundColor Yellow; continue
    }
    break
}
Write-Host "[+] Remaining instances (untouched): $(if ($Existing) { $Existing -join ', ' } else { 'none' })" -ForegroundColor Green

# SSH port. Mirrored mode shares ONE network stack across all distros and the host,
# so two sshd instances cannot both bind the same port.
$PortBusy = [bool](Get-NetTCPConnection -LocalPort 22 -State Listen -ErrorAction SilentlyContinue)
$DefaultPort = if ($PortBusy) { 2222 } else { 22 }
if ($PortBusy) { Write-Host "[!] Something is already listening on TCP 22; defaulting to $DefaultPort." -ForegroundColor Yellow }
while ($true) {
    $PortInput = Read-Host "SSH port for '$DistroName' [$DefaultPort]"
    $SshPort = if ([string]::IsNullOrWhiteSpace($PortInput)) { $DefaultPort } else { $PortInput }
    if ($SshPort -notmatch '^\d+$' -or [int]$SshPort -lt 1 -or [int]$SshPort -gt 65535) {
        Write-Host "[!] Enter a port between 1 and 65535." -ForegroundColor Yellow; continue
    }
    $SshPort = [int]$SshPort
    if (Get-NetTCPConnection -LocalPort $SshPort -State Listen -ErrorAction SilentlyContinue) {
        Write-Host "[!] Port $SshPort is already in use on this machine. Pick another." -ForegroundColor Yellow; continue
    }
    break
}
$FwRuleName     = "WSL2 $DistroName SSH Server (TCP $SshPort)"
$HyperVRuleName = "WSL2 $DistroName SSH Server (TCP $SshPort, Hyper-V)"

# App port. Mirrored mode shares the host's network stack, so the port must be free on Windows too.
while ($true) {
    $AppPortInput = Read-Host "Port for $AppName (gunicorn) [$DefaultAppPort]"
    $AppPort = if ([string]::IsNullOrWhiteSpace($AppPortInput)) { $DefaultAppPort } else { $AppPortInput }
    if ($AppPort -notmatch '^\d+$' -or [int]$AppPort -lt 1024 -or [int]$AppPort -gt 65535) {
        Write-Host "[!] Enter a port between 1024 and 65535." -ForegroundColor Yellow; continue
    }
    $AppPort = [int]$AppPort
    if ($AppPort -eq $SshPort) { Write-Host "[!] Same as the SSH port. Pick another." -ForegroundColor Yellow; continue }
    if (Get-NetTCPConnection -LocalPort $AppPort -State Listen -ErrorAction SilentlyContinue) {
        Write-Host "[!] Port $AppPort is already in use on this machine. Pick another." -ForegroundColor Yellow; continue
    }
    break
}

# Who may reach the app from the network (Windows Firewall scope; Private/Domain profiles only either way).
Write-Host "Allow $AppName (TCP $AppPort) from:" -ForegroundColor Cyan
Write-Host "  1) Local subnet only   [recommended]"
Write-Host "  2) Anywhere (any address that can reach this PC on a Private/Domain network)"
$ScopeChoice = Read-Host "Choose 1 or 2 [1]"
$AppRemote = if ($ScopeChoice -eq '2') { 'Any' } else { 'LocalSubnet' }
$AppFwRuleName     = "WSL2 $DistroName $AppName (TCP $AppPort)"
$AppHyperVRuleName = "WSL2 $DistroName $AppName (TCP $AppPort, Hyper-V)"

$NewUser =Read-Host "Linux username [$DefaultUser]"
if ([string]::IsNullOrWhiteSpace($NewUser)) { $NewUser = $DefaultUser }
if ($NewUser -cnotmatch '^[a-z_][a-z0-9_-]{0,31}$') { throw "Invalid username '$NewUser'." }
if ($NewUser -eq 'root') { throw "Choose a non-root username." }

$GeneratedPassword = $false
$sec1 = Read-Host "Password for '$NewUser' (leave blank to generate a random one)" -AsSecureString
$UserPass = ConvertTo-PlainText $sec1
if ([string]::IsNullOrEmpty($UserPass)) {
    $UserPass = New-RandomPassword
    $GeneratedPassword = $true
} else {
    $sec2 = Read-Host "Confirm password" -AsSecureString
    if ($UserPass -cne (ConvertTo-PlainText $sec2)) { throw "Passwords do not match." }
    if ($UserPass.Length -lt 12) { Write-Host "[!] Password is shorter than 12 characters." -ForegroundColor Yellow }
}

$PubKeyPath = Read-Host "Public key file to authorise for SSH [$PubKeyDefault]"
if ([string]::IsNullOrWhiteSpace($PubKeyPath)) { $PubKeyPath = $PubKeyDefault }
$PubKey = $null
if (Test-Path $PubKeyPath) {
    $PubKey = (Get-Content -Raw $PubKeyPath).Trim()
    if ($PubKey -notmatch '^(ssh-|ecdsa-|sk-)') { throw "'$PubKeyPath' does not look like an SSH public key." }
    Write-Host "[+] Will authorise key from $PubKeyPath" -ForegroundColor Green
} else {
    Write-Host "[!] No public key found at '$PubKeyPath'. Key login will NOT be set up (password login only)." -ForegroundColor Yellow
}

# BP_XML_View does not need ODBC, so this defaults to No in this variant.
$InstallOdbc = (Read-Host "Install Microsoft ODBC Driver 18 + sqlcmd/bcp? (y/N)") -match '^[yY]$'

# Optional: keep the instance running. WSL stops an idle instance, which would stop the app and SSH.
Write-Host "WSL stops an instance once nothing is attached to it, which would take the app and SSH offline." -ForegroundColor Yellow
$KeepAlive = (Read-Host "Create a Windows scheduled task that starts '$DistroName' at your logon and keeps it running? (y/N)") -match '^[yY]$'
$KeepAliveTask = "WSL2 $DistroName keep-alive"

# =========================================================
Write-Host "`n=== 1. Plan ===" -ForegroundColor Cyan
Write-Host "Create new instance '$DistroName' from '$BaseDistro'; SSH on TCP $SshPort; $AppName on TCP $AppPort (from: $AppRemote). Remaining instances will not be modified." -ForegroundColor Green

# =========================================================
Write-Host "`n=== 2. Enforcing Mirrored Networking (.wslconfig) ===" -ForegroundColor Cyan
$WslConfigPath = Join-Path $env:USERPROFILE ".wslconfig"
$lines = New-Object System.Collections.Generic.List[string]
if (Test-Path $WslConfigPath) { (Get-Content $WslConfigPath) | ForEach-Object { $lines.Add($_) } }

$secIdx = -1
for ($i = 0; $i -lt $lines.Count; $i++) { if ($lines[$i].Trim() -ieq '[wsl2]') { $secIdx = $i; break } }
if ($secIdx -lt 0) {
    if ($lines.Count -gt 0 -and $lines[$lines.Count - 1].Trim() -ne '') { $lines.Add('') }
    $lines.Add('[wsl2]'); $lines.Add('networkingMode=mirrored')
} else {
    $keyIdx = -1
    for ($i = $secIdx + 1; $i -lt $lines.Count -and $lines[$i].Trim() -notmatch '^\['; $i++) {
        if ($lines[$i] -match '^\s*networkingMode\s*=') { $keyIdx = $i; break }
    }
    if ($keyIdx -ge 0) { $lines[$keyIdx] = 'networkingMode=mirrored' }
    else { $lines.Insert($secIdx + 1, 'networkingMode=mirrored') }
}
$OriginalLines = if (Test-Path $WslConfigPath) { @(Get-Content $WslConfigPath) } else { @() }
if (($OriginalLines -join "`n") -ceq ($lines -join "`n")) {
    Write-Host "[+] networkingMode=mirrored already set in $WslConfigPath; no change, no shutdown needed." -ForegroundColor Green
} else {
    [System.IO.File]::WriteAllLines($WslConfigPath, $lines, (New-Object System.Text.UTF8Encoding $false))
    Write-Host "[+] networkingMode=mirrored set in $WslConfigPath" -ForegroundColor Green
    Write-Host "[!] Applying it requires 'wsl --shutdown', which stops ALL running WSL distros (existing ones are stopped, not modified)." -ForegroundColor Yellow
    $ShutdownChoice = Read-Host "Run wsl --shutdown now? (Y/n)"
    if ($ShutdownChoice -notmatch '^[nN]$') {
        wsl.exe --shutdown
        Start-Sleep -Seconds 3
    } else {
        Write-Host "[!] Skipped. Mirrored mode will only take effect after you run 'wsl --shutdown'." -ForegroundColor Yellow
    }
}

# =========================================================
Write-Host "`n=== 3. Installing '$DistroName' from $BaseDistro (No Launch) ===" -ForegroundColor Cyan
# --name needs a recent WSL (about 2.4.4+). Run 'wsl --update' if this fails.
wsl.exe --install -d $BaseDistro --name $DistroName --no-launch
if ($LASTEXITCODE -ne 0) {
    throw "wsl --install --name failed. Update WSL ('wsl --update') and retry; nothing existing was modified."
}
if (-not ((Get-WslDistros) -contains $DistroName)) {
    throw "'$DistroName' is not registered after install. Check 'wsl --list --verbose'; nothing existing was modified."
}

# =========================================================
Write-Host "`n=== 4. Creating User '$NewUser' and Locking Root ===" -ForegroundColor Cyan
Wsl-Root "id -u $NewUser >/dev/null 2>&1 || useradd -m -s /bin/bash $NewUser"
Wsl-RootStdin -Text "${NewUser}:${UserPass}" -Cmd "tr -d '\r' | chpasswd"
Wsl-Root "usermod -aG sudo $NewUser"
Wsl-Root "passwd -l root"      # root has no usable password; use sudo
Write-Host "[+] Root password locked; '$NewUser' has sudo." -ForegroundColor Green

# =========================================================
Write-Host "`n=== 5. Writing /etc/wsl.conf (default user + systemd) ===" -ForegroundColor Cyan
$WslConf = "[user]`ndefault=$NewUser`n`n[boot]`nsystemd=true`n"
Wsl-RootStdin -Text $WslConf -Cmd "tr -d '\r' > /etc/wsl.conf"
wsl.exe --terminate $DistroName      # restart so systemd comes up for the remaining steps
Start-Sleep -Seconds 2

# =========================================================
Write-Host "`n=== 6. Installing Packages (git, net-tools, curl, openssh-server, fail2ban, python3-venv, python3-pip) ===" -ForegroundColor Cyan
Wsl-Root "apt-get update"
Wsl-Root "DEBIAN_FRONTEND=noninteractive apt-get install -y git net-tools curl openssh-server fail2ban python3 python3-venv python3-pip"

# =========================================================
Write-Host "`n=== 6a. Deploying $AppName for '$NewUser' (clone, .venv, dependencies) ===" -ForegroundColor Cyan
$AppDir = "/home/$NewUser/flask/$AppName"
# Runs as the new user. Single-quoted here-string: nothing is expanded by PowerShell except the @@tokens@@ below.
$AppScript = @'
set -euo pipefail
BASE="$HOME/flask"; APP="$BASE/@@APP@@"
mkdir -p "$BASE"
if [ -d "$APP/.git" ]; then
    git -C "$APP" pull --ff-only origin @@BRANCH@@
elif [ -e "$APP" ]; then
    echo "$APP exists but is not a git clone." >&2; exit 1
else
    git clone --branch @@BRANCH@@ @@REPO@@ "$APP"
fi
[ -f "$APP/app.py" ] || { echo "app.py not found in $APP" >&2; exit 1; }
python3 -m venv "$APP/.venv"
PY="$APP/.venv/bin/python"
"$PY" -m pip install --quiet --upgrade pip
if [ -f "$APP/requirements.txt" ]; then
    "$PY" -m pip install -r "$APP/requirements.txt"
else
    echo "[!] No requirements.txt in the repo; installing the packages listed in its README."
    "$PY" -m pip install Flask gunicorn pillow striprtf markdown
fi
"$PY" -m pip install --quiet gunicorn
"$PY" -c "import flask, PIL, striprtf, markdown, gunicorn"
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,13) else 1)'; then
    echo "[!] The project README targets Python 3.13+; this instance has $(python3 -V). If the app fails to start, a newer Python is needed."
fi
'@
$AppScript = $AppScript.Replace('@@APP@@', $AppName).Replace('@@BRANCH@@', $Branch).Replace('@@REPO@@', $RepoUrl) -replace "`r", ""
# Written to a temp file (not 'bash -s') so git/pip can't consume the script from stdin.
$DeployCmd = "tr -d '\r' > /tmp/deploy_app.sh && chmod 644 /tmp/deploy_app.sh && runuser -u $NewUser -- env HOME=/home/$NewUser bash /tmp/deploy_app.sh; rc=`$?; rm -f /tmp/deploy_app.sh; exit `$rc"
Wsl-RootStdin -Text $AppScript -Cmd $DeployCmd
Write-Host "[+] $AppName deployed to $AppDir" -ForegroundColor Green

# systemd system service running as the new user (gunicorn, same settings as the repo README).
$UnitFile = @"
[Unit]
Description=Gunicorn instance serving $AppName (Flask)
After=network.target

[Service]
User=$NewUser
WorkingDirectory=$AppDir
Environment="PATH=$AppDir/.venv/bin"
ExecStart=$AppDir/.venv/bin/gunicorn --workers 3 --threads 3 --timeout 300 --bind 0.0.0.0:$AppPort app:app
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
"@
Wsl-RootStdin -Text ($UnitFile -replace "`r", "") -Cmd "tr -d '\r' > /etc/systemd/system/$ServiceName.service"
Wsl-Root "systemctl daemon-reload && systemctl enable $ServiceName && systemctl restart $ServiceName"
$up = $false
for ($i = 0; $i -lt 20 -and -not $up; $i++) {
    Start-Sleep -Seconds 1
    & wsl.exe -d $DistroName -u root bash -c "curl -s -o /dev/null http://127.0.0.1:$AppPort/"
    if ($LASTEXITCODE -eq 0) { $up = $true }
}
if (-not $up) {
    & wsl.exe -d $DistroName -u root bash -c "journalctl -u $ServiceName -n 30 --no-pager"
    throw "$AppName did not respond on port $AppPort. See the journal output above."
}
Write-Host "[+] $AppName is running (service '$ServiceName', port $AppPort)." -ForegroundColor Green

# =========================================================
if ($InstallOdbc) {
    Write-Host "`n=== 6b. Installing Microsoft ODBC Driver 18 (msodbcsql18, mssql-tools18) ===" -ForegroundColor Cyan
    $OdbcScript = @'
set -euo pipefail
. /etc/os-release
if [[ "${ID:-}" != "ubuntu" ]] || ! [[ " 18.04 20.04 22.04 24.04 26.04 " == *" ${VERSION_ID} "* ]]; then
    echo "Ubuntu ${VERSION_ID:-unknown} is not supported by msodbcsql18." >&2
    exit 1
fi
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
curl -sSL -o "$tmp/packages-microsoft-prod.deb" "https://packages.microsoft.com/config/ubuntu/${VERSION_ID}/packages-microsoft-prod.deb"
file "$tmp/packages-microsoft-prod.deb" | grep -q "Debian binary package" || { echo "Downloaded repo package is invalid." >&2; exit 1; }
dpkg -i "$tmp/packages-microsoft-prod.deb"
apt-get update
ACCEPT_EULA=Y DEBIAN_FRONTEND=noninteractive apt-get install -y msodbcsql18 mssql-tools18 unixodbc-dev
# system-wide PATH for sqlcmd/bcp (the script runs as root, so avoid ~/.bashrc)
echo 'export PATH="$PATH:/opt/mssql-tools18/bin"' > /etc/profile.d/mssql-tools18.sh
odbcinst -q -d -n "ODBC Driver 18 for SQL Server"
'@
    # Pipe via stdin and strip CRs so Windows line endings can't break bash
    Wsl-RootStdin -Text ($OdbcScript -replace "`r", "") -Cmd "tr -d '\r' | bash -s"
    Write-Host "[+] ODBC Driver 18 installed. Note: v18 encrypts by default (Encrypt=yes)." -ForegroundColor Green
}

# =========================================================
Write-Host "`n=== 7. Hardening OpenSSH ===" -ForegroundColor Cyan
if ($PubKey) {
    $setupKeys = "install -d -m 700 -o $NewUser -g $NewUser /home/$NewUser/.ssh && " +
                 "tr -d '\r' > /home/$NewUser/.ssh/authorized_keys && " +
                 "chmod 600 /home/$NewUser/.ssh/authorized_keys && " +
                 "chown $NewUser`:$NewUser /home/$NewUser/.ssh/authorized_keys"
    Wsl-RootStdin -Text ($PubKey + "`n") -Cmd $setupKeys
    Write-Host "[+] Public key installed for $NewUser." -ForegroundColor Green
}

$SshdDropIn = @"
# Managed by create_BP_XML_View_wsl2_vm.ps1
Port $SshPort
PermitRootLogin no
AllowUsers $NewUser
PasswordAuthentication yes
PubkeyAuthentication yes
PermitEmptyPasswords no
KbdInteractiveAuthentication no
MaxAuthTries 3
LoginGraceTime 30
X11Forwarding no
AllowAgentForwarding no
AllowTcpForwarding no
ClientAliveInterval 300
ClientAliveCountMax 2
"@
Wsl-Root "mkdir -p /run/sshd /etc/ssh/sshd_config.d"
Wsl-RootStdin -Text ($SshdDropIn -replace "`r", "") -Cmd "tr -d '\r' > /etc/ssh/sshd_config.d/99-hardening.conf"
# Make sure the main config actually includes the drop-in directory.
Wsl-Root "grep -qE '^\s*Include\s+/etc/ssh/sshd_config.d/' /etc/ssh/sshd_config || sed -i '1i Include /etc/ssh/sshd_config.d/*.conf' /etc/ssh/sshd_config"
Wsl-Root "ssh-keygen -A"
Wsl-Root "sshd -t"                                   # validate before restarting

# Ubuntu 24.04 uses socket activation, which ignores 'Port'. Use the plain service instead.
Wsl-Root "systemctl disable --now ssh.socket 2>/dev/null || true"
Wsl-Root "systemctl enable ssh && systemctl restart ssh"

# fail2ban: watch the systemd journal for sshd failures
$F2b = @"
[DEFAULT]
bantime  = 1h
findtime = 10m
maxretry = 4
backend  = systemd

[sshd]
enabled = true
port    = $SshPort
"@
Wsl-RootStdin -Text ($F2b -replace "`r", "") -Cmd "tr -d '\r' > /etc/fail2ban/jail.local"
Wsl-Root "systemctl enable fail2ban && systemctl restart fail2ban"
Write-Host "[+] sshd hardened; fail2ban active." -ForegroundColor Green

# =========================================================
Write-Host "`n=== 8. Windows Firewall (Private + Domain only) ===" -ForegroundColor Cyan
Get-NetFirewallRule -DisplayName $FwRuleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName $FwRuleName `
                    -Direction Inbound -Action Allow -Protocol TCP -LocalPort $SshPort `
                    -Profile Private,Domain `
                    -Description "Inbound SSH to WSL2 Ubuntu (mirrored networking). Blocked on Public networks." | Out-Null
Write-Host "[+] Windows Firewall rule created (Private, Domain)." -ForegroundColor Green

# Mirrored mode is also filtered by the Hyper-V firewall; allow only this port.
if (Get-Command New-NetFirewallHyperVRule -ErrorAction SilentlyContinue) {
    try {
        Get-NetFirewallHyperVRule -Name $HyperVRuleName -ErrorAction SilentlyContinue | Remove-NetFirewallHyperVRule
        New-NetFirewallHyperVRule -Name $HyperVRuleName -DisplayName $HyperVRuleName `
                                  -Direction Inbound -VMCreatorId $WslVmCreatorId `
                                  -Protocol TCP -LocalPorts $SshPort | Out-Null
        Write-Host "[+] Hyper-V firewall rule created for WSL VM." -ForegroundColor Green
    } catch {
        Write-Host "[!] Could not create Hyper-V firewall rule: $($_.Exception.Message)" -ForegroundColor Yellow
    }
} else {
    Write-Host "[!] Hyper-V firewall cmdlets unavailable (needs Windows 11 22H2+); skipped." -ForegroundColor Yellow
}

# =========================================================
Write-Host "`n=== 8b. Windows Firewall for $AppName (TCP $AppPort; Private + Domain; from: $AppRemote) ===" -ForegroundColor Cyan
Get-NetFirewallRule -DisplayName $AppFwRuleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName $AppFwRuleName `
                    -Direction Inbound -Action Allow -Protocol TCP -LocalPort $AppPort `
                    -Profile Private,Domain -RemoteAddress $AppRemote `
                    -Description "Inbound $AppName (gunicorn) to WSL2 '$DistroName' via mirrored networking. Blocked on Public networks." | Out-Null
Write-Host "[+] Windows Firewall rule created (Private, Domain; remote: $AppRemote)." -ForegroundColor Green

if (Get-Command New-NetFirewallHyperVRule -ErrorAction SilentlyContinue) {
    try {
        Get-NetFirewallHyperVRule -Name $AppHyperVRuleName -ErrorAction SilentlyContinue | Remove-NetFirewallHyperVRule
        New-NetFirewallHyperVRule -Name $AppHyperVRuleName -DisplayName $AppHyperVRuleName `
                                  -Direction Inbound -VMCreatorId $WslVmCreatorId `
                                  -Protocol TCP -LocalPorts $AppPort | Out-Null
        Write-Host "[+] Hyper-V firewall rule created for WSL VM." -ForegroundColor Green
    } catch {
        Write-Host "[!] Could not create Hyper-V firewall rule: $($_.Exception.Message)" -ForegroundColor Yellow
    }
} else {
    Write-Host "[!] Hyper-V firewall cmdlets unavailable (needs Windows 11 22H2+); skipped." -ForegroundColor Yellow
}

# =========================================================
if ($KeepAlive) {
    Write-Host "`n=== 8c. Keep-alive scheduled task ===" -ForegroundColor Cyan
    $taskArgs = "-NoProfile -WindowStyle Hidden -Command `"wsl.exe -d $DistroName --exec /bin/sleep infinity`""
    $action   = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $taskArgs
    $trigger  = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
    $principalTask = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
    Unregister-ScheduledTask -TaskName $KeepAliveTask -Confirm:$false -ErrorAction SilentlyContinue
    Register-ScheduledTask -TaskName $KeepAliveTask -Action $action -Trigger $trigger -Settings $settings -Principal $principalTask `
        -Description "Starts WSL2 instance '$DistroName' at logon and keeps it running (so $AppName and SSH stay available)." | Out-Null
    Start-ScheduledTask -TaskName $KeepAliveTask
    Write-Host "[+] Task '$KeepAliveTask' created and started (runs at logon of $env:USERDOMAIN\$env:USERNAME)." -ForegroundColor Green
}

# =========================================================
Write-Host "`n=== 9. Verification ===" -ForegroundColor Cyan
& wsl.exe -d $DistroName -u root bash -c "systemctl is-active ssh fail2ban $ServiceName; sshd -T | grep -E '^(port|permitrootlogin|passwordauthentication|pubkeyauthentication|allowusers) '; echo -n 'app HTTP status: '; curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:$AppPort/; echo"
# The instance is left running (the app and sshd are live). Without the keep-alive task, WSL will stop it when idle.

Write-Host "`n=========================================================" -ForegroundColor Green
Write-Host " Secure profile build complete" -ForegroundColor Green
Write-Host " Instance      : $DistroName (other instances not modified)" -ForegroundColor Green
Write-Host " $AppName   : http://localhost:$AppPort/ (this PC)   http://<this-PC-LAN-IP>:$AppPort/ (network: $AppRemote)" -ForegroundColor Green
Write-Host " App location  : $AppDir (service '$ServiceName': systemctl status|restart $ServiceName)" -ForegroundColor Green
Write-Host " Keep-alive    : $(if ($KeepAlive) { "task '$KeepAliveTask'" } else { 'not created (WSL stops the idle instance)' })" -ForegroundColor Green
Write-Host " Connect (host): ssh -p $SshPort $NewUser@127.0.0.1   (LAN: this PC's IP)" -ForegroundColor Green
Write-Host " User          : $NewUser (sudo; root locked, root SSH disabled)" -ForegroundColor Green
Write-Host " SSH           : port $SshPort, key + password auth, AllowUsers $NewUser" -ForegroundColor Green
Write-Host " Key installed : $([bool]$PubKey)" -ForegroundColor Green
Write-Host " ODBC 18       : $InstallOdbc" -ForegroundColor Green
Write-Host " Networking    : mirrored (.wslconfig)" -ForegroundColor Green
Write-Host " Firewall      : inbound TCP $SshPort (SSH) and $AppPort ($AppName) on Private/Domain only; fail2ban active" -ForegroundColor Green
Write-Host "=========================================================" -ForegroundColor Green
if ($GeneratedPassword) {
    Write-Host "`nGenerated password for '$NewUser' (shown once, store it now):" -ForegroundColor Yellow
    Write-Host "  $UserPass" -ForegroundColor Yellow
}
$UserPass = $null

Read-Host -Prompt "`nExecution finished. Press ENTER to close this window"
