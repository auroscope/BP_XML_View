# EHR Data Extraction Proposal

Based on a detailed structural analysis of the newly provided `BPSEHRV2` (Best Practice Software V1.13) XML file, the patient's medical record is natively mapped into well-structured XML tags. I propose the following top five initial data extractions to fulfill the objective of displaying, exporting, and managing subsections of the patient's medical record.

## 1. Demographics
* **Source:** `<Demographics><Patient>`
* **Details:** We will extract core identifying data including `<FIRSTNAME>`, `<SURNAME>`, `<DOB>`, `<ADDRESS1>`, `<MOBILEPHONE>`, and `<MEDICARENO>`. This dataset will form the patient summary header on the main display page.

## 2. Past Medical History
* **Source:** `<PastHistory><Condition>`
* **Details:** The patient's historical diagnoses are explicitly structured. We will parse these tags to extract the condition names (`<ITEMTEXT>`), the onset date/year (`<YEAR>`), and any associated clinical remarks (`<DETAILS>`). This allows for an accurate timeline of the patient's chronic conditions.

## 3. Allergies & Adverse Reactions
* **Source:** `<Reactions><Reaction>`
* **Details:** We will parse the reaction list to extract the offending substance (`<ITEMNAME>`) and the specific adverse reaction (`<REACTION>`), such as "Codeine" causing "PAIN ABDOMEN". This is critical safety information to display prominently.

## 4. Current Medications
* **Source:** `<CurrentRx><Medication>`
* **Details:** The active prescription list is neatly structured. We will extract the exact drug formulation (`<DRUGNAME>`), dosage and instructions (`<DOSE>`, `<INSTRUCTIONS>`), and the prescription lifecycle (`<FIRSTDATE>`, `<LASTDATE>`) to present a clean, structured medication table.

## 5. Scanned Documents & Correspondence (Binaries)
* **Source:** `<Document><Content>`
* **Details:** The XML bundles external files (such as Medical Certificates, Radiology Reports, and Histopathology findings) as base64-encoded ZIP archives directly within the `<Content>` tags. We will build an extraction pipeline that decodes these base64 payloads, unzips the binaries (such as BMP, PDF, or RTF files defined in `<DocType>` and `<FileName>`), and securely serves them via the web interface for in-browser viewing or download.

---

*Future Roadmap: As the platform matures, we will expand these extractions to include structured Histopathology/Laboratory results (`<Investigations>`), Immunisation timelines, Social History, and automated export mechanisms (JSON/CSV/PDF) for seamless clinical interoperability.*