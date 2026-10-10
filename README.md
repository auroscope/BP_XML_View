# BP_XML_View (Best Practice Software XML Viewer)

> [!IMPORTANT]
> **This project is no longer maintained.** Development continues at
> [auroscope/xml_curator_for_BP_MD](https://github.com/auroscope/xml_curator_for_BP_MD), which keeps all of
> BP_XML_View's features and adds PDF/RTF shrinking, document details editing, OCR and MedicalDirector support.
> Please use that project for new installs.

BP_XML_View is a lightweight, high-performance Python Flask application designed to parse, display, manage, and edit patient Electronic Health Record (EHR) XML payloads exported from **Best Practice Software (BPSEHRV2)**. 

The application utilizes a secure, serverless file-based approach. It treats the uploaded patient XML files themselves as the primary data store, dynamically parsing and writing modifications directly back to the files. It handles complex healthcare datasets, dynamic binary extraction from base64 ZIP archives, RTF parsing into clean HTML tables, image conversions (TIF to browser-renderable formats), and clinical observations visualization.

## 🎯 Primary Use Case

The primary use case for this application is to examine and allow **selective deletion** of content within a Best Practice Software XML patient export file. 

During clinical data migrations, patient transfers, or record sharing, XML export files can contain unnecessary, low-value, or bloated documents (such as multi-megabyte scanning outputs or redundant administrative paperwork). By using this application to review and selectively delete these items from the XML payload *prior* to import, users can prevent database bloat and ensure that only clean, high-value clinical content is imported into their local **Best Practice Database**.

---

## 🚀 Key Features

### 1. **EHR Data Extraction & Mapping**
The application extracts and structures patient information across major clinical subsections:
* **Demographics:** Parses patient identification nodes (`<Patient>`), extracting full names, contact info, date of birth, address, and Medicare numbers.
* **Allergies & Adverse Reactions:** Maps offending substances (`<Reaction>`) and specific clinical side effects.
* **Current Medications:** Identifies active prescriptions based on lifecycle dates and status codes.
* **Past/Ceased Medications:** Identifies and filters discontinued historical prescriptions.
* **Clinical Visits:** Extracts historical patient visit timelines and physician notes.
* **Pathology & Investigations:** Parses clinical reports, categorizing them into atomized test results and non-atomized unstructured reports.

### 2. **In-Browser Document & Letter Viewer**
The XML schema packages external attachments (Medical Certificates, Letters, Radiology/Histopathology Reports) as Base64-encoded ZIP payloads. 
* **Dynamic Decryption & Unzipping:** Decodes base64 strings and decompresses ZIP archives on the fly in memory.
* **RTF-to-HTML Table Reconstruction:** Decodes Rich Text Format (`.rtf`) files, converting pipe/tab-delimited records into responsive, beautifully structured HTML tables.
* **On-the-Fly Image Conversion:** Converts TIF image files (unrenderable in modern browsers) into web-friendly formats using Pillow.
* **Multi-Format Streaming:** Serves PDFs, text files, and standard images inline.

### 3. **LOINC-Based Pathology Charts**
The application tracks historical clinical markers and maps them to standard **LOINC (Logical Observation Identifiers Names and Codes)**.
* **Trend Visualizations:** Plots trends over time for critical metrics like Blood Pressure, HbA1c, Cholesterol levels, and standard lab values.
* **Interactive Charting:** Uses Chart.js to render sleek, responsive, interactive graphs.
* **Print Optimization:** Formatted with tailored CSS media queries to ensure clean print layouts without broken charts or cutoffs.

### 4. **EHR Editing & Deletion**
Unlike static viewers, BP_XML_View allows clinicians or administrative staff to manage patient data directly:
* **Interactive Deletion:** Select individual clinical items (vitals, medications, allergies, discrete results, reports, or letters) to delete.
* **Bulk Document Deletion:** Multi-select and remove multiple base64-encoded binary documents in the document viewer sidebar.
* **State Persistence:** Sidebar states and checkbox selections are preserved across page refreshes.
* **Download Modified XML:** Re-saves the XML structure to disk using Python's `ElementTree` XML library and provides a download button to export the trimmed XML payload.

---

## 🛠️ Architecture

BP_XML_View is built using a minimal, modular tech stack:
* **Backend:** Flask (Python 3.13)
* **Frontend:** Vanilla HTML5, CSS3, and JavaScript, with Chart.js for data visualization.
* **EHR Processing:** `xml.etree.ElementTree` for tree traversal and modification, and `re` for schema sanitization.
* **Binary Extraction:** `base64`, `zipfile`, and `io` for unzipping attachments in memory.
* **Format Parsers:** `striprtf` for document extraction, and `PIL` (Pillow) for image rendering.
* **Deployment:** Served via Gunicorn as a systemd background daemon on Port 5007.

---

## ⚡ Automated Install Scripts (`install_scripts/`)

The [`install_scripts/`](install_scripts/) directory contains automations that install and run BP_XML_View for you. Pick the one that matches your situation:

| Your situation | Use | What it does |
|---|---|---|
| **Windows 11 PC, no Linux set up yet.** You want the app running in its own isolated Linux VM. | [`create_BP_XML_View_wsl2_vm.ps1`](install_scripts/create_BP_XML_View_wsl2_vm.ps1)<br>Guide: [`readme_create_BP_XML_View_wsl2_vm.md`](install_scripts/readme_create_BP_XML_View_wsl2_vm.md) | Builds a **new** hardened WSL2 Ubuntu instance, deploys the app (clone, `.venv`, `requirements.txt`) as a systemd service on gunicorn, and creates the Windows and Hyper-V firewall rules for the app and SSH. This is the one-stop option for Windows. |
| **An Ubuntu system already exists.** A bare-metal server, a VM, a cloud instance, or a WSL2 Ubuntu you have already set up. You only need the app installed. | [`setup_bp_xml_view.sh`](install_scripts/setup_bp_xml_view.sh) | Run it as a normal sudo user. It clones the repo to `~/flask/BP_XML_View`, creates `.venv`, installs `requirements.txt`, and runs the app under gunicorn as a systemd user service (starts at boot). It opens the port in `ufw`, limited to your local network or open to anywhere (you choose), and checks the port is free first. It does **not** harden the operating system or SSH. |
| **Windows 11 PC, you want a hardened Ubuntu WSL2 VM with SSH but not the app** (or you plan to install the app yourself). | [`create_wsl2_ubuntu_sandbox.ps1`](install_scripts/create_wsl2_ubuntu_sandbox.ps1)<br>Guide: [`readme_wsl2_ubuntu_sandbox.md`](install_scripts/readme_wsl2_ubuntu_sandbox.md) | Builds a **new** hardened WSL2 Ubuntu instance with SSH, fail2ban, mirrored networking and Windows firewall rules. Contains no application. You can then run `setup_bp_xml_view.sh` inside it. |
| **Development, another operating system, or you prefer to do it by hand.** | [Manual installation](#manual-installation) below | Step-by-step commands. |

### Things all the PowerShell scripts have in common

* Run them from an **elevated (Administrator)** Windows PowerShell 5.1. They stop immediately if not elevated.
* They are **interactive**: instance name, ports, user name, password and firewall scope are all prompted for, with sensible defaults. Passwords are never stored in the script.
* They create a **new** WSL instance and **never modify an existing one**. Deleting old instances is opt-in and needs the exact instance name typed to confirm.
* They need only the script file itself, not the whole repository; the application scripts clone it for you.
* They run a **preflight check** first: Windows 11 22H2 or later, and WSL 2.4.4 or later. If WSL is missing or too old they offer to install or update it. **A reboot may be required**; if so the script stops, you restart Windows, and you run it again.
* If a script was downloaded or copied from another machine, you may first need to unblock it: `Unblock-File .\<script>.ps1`.

### Quick start

**Windows (new isolated VM with the app):** open an elevated PowerShell in the folder holding the script and run:
```powershell
powershell.exe -NoExit -ExecutionPolicy Bypass -File .\create_BP_XML_View_wsl2_vm.ps1
```
`-ExecutionPolicy Bypass` applies to that one process only. To launch it from a desktop shortcut or the right-click menu instead, see [`win_powershell_shortcut_how-to.md`](install_scripts/win_powershell_shortcut_how-to.md).

**Existing Ubuntu system:** run as your normal sudo user (not as root):
```bash
git clone https://github.com/auroscope/BP_XML_View.git
cd BP_XML_View/install_scripts
chmod +x setup_bp_xml_view.sh
./setup_bp_xml_view.sh
```

### Before you run them

* **Read the scripts first.** They install software, create users, and change firewall rules, so review anything you run with elevated privileges.
* **This application handles patient health data.** When asked who may reach the app, choose **local network only** unless you have a specific reason not to, and keep the host on a trusted network. See [Security & Privacy Notes](#-security--privacy-notes).
* On Windows, WSL stops an idle instance, which takes the app offline. The WSL application script can optionally create a scheduled task to keep it running; see its guide.

---

<a id="manual-installation"></a>

## ⚙️ Manual Installation & Setup

If you prefer not to use the [automated scripts](#-automated-install-scripts-install_scripts), install by hand:

### **Prerequisites**
* Python 3.13+
* Git

### **1. Clone the Repository**
```bash
git clone https://github.com/auroscope/BP_XML_View.git
cd BP_XML_View
```

### **2. Setup Virtual Environment & Install Dependencies**
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### **3. Run the App Locally**
Start the development server:
```bash
python3 app.py
```
Open [http://localhost:5002](http://localhost:5002) (or `http://<ip_of_host>:5002` if hosting on another machine or in WSL2 with appropriate firewall settings) in your browser.

*(Note: Since the server binds to `0.0.0.0`, it is accessible across your local network. The built-in Flask development server runs on Port 5002 by default, while the Gunicorn production service is configured to bind to Port 5007).*

> **Caution:** `app.py` starts Flask's development server with `debug=True`, and Flask's debugger allows code execution for anyone who can reach it. Use `python3 app.py` only on a machine you trust, on a network you trust. For anything shared, serve the app with Gunicorn as shown below (this is what the install scripts do).

---

## 🖥️ Production Deployment (systemd + Gunicorn)

To run BP_XML_View as a system service on Ubuntu/Debian (the [install scripts](#-automated-install-scripts-install_scripts) automate this):

1. Create a service file at `/etc/systemd/system/bp_xml_view.service`:
   ```ini
   [Unit]
   Description=Gunicorn instance to serve BP_XML_View Flask App
   After=network.target

   [Service]
   User=<service-user>
   Group=<service-user>
   WorkingDirectory=/path/to/BP_XML_View
   Environment="PATH=/path/to/BP_XML_View/.venv/bin"
   ExecStart=/path/to/BP_XML_View/.venv/bin/gunicorn --workers 3 --threads 3 --timeout 300 --bind 0.0.0.0:5007 app:app

   [Install]
   WantedBy=multi-user.target
   ```
   Run the service as an unprivileged user that owns the application folder, rather than `root`. The install scripts do this.

2. Enable and start the system service:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable bp_xml_view.service
   sudo systemctl start bp_xml_view.service
   ```

3. Check service status:
   ```bash
   sudo systemctl status bp_xml_view.service
   ```

---

## 🔒 Security & Privacy Notes

* **No Database Storage:** The application does not store patient data in SQL or NoSQL databases. Everything is dynamically read from and written to the uploaded patient XML file in the `working/` directory.
* **Clean Git Repository:** The `.gitignore` is pre-configured to strictly ignore patient XML records, decoded binary attachments, logs, security certificates, and local environment secrets.
* **Production Deployment:** In production, it is highly recommended to run this application behind an HTTPS-terminating reverse proxy (such as Cloudflare, Nginx, or an upstream firewall/load balancer) to protect patient clinical data in transit.
* **Network Exposure:** The server binds to `0.0.0.0`. Limit who can reach the port (for example to your local network) using the host firewall; the install scripts offer this choice.
* **Installation Automations:** The scripts in `install_scripts/` run with elevated privileges and change system and firewall configuration. Review them before use.

---

## 📄 License

This project is open-source and available under the [MIT License](LICENSE).
