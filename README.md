# BP_XML_View (Best Practice Software XML Viewer)

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

## ⚙️ Installation & Setup

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
pip install Flask gunicorn pillow striprtf markdown
```

### **3. Run the App Locally**
Start the development server:
```bash
python3 app.py
```
Open [http://localhost:5002](http://localhost:5002) in your browser.

*(Note: The built-in Flask development server runs on Port 5002 by default, while the Gunicorn production service is configured to bind to Port 5007).*

---

## 🖥️ Production Deployment (systemd + Gunicorn)

To run BP_XML_View as a system service on Ubuntu/Debian:

1. Create a service file at `/etc/systemd/system/bp_xml_view.service`:
   ```ini
   [Unit]
   Description=Gunicorn instance to serve BP_XML_View Flask App
   After=network.target

   [Service]
   User=root
   Group=root
   WorkingDirectory=/path/to/BP_XML_View
   Environment="PATH=/path/to/BP_XML_View/.venv/bin"
   ExecStart=/path/to/BP_XML_View/.venv/bin/gunicorn --workers 3 --bind 0.0.0.0:5007 app:app

   [Install]
   WantedBy=multi-user.target
   ```

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

---

## 📄 License

This project is open-source and available under the [MIT License](LICENSE).
