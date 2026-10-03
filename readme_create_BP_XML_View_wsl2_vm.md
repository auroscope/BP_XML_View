# BP_XML_View on WSL2: Provisioning Script

`create_BP_XML_View_wsl2_vm.ps1` builds a new, hardened Ubuntu instance under WSL2 and deploys [BP_XML_View](https://github.com/auroscope/BP_XML_View) (a Flask application) in it, served by gunicorn and reachable from the host and the local network through WSL mirrored networking. It is based on `create_wsl2_ubuntu.ps1` (see `readme_wsl2_ubuntu_sandbox.md` for the shared SSH and hardening details) and adds the application deployment, its firewall rules and an optional keep-alive task.

The script is interactive. It creates a **new** instance and never modifies an existing one; deleting old instances is opt-in and needs a typed confirmation.

## Contents

1. [What it does](#what-it-does)
2. [Requirements](#requirements)
3. [Running it](#running-it)
4. [Prompts and defaults](#prompts-and-defaults)
5. [Execution flow](#execution-flow)
6. [Resulting configuration](#resulting-configuration)
7. [Accessing the application](#accessing-the-application)
8. [Keeping the instance running](#keeping-the-instance-running)
9. [Operating the application](#operating-the-application)
10. [Security notes](#security-notes)
11. [Troubleshooting](#troubleshooting)
12. [Cleanup and rollback](#cleanup-and-rollback)
13. [Known limitations](#known-limitations)

## What it does

- Lists existing WSL instances and optionally deletes selected ones.
- Installs a new Ubuntu instance under a name you choose (default `ubuntu_bp_xml_view`).
- Ensures `networkingMode=mirrored` in `%USERPROFILE%\.wslconfig`.
- Creates a non-root sudo user and locks the root password.
- Enables systemd, installs and hardens OpenSSH, and configures fail2ban.
- Clones BP_XML_View to `~/flask/BP_XML_View`, creates a `.venv`, and installs `requirements.txt`.
- Runs the app under gunicorn as a systemd service started at instance boot.
- Creates Windows Defender Firewall and Hyper-V firewall rules for both SSH and the application port.
- Optionally installs Microsoft ODBC Driver 18 and creates a scheduled task to keep the instance running.

## Requirements

| Item | Requirement |
|---|---|
| OS | Windows 11. The Hyper-V firewall step needs 22H2 or later and is skipped with a warning otherwise. |
| WSL | Recent WSL 2 (`wsl --version`). `wsl --install --name` needs roughly 2.4.4 or later. Run `wsl --update` if install fails. |
| Shell | Windows PowerShell 5.1, **elevated** (the script throws if not Administrator). |
| Network | Internet access to the Ubuntu image, apt, GitHub and PyPI. The repository must remain public, as the script has no credential handling. |
| Virtualisation | Enabled in firmware and Windows. |
| Optional | An SSH public key at `%USERPROFILE%\.ssh\id_ed25519.pub` (or another path you supply). |

## Running it

The script can live in any folder. From an elevated PowerShell:

```powershell
powershell.exe -NoExit -ExecutionPolicy Bypass -File .\create_BP_XML_View_wsl2_vm.ps1
```

Or with the full path:

```powershell
powershell.exe -NoExit -ExecutionPolicy Bypass -File "<path-to-script>\create_BP_XML_View_wsl2_vm.ps1"
```

As a shortcut target, use the full path and set the shortcut to run as administrator. `-ExecutionPolicy Bypass` applies to that one process only. If the script was copied from another machine, run `Unblock-File` on it first.

## Prompts and defaults

Press Enter to accept a default. Prompts appear in this order.

| Prompt | Default | Notes |
|---|---|---|
| Numbers to DELETE | none | Only shown if instances exist. Each deletion needs the exact instance name typed to confirm. |
| Name for the NEW WSL instance | `ubuntu_bp_xml_view` | Letters, digits, `_`, `-`, `.`. Rejected if the name already exists. |
| SSH port | `22`, or `2222` if something already listens on TCP 22 | Rejected if in use on the host. |
| Port for BP_XML_View (gunicorn) | `5007` | 1024-65535. Rejected if equal to the SSH port or already in use on the host. |
| Allow BP_XML_View from | `1` Local subnet only | `2` = Anywhere. Applies to the Windows Defender rule (Private and Domain profiles in both cases). |
| Linux username | `defaultuser` | Set by `$DefaultUser`. Cannot be `root`. |
| Password | blank = generate | Confirmed when typed. A generated 24-character password is shown once at the end. |
| Public key file | `%USERPROFILE%\.ssh\id_ed25519.pub` | If missing, SSH is password-only. |
| Install ODBC Driver 18 | **No** | BP_XML_View does not need it. |
| Create keep-alive scheduled task | **No** | See [Keeping the instance running](#keeping-the-instance-running). |
| Run `wsl --shutdown` now | Yes | Only asked if `.wslconfig` actually changed. |

The defaults (`$BaseDistro`, `$DefaultName`, `$RepoUrl`, `$Branch`, `$DefaultAppPort`, `$DefaultUser`, `$PubKeyDefault`) are at the top of the script.

## Execution flow

| Step | Action |
|---|---|
| 0 | Interactive setup (above). |
| 1 | Prints the plan. |
| 2 | Ensures `networkingMode=mirrored` in `.wslconfig`, preserving other settings. Offers `wsl --shutdown` only if the file changed (stops all running distros without modifying them). |
| 3 | `wsl --install -d Ubuntu --name <name> --no-launch`, then verifies the instance is registered. |
| 4 | Creates the user, sets the password over stdin, adds to `sudo`, locks root. |
| 5 | Writes `/etc/wsl.conf` (default user, `systemd=true`) and restarts the instance so systemd is active. |
| 6 | Installs `git net-tools curl openssh-server fail2ban python3 python3-venv python3-pip`. |
| 6a | Deploys the app as the new user (clone, `.venv`, `pip install -r requirements.txt`, import check), writes and starts the `bp_xml_view` systemd service, and waits up to 20 seconds for an HTTP response. On failure it prints the service journal and stops. |
| 6b | Optional: Microsoft ODBC Driver 18, `sqlcmd` and `bcp`. |
| 7 | SSH hardening drop-in, host keys, `sshd -t`, service switch from `ssh.socket`, fail2ban jail. |
| 8 | Windows Firewall and Hyper-V firewall rules for SSH. |
| 8b | Windows Firewall and Hyper-V firewall rules for the application port. |
| 8c | Optional: the keep-alive scheduled task. |
| 9 | Verification (service states, effective sshd values, app HTTP status) and summary. The instance is left running. |

Any failing step aborts the script. Passwords and files are passed over stdin with carriage returns stripped, and the embedded deploy script is written to a temporary file in the instance, so tools cannot consume the script from stdin.

## Resulting configuration

### Windows host

| Item | Detail |
|---|---|
| `%USERPROFILE%\.wslconfig` | `[wsl2]` gains `networkingMode=mirrored` (other settings preserved). |
| SSH firewall rule | `WSL2 <name> SSH Server (TCP <ssh-port>)`: inbound, Private and Domain. |
| SSH Hyper-V rule | `WSL2 <name> SSH Server (TCP <ssh-port>, Hyper-V)`. |
| App firewall rule | `WSL2 <name> BP_XML_View (TCP <app-port>)`: inbound, Private and Domain, remote address `LocalSubnet` or `Any` as chosen. |
| App Hyper-V rule | `WSL2 <name> BP_XML_View (TCP <app-port>, Hyper-V)`. It cannot be limited to the subnet; the Defender rule does the scoping. |
| Scheduled task (optional) | `WSL2 <name> keep-alive`. |

### Inside the instance

| Path or item | Purpose |
|---|---|
| `/home/<user>/flask/BP_XML_View` | The cloned repository. |
| `/home/<user>/flask/BP_XML_View/.venv` | Python virtual environment with the app's dependencies. |
| `/etc/systemd/system/bp_xml_view.service` | Runs gunicorn as `<user>`: `--workers 3 --threads 3 --timeout 300 --bind 0.0.0.0:<app-port> app:app`, `Restart=on-failure`, enabled at boot. |
| `/etc/wsl.conf` | Default user and `systemd=true`. |
| `/etc/ssh/sshd_config.d/99-hardening.conf` | sshd hardening (no root login, `AllowUsers`, 3 attempts, forwarding off, and so on). |
| `/etc/fail2ban/jail.local` | sshd jail (1h ban, 4 failures in 10 minutes). |

The service runs gunicorn rather than `python app.py`. `app.py` ends with `app.run(host='0.0.0.0', ..., debug=True)`, and running that directly would expose Flask's debugger to the network.

## Accessing the application

| From | URL |
|---|---|
| The Windows host | `http://localhost:<app-port>/` |
| Another machine on the LAN | `http://<host-LAN-IP>:<app-port>/` |
| SSH from the host | `ssh -p <ssh-port> <user>@127.0.0.1` |
| SSH from the LAN | `ssh -p <ssh-port> <user>@<host-LAN-IP>` |

The host cannot reach the instance through its own LAN IP. Windows treats a connection to its own address as local and nothing on Windows listens there. Use `localhost` or `127.0.0.1` on the host. From other machines the Windows and Hyper-V rules must allow the port, and the network must be a Private or Domain profile.

## Keeping the instance running

WSL stops an instance once nothing is attached to it, and systemd services do not count as attached. Without something keeping it alive, the app and SSH go offline after you close your last terminal, and nothing starts the instance after a reboot.

The optional keep-alive task addresses this:

- Name: `WSL2 <name> keep-alive`.
- Trigger: at logon of the user who ran the script.
- Action: a hidden `powershell.exe` running `wsl.exe -d <name> --exec /bin/sleep infinity`.
- Runs as that user (interactive, limited), with no time limit, restarting up to 3 times on failure. It is started immediately when created.

Limitation: it runs only while that user is logged in. After a reboot, the app stays down until that user logs in. For an always-on server, consider configuring Windows automatic logon, or a different approach suited to your environment. Remove the task with `Unregister-ScheduledTask -TaskName "WSL2 <name> keep-alive" -Confirm:$false`.

## Operating the application

Run these from the Windows host (replace `<name>` and `<user>`).

```powershell
# Status and recent logs
wsl -d <name> -u root systemctl status bp_xml_view --no-pager
wsl -d <name> -u root journalctl -u bp_xml_view -n 50 --no-pager

# Restart / stop / start
wsl -d <name> -u root systemctl restart bp_xml_view

# Update the app from GitHub, refresh dependencies, restart
wsl -d <name> -u <user> -- bash -c "cd ~/flask/BP_XML_View && git pull --ff-only && .venv/bin/pip install -r requirements.txt"
wsl -d <name> -u root systemctl restart bp_xml_view
```

A restart interrupts requests in progress. Uploaded files are stored in the repository's `working/` folder, which is untracked by git, so `git pull` and restarts do not remove them. A `git pull` can still fail if local changes conflict.

To open a shell in the instance as the user: `wsl -d <name> -u <user>`.

## Security notes

- Network exposure: the app listens on `0.0.0.0` inside the shared network. Reach is controlled by the Windows Firewall rule: **Local subnet only** (default) or **Anywhere**, always limited to Private and Domain profiles. Choose the narrowest scope that works.
- The application handles patient health record data. Treat the host, instance and network accordingly.
- `app.py` sets a hardcoded Flask `secret_key`. The script does not audit the application for authentication, so assume anyone who can reach the port can use it.
- gunicorn is used instead of Flask's debug server (no Werkzeug debugger exposure).
- SSH posture is as in `readme_wsl2_ubuntu_sandbox.md`: no root login, `AllowUsers`, key and password auth, 3 attempts, forwarding disabled, fail2ban, Private and Domain only. Password login is on by design; consider key-only once your key works.
- Passwords are not stored in the script. They are prompted for, or generated and shown once.

## Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| Script ends with "did not respond on port ..." | Read the journal it prints, or `journalctl -u bp_xml_view`. Common causes: Python older than the project needs (the deploy step warns about this), a dependency failure, or a port clash inside the instance. |
| `wsl --install --name failed` | WSL is too old. Run `wsl --update` and retry. Nothing existing has been modified. |
| `git clone` fails | No internet or GitHub access from the instance, a proxy, or the repository is no longer public. |
| `pip install` fails | PyPI unreachable, or a package needs a newer Python. Check the pip output. |
| App works on the host (`localhost`) but not from the LAN | Check the network profile (`Get-NetConnectionProfile`) is Private or Domain. Confirm the Windows and Hyper-V rules exist. Check the app firewall scope (`LocalSubnet` excludes other subnets). Check upstream firewalls. |
| Hyper-V rule skipped | The cmdlets need Windows 11 22H2 or later. |
| Port already in use | Mirrored mode shares one network between the host and all instances. Pick another port. |
| App and SSH stop after you close your terminal | WSL stopped the idle instance. See [Keeping the instance running](#keeping-the-instance-running). |
| Mirrored mode not active | `.wslconfig` applies only after `wsl --shutdown`. Check it contains `networkingMode=mirrored` under `[wsl2]`. |
| Browser shows a connection error on `<host-LAN-IP>` from the host itself | Expected; use `localhost` on the host. |
| Locked out of SSH by fail2ban | `wsl -d <name> -u root fail2ban-client set sshd unbanip <ip>`, or wait out the 1 hour ban. |

Useful checks:

```powershell
wsl --list --verbose
Get-NetFirewallRule -DisplayName "WSL2 <name>*"
Get-NetFirewallHyperVRule -Name "WSL2 <name>*"
Get-NetTCPConnection -LocalPort <app-port> -State Listen
Get-ScheduledTask -TaskName "WSL2 <name> keep-alive"
```

## Cleanup and rollback

Remove an instance and everything the script created for it (replace `<name>`, `<ssh-port>` and `<app-port>`):

```powershell
wsl --terminate <name>
wsl --unregister <name>        # permanently deletes the instance and all its data, including uploaded files
Remove-NetFirewallRule -DisplayName "WSL2 <name> SSH Server (TCP <ssh-port>)"
Remove-NetFirewallRule -DisplayName "WSL2 <name> BP_XML_View (TCP <app-port>)"
Remove-NetFirewallHyperVRule -Name "WSL2 <name> SSH Server (TCP <ssh-port>, Hyper-V)"
Remove-NetFirewallHyperVRule -Name "WSL2 <name> BP_XML_View (TCP <app-port>, Hyper-V)"
Unregister-ScheduledTask -TaskName "WSL2 <name> keep-alive" -Confirm:$false
```

To revert mirrored networking, remove `networkingMode=mirrored` from `%USERPROFILE%\.wslconfig` and run `wsl --shutdown`. Other tooling or instances may rely on it.

## Known limitations

- The script creates new instances only. To change settings later (ports, scope), edit the rules and service manually or build a new instance and delete the old one.
- One process can bind a given port at a time; all instances and the host share the network in mirrored mode.
- `wsl --shutdown` (offered only when `.wslconfig` changes) stops every running distro, including ones you did not create.
- Deleted instances cannot be recovered; the script does not take backups.
- The keep-alive task only works while the user is logged in.
- The project README targets Python 3.13+. Ubuntu's default Python in WSL may be older; the script warns but continues.
- The script has no support for private repositories or a different application. Change `$RepoUrl`, `$AppName` and `$ServiceName` at the top to reuse it for another Flask project, and review the deploy step for app-specific assumptions.
