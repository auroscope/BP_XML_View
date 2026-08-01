import os
import markdown
import xml.etree.ElementTree as ET
import re
import base64
import zipfile
import io
import subprocess
import json
import uuid
from PIL import Image
from striprtf.striprtf import rtf_to_text
from flask import Flask, render_template_string, request, redirect, flash, url_for, send_file, Response
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = "secret_key_for_session"
UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'working')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Global store for generated JSON files (for download)
json_store = {}

# --- Utilities ---

@app.route('/patient/<filename>/delete', methods=['POST'])
def delete_items(filename):
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(filename))
    if not os.path.exists(filepath):
        flash("File not found.", "danger")
        return redirect(url_for('view_patient', filename=filename))

    selected_items = request.form.getlist('selected_items')
    if not selected_items:
        flash("No items selected for deletion.", "warning")
        return redirect(url_for('view_patient', filename=filename))

    try:
        # Read file bytes
        with open(filepath, 'rb') as f:
            xml_data = f.read()
        
        # Strip UTF-8 BOM if present
        if xml_data.startswith(b'\xef\xbb\xbf'):
            xml_data = xml_data[3:]
            
        xml_text = xml_data.decode('ISO-8859-1')
        xml_text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', xml_text)

        root = ET.fromstring(xml_text)
        parent_map = {c: p for p in root.iter() for c in p}

        # Group selected items by category
        from collections import defaultdict
        grouped = defaultdict(list)
        for item in selected_items:
            if ':' in item:
                cat, val = item.split(':', 1)
                grouped[cat].append(val)

        deleted_count = 0

        # Category: history
        if 'history' in grouped:
            indices = sorted([int(x) for x in grouped['history']], reverse=True)
            history = []
            for cond in root.findall('.//PastHistory/Condition'):
                year = get_text(cond, 'YEAR')
                history.append({
                    'element': cond,
                    'year': year if year != '0' else 'Unknown'
                })
            history.sort(key=lambda x: int(x['year']) if x['year'].isdigit() else 0, reverse=True)
            for idx in indices:
                if 0 <= idx < len(history):
                    node = history[idx]['element']
                    parent = parent_map.get(node)
                    if parent is not None:
                        parent.remove(node)
                        deleted_count += 1

        # Category: allergies
        if 'allergies' in grouped:
            indices = sorted([int(x) for x in grouped['allergies']], reverse=True)
            allergies = []
            for rxn in root.findall('.//Reactions/Reaction'):
                allergies.append({'element': rxn})
            for idx in indices:
                if 0 <= idx < len(allergies):
                    node = allergies[idx]['element']
                    parent = parent_map.get(node)
                    if parent is not None:
                        parent.remove(node)
                        deleted_count += 1

        # Category: meds_current
        if 'meds_current' in grouped:
            indices = sorted([int(x) for x in grouped['meds_current']], reverse=True)
            meds_current = []
            for med in root.findall('.//CurrentRx/Medication'):
                status = get_text(med, 'RXSTATUS')
                if status in ['1', '2']:
                    meds_current.append({'element': med})
            for idx in indices:
                if 0 <= idx < len(meds_current):
                    node = meds_current[idx]['element']
                    parent = parent_map.get(node)
                    if parent is not None:
                        parent.remove(node)
                        deleted_count += 1

        # Category: meds_past
        if 'meds_past' in grouped:
            indices = sorted([int(x) for x in grouped['meds_past']], reverse=True)
            meds_past = []
            for med in root.findall('.//CurrentRx/Medication'):
                status = get_text(med, 'RXSTATUS')
                if status not in ['1', '2']:
                    meds_past.append({'element': med})
            for idx in indices:
                if 0 <= idx < len(meds_past):
                    node = meds_past[idx]['element']
                    parent = parent_map.get(node)
                    if parent is not None:
                        parent.remove(node)
                        deleted_count += 1

        # Category: visits
        if 'visits' in grouped:
            indices = sorted([int(x) for x in grouped['visits']], reverse=True)
            visits = []
            for visit in root.findall('.//Visit'):
                visits.append({
                    'element': visit,
                    'date': get_text(visit, 'VISITDATE')
                })
            visits.sort(key=lambda x: [-int(part) for part in x['date'].split('/')[::-1]] if '/' in x['date'] else [-1])
            for idx in indices:
                if 0 <= idx < len(visits):
                    node = visits[idx]['element']
                    parent = parent_map.get(node)
                    if parent is not None:
                        parent.remove(node)
                        deleted_count += 1

        # Category: results
        if 'results' in grouped:
            indices = sorted([int(x) for x in grouped['results']], reverse=True)
            allowed_loinc = {
                '10334-1','10535-3','12841-3','13965-9','14334-7','14631-6','14635-7','14646-4','14647-2','14685-2',
                '14771-0','14804-9','14805-6','14920-3','14927-8','14928-6','14933-6','1742-6','1743-4','17856-6',
                '1920-8','1988-5','2064-4','2157-6','22748-8','2276-4','2324-2','24108-3','2532-0',
                '2601-3','2823-3','2857-1','2885-2','2890-2','29265-6','2951-2','3016-3','32294-1','32309-7',
                '33914-3','39469-2','43396-1','4537-7','4544-3','4548-4','50210-4','59261-8','61152-5','6690-2',
                '6768-6','70204-3','711-2','718-7','72160-5','731-0','742-7','751-8','777-3','787-2','789-8','9830-1'
            }
            results = []
            for res in root.findall('.//Result'):
                name = get_text(res, 'RESULTNAME')
                if not name: continue
                loinc_code = get_text(res, 'LOINCCODE')
                if loinc_code not in allowed_loinc: continue
                results.append({
                    'element': res,
                    'loinc': loinc_code,
                    'date': get_text(res, 'REPORTDATE')
                })
            results.sort(key=lambda x: (x['loinc'], [-int(part) for part in x['date'].split('/')[::-1]] if '/' in x['date'] else [-1]))
            for idx in indices:
                if 0 <= idx < len(results):
                    node = results[idx]['element']
                    parent = parent_map.get(node)
                    if parent is not None:
                        parent.remove(node)
                        deleted_count += 1

        # Category: investigations
        if 'investigations' in grouped:
            indices = sorted([int(x) for x in grouped['investigations']], reverse=True)
            investigations = []
            for inv in root.findall('.//Investigation'):
                inv_id = get_text(inv, 'REPORTID')
                if not inv_id: continue
                if root.find(f".//Result[REPORTID='{inv_id}']") is None and inv.find('.//InvestigationPage') is None:
                    investigations.append({
                        'element': inv,
                        'date': get_text(inv, 'REPORTDATE')
                    })
            investigations.sort(key=lambda x: [-int(part) for part in x['date'].split('/')[::-1]] if '/' in x['date'] else [-1])
            for idx in indices:
                if 0 <= idx < len(investigations):
                    node = investigations[idx]['element']
                    parent = parent_map.get(node)
                    if parent is not None:
                        parent.remove(node)
                        deleted_count += 1

        # Category: documents
        if 'documents' in grouped:
            for doc_id in grouped['documents']:
                node = root.find(f".//Document[DOCUMENTID='{doc_id}']") or root.find(f".//Investigation[REPORTID='{doc_id}']")
                if node is not None:
                    parent = parent_map.get(node)
                    if parent is not None:
                        parent.remove(node)
                        deleted_count += 1

        # Category: correspondence
        if 'correspondence' in grouped:
            for corr_id in grouped['correspondence']:
                node = root.find(f".//CorrespondenceOut/Correspondence[RECORDID='{corr_id}']")
                if node is not None:
                    parent = parent_map.get(node)
                    if parent is not None:
                        parent.remove(node)
                        deleted_count += 1

        # Save XML back to disk
        xml_str = ET.tostring(root, encoding='ISO-8859-1')
        
        # Write XML string with declaration
        with open(filepath, 'wb') as f:
            f.write(xml_str)

        flash(f"Successfully deleted {deleted_count} item(s) from the XML payload.", "success")
    except Exception as e:
        flash(f"Error modifying XML payload: {str(e)}", "danger")

    return redirect(url_for('view_patient', filename=filename))

    selected_items = request.form.getlist('selected_items')
    if not selected_items:
        flash("No items selected for deletion.", "warning")
        return redirect(url_for('view_patient', filename=filename))

    try:
        # Read file bytes
        with open(filepath, 'rb') as f:
            xml_data = f.read()
        
        # Strip UTF-8 BOM if present
        if xml_data.startswith(b'\xef\xbb\xbf'):
            xml_data = xml_data[3:]
            
        xml_text = xml_data.decode('ISO-8859-1')
        xml_text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', xml_text)

        root = ET.fromstring(xml_text)
        parent_map = {c: p for p in root.iter() for c in p}

        # Group selected items by category
        from collections import defaultdict
        grouped = defaultdict(list)
        for item in selected_items:
            if ':' in item:
                cat, val = item.split(':', 1)
                grouped[cat].append(val)

        deleted_count = 0

        # Category: history (Past History conditions)
        if 'history' in grouped:
            indices = sorted([int(x) for x in grouped['history']], reverse=True)
            conditions = root.findall('.//PastHistory/Condition')
            for idx in indices:
                if 0 <= idx < len(conditions):
                    node = conditions[idx]
                    parent = parent_map.get(node)
                    if parent is not None:
                        parent.remove(node)
                        deleted_count += 1

        # Category: allergies
        if 'allergies' in grouped:
            indices = sorted([int(x) for x in grouped['allergies']], reverse=True)
            reactions = root.findall('.//Reactions/Reaction')
            for idx in indices:
                if 0 <= idx < len(reactions):
                    node = reactions[idx]
                    parent = parent_map.get(node)
                    if parent is not None:
                        parent.remove(node)
                        deleted_count += 1

        # Category: meds_current
        if 'meds_current' in grouped:
            indices = sorted([int(x) for x in grouped['meds_current']], reverse=True)
            current_meds = [med for med in root.findall('.//CurrentRx/Medication') if get_text(med, 'RXSTATUS') in ['1', '2']]
            for idx in indices:
                if 0 <= idx < len(current_meds):
                    node = current_meds[idx]
                    parent = parent_map.get(node)
                    if parent is not None:
                        parent.remove(node)
                        deleted_count += 1

        # Category: meds_past
        if 'meds_past' in grouped:
            indices = sorted([int(x) for x in grouped['meds_past']], reverse=True)
            past_meds = [med for med in root.findall('.//CurrentRx/Medication') if get_text(med, 'RXSTATUS') not in ['1', '2']]
            for idx in indices:
                if 0 <= idx < len(past_meds):
                    node = past_meds[idx]
                    parent = parent_map.get(node)
                    if parent is not None:
                        parent.remove(node)
                        deleted_count += 1

        # Category: visits
        if 'visits' in grouped:
            indices = sorted([int(x) for x in grouped['visits']], reverse=True)
            visits = root.findall('.//Visit')
            for idx in indices:
                if 0 <= idx < len(visits):
                    node = visits[idx]
                    parent = parent_map.get(node)
                    if parent is not None:
                        parent.remove(node)
                        deleted_count += 1

        # Category: results
        if 'results' in grouped:
            indices = sorted([int(x) for x in grouped['results']], reverse=True)
            allowed_loinc = {
                '10334-1','10535-3','12841-3','13965-9','14334-7','14631-6','14635-7','14646-4','14647-2','14685-2',
                '14771-0','14804-9','14805-6','14920-3','14927-8','14928-6','14933-6','1742-6','1743-4','17856-6',
                '1920-8','1988-5','2064-4','2157-6','22748-8','2276-4','2324-2','24108-3','2532-0',
                '2601-3','2823-3','2857-1','2885-2','2890-2','29265-6','2951-2','3016-3','32294-1','32309-7',
                '33914-3','39469-2','43396-1','4537-7','4544-3','4548-4','50210-4','59261-8','61152-5','6690-2',
                '6768-6','70204-3','711-2','718-7','72160-5','731-0','742-7','751-8','777-3','787-2','789-8','9830-1'
            }
            results = [res for res in root.findall('.//Result') if get_text(res, 'RESULTNAME') and get_text(res, 'LOINCCODE') in allowed_loinc]
            for idx in indices:
                if 0 <= idx < len(results):
                    node = results[idx]
                    parent = parent_map.get(node)
                    if parent is not None:
                        parent.remove(node)
                        deleted_count += 1

        # Category: investigations
        if 'investigations' in grouped:
            indices = sorted([int(x) for x in grouped['investigations']], reverse=True)
            investigations = []
            for inv in root.findall('.//Investigation'):
                inv_id = get_text(inv, 'REPORTID')
                if not inv_id: continue
                if root.find(f".//Result[REPORTID='{inv_id}']") is None and inv.find('.//InvestigationPage') is None:
                    investigations.append(inv)
            for idx in indices:
                if 0 <= idx < len(investigations):
                    node = investigations[idx]
                    parent = parent_map.get(node)
                    if parent is not None:
                        parent.remove(node)
                        deleted_count += 1

        # Category: documents
        if 'documents' in grouped:
            for doc_id in grouped['documents']:
                node = root.find(f".//Document[DOCUMENTID='{doc_id}']") or root.find(f".//Investigation[REPORTID='{doc_id}']")
                if node is not None:
                    parent = parent_map.get(node)
                    if parent is not None:
                        parent.remove(node)
                        deleted_count += 1

        # Category: correspondence
        if 'correspondence' in grouped:
            for corr_id in grouped['correspondence']:
                node = root.find(f".//CorrespondenceOut/Correspondence[RECORDID='{corr_id}']")
                if node is not None:
                    parent = parent_map.get(node)
                    if parent is not None:
                        parent.remove(node)
                        deleted_count += 1

        # Save XML back to disk
        xml_str = ET.tostring(root, encoding='ISO-8859-1')
        
        # Write XML string with declaration
        with open(filepath, 'wb') as f:
            pass
            f.write(xml_str)

        flash(f"Successfully deleted {deleted_count} item(s) from the XML payload.", "success")
    except Exception as e:
        flash(f"Error modifying XML payload: {str(e)}", "danger")

    return redirect(url_for('view_patient', filename=filename))

# --- Utilities ---
@app.route('/patient/<filename>/download')
def download_edited_xml(filename):
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(filename))
    if not os.path.exists(filepath):
        return "File not found.", 404
    
    # Extract filename and construct new download name
    base_name = filename
    if base_name.lower().endswith('.xml'):
        base_name = base_name[:-4]
    download_name = f"{base_name}_edited.xml"
    
    return send_file(
        filepath,
        mimetype='application/xml',
        as_attachment=True,
        download_name=download_name
    )

def get_text(node, path):
    if node is None: return ''
    el = node.find(path)
    if el is not None and el.text is not None:
        return el.text.strip()
    return ''

def format_text_tables(text):
    """
    Detects lines with multiple '|' characters and converts consecutive such lines
    into an HTML table. Other lines are left as is.
    """
    if not text:
        return ""
        
    lines = text.split('\n')
    formatted_html = []
    in_table = False
    
    for line in lines:
        # Simple heuristic: if a line has at least 2 pipe characters, it might be a table row
        if line.count('|') >= 2:
            if not in_table:
                formatted_html.append('<table style="width:100%; border-collapse: collapse; margin-bottom: 15px; background: white; box-shadow: 0 1px 3px rgba(0,0,0,0.1);">')
                in_table = True
            
            # Split line by pipe, strip whitespace, and drop empty first/last elements if they exist
            cells = [cell.strip() for cell in line.split('|')]
            if not cells[0]: cells = cells[1:]
            if cells and not cells[-1]: cells = cells[:-1]
            
            formatted_html.append('<tr>')
            for cell in cells:
                # Use slightly different styling for potential header rows (e.g. bold first row)
                formatted_html.append(f'<td style="padding: 8px; border: 1px solid #dee2e6;">{cell}</td>')
            formatted_html.append('</tr>')
        else:
            if in_table:
                formatted_html.append('</table>')
                in_table = False
            # Ensure normal text respects newlines when rendered in HTML
            formatted_html.append(f"<div>{line}</div>" if line.strip() else "<br>")
            
    if in_table:
        formatted_html.append('</table>')
        
    return '\n'.join(formatted_html)

def normalize_date(date_str):
    if not date_str:
        return ""
    # Remove time part if present (e.g. "2016-06-21 00:00:00" -> "2016-06-21")
    date_str = date_str.split(' ')[0]
    # Handle YYYY-MM-DD
    match = re.match(r'^(\d{4})-(\d{2})-(\d{2})', date_str)
    if match:
        year, month, day = match.groups()
        return f"{day}/{month}/{year}"
    return date_str

def format_size(size_bytes):
    if size_bytes is None:
        return "N/A"
    try:
        size_bytes = int(size_bytes)
    except (ValueError, TypeError):
        return "N/A"
    if size_bytes >= 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    elif size_bytes >= 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes} B" 

# --- XML Extraction Logic ---

def parse_ehr_xml(filepath):
    with open(filepath, 'rb') as f:
        xml_data = f.read()
    
    # Strip UTF-8 BOM if present
    if xml_data.startswith(b'\xef\xbb\xbf'):
        xml_data = xml_data[3:]
        
    # Decode using ISO-8859-1 as specified by the XML header
    xml_text = xml_data.decode('ISO-8859-1')
    
    # Remove invalid XML control characters (ASCII 0-31 except tab, newline, carriage return)
    xml_text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', xml_text)

    root = ET.fromstring(xml_text)

    is_bps = (root.tag == 'BPSEHRV2')

    if is_bps:
        patient = root.find('.//Demographics/Patient')
        if patient is None:
            patient = root.find('.//Patient')

        address_parts = [
            get_text(patient, 'ADDRESS1'),
            get_text(patient, 'ADDRESS2'),
            get_text(patient, 'CITY'),
            get_text(patient, 'POSTCODE')
        ]
        full_address = ", ".join([part for part in address_parts if part and part.upper() != 'NIL'])

        demo = {
            'first_name': get_text(patient, 'FIRSTNAME'),
            'surname': get_text(patient, 'SURNAME'),
            'dob': get_text(patient, 'DOB'),
            'address': full_address,
            'mobile': get_text(patient, 'MOBILEPHONE'),
            'medicare': get_text(patient, 'MEDICARENO')
        }

        allergies = []
        for rxn in root.findall('.//Reactions/Reaction'):
            allergies.append({
                'item': get_text(rxn, 'ITEMNAME'),
                'reaction': get_text(rxn, 'REACTION')
            })

        history = []
        for cond in root.findall('.//PastHistory/Condition'):
            year = get_text(cond, 'YEAR')
            history.append({
                'condition': get_text(cond, 'ITEMTEXT'),
                'year': year if year != '0' else 'Unknown',
                'details': get_text(cond, 'DETAILS')
            })

        meds_current = []
        meds_past = []
        for med in root.findall('.//CurrentRx/Medication'):
            status = get_text(med, 'RXSTATUS')
            med_obj = {
                'drug': get_text(med, 'DRUGNAME'),
                'dose': get_text(med, 'DOSE'),
                'instructions': get_text(med, 'INSTRUCTIONS'),
                'start': get_text(med, 'FIRSTDATE'),
                'end': get_text(med, 'LASTDATE'),
                'del_reason': get_text(med, 'DELETIONREASON'),
                'del_date': get_text(med, 'DELETIONDATE'),
                'indication': get_text(med, 'INDICATION'),
                'type': 'Regular' if status == '1' else 'PRN/Occasional' if status == '2' else 'Ceased'
            }
            if status in ['1', '2']:
                meds_current.append(med_obj)
            else:
                meds_past.append(med_obj)

        results = []
        allowed_loinc = {
            '10334-1','10535-3','12841-3','13965-9','14334-7','14631-6','14635-7','14646-4','14647-2','14685-2',
            '14771-0','14804-9','14805-6','14920-3','14927-8','14928-6','14933-6','1742-6','1743-4','17856-6',
            '1920-8','1988-5','2064-4','2157-6','22748-8','2276-4','2324-2','24108-3','2532-0',
            '2601-3','2823-3','2857-1','2885-2','2890-2','29265-6','2951-2','3016-3','32294-1','32309-7',
            '33914-3','39469-2','43396-1','4537-7','4544-3','4548-4','50210-4','59261-8','61152-5','6690-2',
            '6768-6','70204-3','711-2','718-7','72160-5','731-0','742-7','751-8','777-3','787-2','789-8','9830-1'
        }
        for res in root.findall('.//Result'):
            name = get_text(res, 'RESULTNAME')
            if not name: continue
            
            loinc_code = get_text(res, 'LOINCCODE')
            if loinc_code not in allowed_loinc: continue

            results.append({
                'date': get_text(res, 'REPORTDATE'),
                'name': name,
                'value': get_text(res, 'RESULTVALUE'),
                'units': get_text(res, 'UNITS'),
                'range': get_text(res, 'RANGE'),
                'flag': get_text(res, 'ABNORMALFLAG').strip(),
                'loinc': loinc_code
            })

        visits = []
        for visit in root.findall('.//Visit'):
            rtf_notes = get_text(visit, 'VISITNOTES')
            try:
                plain_notes = rtf_to_text(rtf_notes) if rtf_notes else ''
            except:
                plain_notes = rtf_notes
                
            visits.append({
                'date': get_text(visit, 'VISITDATE'),
                'doctor': get_text(visit, 'DRNAME'),
                'notes': plain_notes
            })

        documents = []
        investigations = []
        for doc in root.findall('.//Document'):
            doc_id = get_text(doc, 'DOCUMENTID')
            if not doc_id: continue
            
            page = doc.find('.//DocumentPage')
            doc_type = get_text(page, 'DocType').strip() if page is not None else 'Unknown'
            file_name = get_text(page, 'FileName').strip() if page is not None else ''
            
            size_str = 'N/A'
            if page is not None:
                content_node = page.find('Content')
                if content_node is not None and content_node.text:
                    b64_len = len(content_node.text.strip())
                    approx_bytes = (b64_len * 3) // 4
                    size_str = format_size(approx_bytes)

            documents.append({
                'id': doc_id,
                'source': 'document',
                'date': get_text(doc, 'CORRESPONDENCEDATE'),
                'provider': get_text(doc, 'CONTACTNAME'),
                'category': get_text(doc, 'CATEGORY'),
                'subject': get_text(doc, 'SUBJECT'),
                'type': doc_type,
                'filename': file_name,
                'size': size_str
            })
            
        for inv in root.findall('.//Investigation'):
            inv_id = get_text(inv, 'REPORTID')
            if not inv_id: continue
            
            if root.find(f".//Result[REPORTID='{inv_id}']") is None:
                page = inv.find('.//InvestigationPage')
                if page is not None:
                    doc_type = get_text(page, 'DocType').strip()
                    file_name = get_text(page, 'FileName').strip()
                    
                    size_str = 'N/A'
                    content_node = page.find('Content')
                    if content_node is not None and content_node.text:
                        b64_len = len(content_node.text.strip())
                        approx_bytes = (b64_len * 3) // 4
                        size_str = format_size(approx_bytes)

                    documents.append({
                        'id': inv_id,
                        'source': 'investigation',
                        'date': get_text(inv, 'REPORTDATE'),
                        'provider': get_text(inv, 'PROVIDERNAME'),
                        'category': 'Report / Image',
                        'subject': get_text(inv, 'TESTNAME'),
                        'type': doc_type,
                        'filename': file_name,
                        'size': size_str
                    })
                else:
                    rtf_body = get_text(inv, 'REPORTBODY') or ''
                    size_str = format_size(len(rtf_body))
                    investigations.append({
                        'id': inv_id,
                        'source': 'investigation',
                        'date': get_text(inv, 'REPORTDATE'),
                        'provider': get_text(inv, 'PROVIDERNAME'),
                        'subject': get_text(inv, 'TESTNAME'),
                        'type': 'rtf',
                        'size': size_str
                    })

        correspondence = []
        for corr in root.findall('.//CorrespondenceOut/Correspondence'):
            corr_id = get_text(corr, 'RECORDID')
            if not corr_id: continue
            
            doc_type = get_text(corr, 'DOCTYPE').strip() if get_text(corr, 'DOCTYPE') else 'rtf'
            
            rtf_content = get_text(corr, 'CONTENT') or ''
            size_str = format_size(len(rtf_content))
            correspondence.append({
                'id': corr_id,
                'source': 'correspondence',
                'date': get_text(corr, 'CORRESPONDENCEDATE'),
                'provider': get_text(corr, 'CONTACTNAME'),
                'category': 'Outgoing Letter',
                'subject': get_text(corr, 'SUBJECT'),
                'type': doc_type,
                'filename': '',
                'size': size_str
            })

    else:
        patient = root.find('.//DEMOGRAPHICS')
        address_parts = [
            get_text(patient, 'ADDRESS'),
            get_text(patient, 'CITY'),
            get_text(patient, 'POSTCODE')
        ]
        full_address = ", ".join([part for part in address_parts if part and part.upper() != 'NIL'])

        demo = {
            'first_name': get_text(patient, 'FIRSTNAME'),
            'surname': get_text(patient, 'SURNAME'),
            'dob': normalize_date(get_text(patient, 'DOB')),
            'address': full_address,
            'mobile': get_text(patient, 'MOB_PHONE') or get_text(patient, 'PHONE'),
            'medicare': get_text(patient, 'MC_NO')
        }

        allergies = []
        allergies_text = get_text(root.find('.//ADDITIONAL_DEMOGRAPHICS'), 'ALLERGIES')
        if allergies_text:
            for line in allergies_text.split('\n'):
                if line.strip():
                    allergies.append({
                        'item': line.strip(),
                        'reaction': 'N/A'
                    })

        history = []
        for cond in root.findall('.//PMH/PMHITEM'):
            year = get_text(cond, 'YEAR')
            history.append({
                'condition': get_text(cond, 'CONDITION'),
                'year': year if year and year != '0' else 'Unknown',
                'details': get_text(cond, 'COMMENT')
            })

        drug_map = {}
        for script in root.findall('.//SCRIPTS/SCRIPTITEM'):
            drug_no = get_text(script, 'DRUG_NO')
            drug_name = get_text(script, 'P1')
            if drug_no and drug_name:
                drug_map[drug_no] = drug_name

        meds_current = []
        meds_past = []
        for med in root.findall('.//RX/RXITEM'):
            drug_no = get_text(med, 'DRUG_NO')
            drug_name = drug_map.get(drug_no, f"Drug #{drug_no}")
            dose = get_text(med, 'DOSE')
            frequency = get_text(med, 'FREQUENCY')
            special = get_text(med, 'SPECIAL')
            instructions = f"{dose} {frequency}" + (f" ({special})" if special else "")
            
            med_obj = {
                'drug': drug_name,
                'dose': dose,
                'instructions': instructions,
                'start': normalize_date(get_text(med, 'FIRSTDATE')),
                'end': normalize_date(get_text(med, 'LASTDATE')),
                'del_reason': '',
                'del_date': normalize_date(get_text(med, 'CEASED_DATE')),
                'indication': get_text(med, 'INDICATION') or get_text(med, 'REASON') or '',
                'type': 'PRN/Occasional' if 'PRN' in (frequency or '').upper() or 'PRN' in (special or '').upper() else 'Regular'
            }
            meds_current.append(med_obj)

        results = []
        allowed_loinc = {
            '10334-1','10535-3','12841-3','13965-9','14334-7','14631-6','14635-7','14646-4','14647-2','14685-2',
            '14771-0','14804-9','14805-6','14920-3','14927-8','14928-6','14933-6','1742-6','1743-4','17856-6',
            '1920-8','1988-5','2064-4','2157-6','22748-8','2276-4','2324-2','24108-3','2532-0',
            '2601-3','2823-3','2857-1','2885-2','2890-2','29265-6','2951-2','3016-3','32294-1','32309-7',
            '33914-3','39469-2','43396-1','4537-7','4544-3','4548-4','50210-4','59261-8','61152-5','6690-2',
            '6768-6','70204-3','711-2','718-7','72160-5','731-0','742-7','751-8','777-3','787-2','789-8','9830-1'
        }
        for res in root.findall('.//RESULT'):
            loinc_node = res.find('LOINC')
            if loinc_node is None: continue
            
            loinc_code = loinc_node.text.strip() if loinc_node.text else ''
            if loinc_code not in allowed_loinc: continue

            results.append({
                'date': normalize_date(get_text(res, 'RESULTDATE')),
                'name': get_text(res, 'TESTNAME'),
                'value': get_text(res, 'RESULT'),
                'units': get_text(res, 'UNITS'),
                'range': get_text(res, 'RANGE'),
                'flag': '',
                'loinc': loinc_code
            })

        visits = []
        for visit in root.findall('.//PROGRESS/VISIT'):
            rtf_notes = get_text(visit, 'NOTES')
            try:
                plain_notes = rtf_to_text(rtf_notes) if rtf_notes else ''
            except:
                plain_notes = rtf_notes
                
            visits.append({
                'date': normalize_date(get_text(visit, 'VISITDATE')),
                'doctor': get_text(visit, 'DR'),
                'notes': plain_notes
            })

        documents = []
        investigations = []
        for doc in root.findall('.//DOCUMENTS2/DOCUMENT'):
            doc_id = get_text(doc, 'DOC_NO')
            if not doc_id: continue
            
            doc_type = get_text(doc, 'DOCTYPE').strip()
            file_name = get_text(doc, 'FILENAME').strip()
            
            size_str = 'N/A'
            content_node = doc.find('BASE64_DATA')
            if content_node is not None and content_node.text:
                b64_len = len(content_node.text.strip())
                approx_bytes = (b64_len * 3) // 4
                size_str = format_size(approx_bytes)

            documents.append({
                'id': doc_id,
                'source': 'document',
                'date': normalize_date(get_text(doc, 'DOCDATE')),
                'provider': get_text(doc, 'DOCTORNAME') or get_text(doc, 'DOCDESC') or 'Unknown',
                'category': get_text(doc, 'DOCTYPE'),
                'subject': get_text(doc, 'DOCTITLE'),
                'type': doc_type,
                'filename': file_name,
                'size': size_str
            })

        correspondence = []

    # Sort
    history.sort(key=lambda x: int(x['year']) if x['year'].isdigit() else 0, reverse=True)
    results.sort(key=lambda x: (x['loinc'], [-int(part) for part in x['date'].split('/')[::-1]] if '/' in x['date'] else [-1]))
    visits.sort(key=lambda x: [-int(part) for part in x['date'].split('/')[::-1]] if '/' in x['date'] else [-1])
    documents.sort(key=lambda x: [-int(part) for part in x['date'].split('/')[::-1]] if '/' in x['date'] else [-1])
    investigations.sort(key=lambda x: [-int(part) for part in x['date'].split('/')[::-1]] if '/' in x['date'] else [-1])
    correspondence.sort(key=lambda x: [-int(part) for part in x['date'].split('/')[::-1]] if '/' in x['date'] else [-1])

    return {
        'demographics': demo,
        'allergies': allergies,
        'history': history,
        'medications_current': meds_current,
        'medications_past': meds_past,
        'results': results,
        'visits': visits,
        'documents': documents,
        'investigations': investigations,
        'correspondence': correspondence
    }


# HTML templates
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Project 5 - File Upload</title>
    <style>
        body { font-family: sans-serif; display: flex; flex-direction: column; align-items: center; justify-content: center; min-height: 100vh; margin: 0; background-color: #f4f4f9; padding: 20px; }
        .upload-container { background: white; padding: 2rem; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); width: 500px; text-align: center; margin-bottom: 20px; }
        #drop-zone { border: 2px dashed #007bff; border-radius: 8px; padding: 40px; cursor: pointer; transition: background 0.3s; margin-bottom: 20px; position: relative; }
        #drop-zone.hover { background: #e7f1ff; }
        #file-input { display: none; }
        #file-name { margin-top: 10px; font-weight: bold; color: #007bff; }
        .btn { background: #007bff; color: white; padding: 10px 20px; border: none; border-radius: 4px; cursor: pointer; font-size: 16px; margin-bottom: 10px; text-decoration: none; display: inline-block; }
        .btn:hover { background: #0056b3; }
        .btn-danger { background: #dc3545; }
        .btn-danger:hover { background: #a71d2a; }
        .browse-text { text-decoration: underline; color: #007bff; }
        .message { margin-top: 10px; color: #28a745; }
        .links { margin-top: 20px; border-top: 1px solid #ddd; padding-top: 20px; }
        .links a { color: #007bff; text-decoration: none; font-weight: bold; }
        .links a:hover { text-decoration: underline; }
        .success-box { margin-top: 20px; padding: 20px; background-color: #d4edda; border-radius: 8px; border: 1px solid #c3e6cb; }
        
        .file-list { background: white; padding: 1.5rem; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); width: 500px; text-align: left; }
        .file-list h3 { margin-top: 0; color: #2c3e50; border-bottom: 2px solid #f4f4f9; padding-bottom: 10px; }
        .file-item { display: flex; justify-content: space-between; align-items: center; padding: 10px 0; border-bottom: 1px solid #eee; }
        .file-item:last-child { border-bottom: none; }
        .file-info { flex-grow: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; margin-right: 10px; }
        .file-actions { display: flex; gap: 8px; }
        .btn-xs { padding: 4px 8px; font-size: 0.8em; }
    </style>
</head>
<body>
    <div class="upload-container">
        <h1>Upload EHR Document</h1>
        
        {% if uploaded_filename %}
            <div class="success-box">
                <h2 style="color: #155724; margin-top:0;">Upload Successful!</h2>
                <p>Parsed: <strong>{{ uploaded_filename }}</strong></p>
                <a href="{{ url_for('view_patient', filename=uploaded_filename) }}" class="btn" style="background-color: #28a745;">Open Dashboard</a>
                <br>
                <a href="/" style="font-size: 0.9em; color: #666;">Upload Another</a>
            </div>
        {% else %}
            <form id="upload-form" action="/" method="post" enctype="multipart/form-data">
                <div id="drop-zone">
                    <p>Drag & Drop XML here or <span class="browse-text">click to browse</span></p>
                    <div id="file-name"></div>
                    <input type="file" name="file" id="file-input">
                </div>
                <button type="submit" class="btn">Upload and Process File</button>
            </form>
            {% with messages = get_flashed_messages() %}
              {% if messages %}
                {% for message in messages %}
                  <p class="message">{{ message }}</p>
                {% endfor %}
              {% endif %}
            {% endwith %}
        {% endif %}
    </div>

    {% if existing_files %}
    <div class="file-list">
        <h3>Existing Patient Records</h3>
        {% for file in existing_files %}
        <div class="file-item">
            <div class="file-info" title="{{ file }}">
                <strong>{{ file }}</strong>
            </div>
            <div class="file-actions">
                <a href="{{ url_for('view_patient', filename=file) }}" class="btn btn-xs">Open</a>
                <a href="/patient/{{ file }}/download" class="btn btn-xs" style="background-color: #28a745;">Download</a>
                <form action="{{ url_for('delete_file', filename=file) }}" method="post" style="display:inline;" onsubmit="return confirm('Are you sure you want to delete this record?')">
                    <button type="submit" class="btn btn-danger btn-xs">Delete</button>
                </form>
            </div>
        </div>
        {% endfor %}
    </div>
    {% endif %}

    <div class="links">
        <a href="/proposal">View EHR Extraction Proposal</a>
    </div>

    <script>
        const dropZone = document.getElementById('drop-zone');
        const fileInput = document.getElementById('file-input');
        const fileNameDisplay = document.getElementById('file-name');
        
        if (dropZone) {
            dropZone.addEventListener('click', () => fileInput.click());

            fileInput.addEventListener('change', () => {
                if (fileInput.files.length > 0) {
                    fileNameDisplay.textContent = `Selected: ${fileInput.files[0].name}`;
                }
            });

            dropZone.addEventListener('dragover', (e) => {
                e.preventDefault();
                dropZone.classList.add('hover');
            });

            ['dragleave', 'dragend'].forEach(type => {
                dropZone.addEventListener(type, () => dropZone.classList.remove('hover'));
            });

            dropZone.addEventListener('drop', (e) => {
                e.preventDefault();
                dropZone.classList.remove('hover');
                if (e.dataTransfer.files.length) {
                    fileInput.files = e.dataTransfer.files;
                    fileNameDisplay.textContent = `Selected: ${fileInput.files[0].name}`;
                }
            });
        }
    </script>
</body>
</html>
"""

PATIENT_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Patient Dashboard - {{ data.demographics.first_name }} {{ data.demographics.surname }}</title>
    <style>
        body { font-family: sans-serif; margin: 0; display: flex; height: 100vh; background-color: #f4f4f9; }
        .sidebar { width: 250px; background: #2c3e50; color: white; padding: 20px; box-sizing: border-box; flex-shrink: 0; }
        .sidebar h2 { margin-top: 0; font-size: 1.2rem; border-bottom: 1px solid #455a64; padding-bottom: 10px; }
        .sidebar ul { list-style: none; padding: 0; }
        .sidebar li { margin-bottom: 10px; }
        .sidebar a { color: #ecf0f1; text-decoration: none; display: block; padding: 10px; border-radius: 4px; transition: background 0.2s; }
        .sidebar a:hover { background: #34495e; }
        .content { flex-grow: 1; overflow-y: auto; scroll-behavior: smooth; position: relative; }
        
        /* Global Controls */
        .global-controls { 
            display: flex; gap: 10px; align-items: center; 
            background: white; padding: 20px 30px; 
            border-bottom: 1px solid #ddd;
            position: sticky; top: 0; z-index: 100;
            box-shadow: 0 2px 4px rgba(0,0,0,0.05);
        }
        .global-controls h1 { margin: 0; flex-grow: 1; color: #2c3e50; font-size: 1.8rem; }
        .btn-sm { background: #e9ecef; color: #495057; border: 1px solid #ced4da; padding: 5px 10px; border-radius: 4px; cursor: pointer; font-size: 0.9em; transition: all 0.2s; text-decoration: none; display: inline-block; }
        .btn-sm:hover { background: #dee2e6; color: #212529; }

        .cards-container { padding: 30px; }

        /* Collapsible Cards */
        .card { background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); margin-bottom: 30px; transition: margin 0.3s ease; }
        .card-header { display: flex; align-items: center; cursor: pointer; user-select: none; border-bottom: 2px solid #e7f1ff; padding-bottom: 8px; margin-bottom: 15px; }
        .card-header h2 { color: #007bff; margin: 0; flex-grow: 1; border: none; padding: 0; }
        .toggle-icon { display: inline-block; width: 24px; height: 24px; line-height: 24px; text-align: center; border-radius: 50%; background-color: #f8f9fa; color: #6c757d; font-weight: bold; font-size: 1.2em; transition: transform 0.3s ease; }
        .card-header:hover .toggle-icon { background-color: #e9ecef; color: #007bff; }
        .card-content { transition: opacity 0.3s ease-in-out; opacity: 1; display: block; overflow-x: auto; }
        
        /* Collapsed State */
        .card.collapsed { padding-bottom: 10px; margin-bottom: 15px; }
        .card.collapsed .card-header { border-bottom: none; margin-bottom: 0; padding-bottom: 0; }
        .card.collapsed .card-content { display: none; opacity: 0; }
        .card.collapsed .toggle-icon { transform: rotate(-90deg); }

        table { width: 100%; border-collapse: collapse; margin-top: 10px; }
        th, td { padding: 12px; text-align: left; vertical-align: top; border-bottom: 1px solid #ddd; }
        th { background-color: #f8f9fa; color: #495057; }
        tr:hover { background-color: #f1f3f5; }
        .header-info { display: grid; grid-template-columns: 1fr 1fr; gap: 15px; }
        .header-info div { background: #e9ecef; padding: 12px 15px; border-radius: 6px; color: #495057; }
        .header-info .full-width { grid-column: span 2; }
    </style>
</head>
<body>
    <div class="sidebar">
        <h2>Sections</h2>
        <ul>
            <li><a href="/" style="font-weight: bold; border-bottom: 1px solid #34495e; margin-bottom: 10px; padding-bottom: 15px;">&#8962; Home</a></li>
            <li><a href="#demographics">Demographics</a></li>
            <li><a href="#allergies">Allergies</a></li>
            <li><a href="#history">Past Medical History</a></li>
            <li><a href="#medications">Current Medications</a></li>
            <li><a href="#medications-past">Past/Ceased Meds</a></li>
            <li><a href="#visits">Clinical Visits</a></li>
            <li><a href="#pathology">Pathology Results</a></li>
            <li><a href="#investigations">Non-Atomized Pathology</a></li>
            <li><a href="#correspondence">Correspondence Out</a></li>
            <li><a href="#documents">Documents</a></li>
        </ul>
    </div>
    
    <div class="content">
        <div class="global-controls">
            <h1>Patient Dashboard: {{ data.demographics.first_name }} {{ data.demographics.surname }}</h1>
            <a href="/patient/{{ filename }}/download" class="btn-sm" style="background-color: #28a745; color: white; border: none; font-weight: bold; margin-right: 5px; text-decoration: none;">Download Edited XML</a>
            <button class="btn-sm" onclick="expandAll()">Expand All</button>
            <button class="btn-sm" onclick="collapseAll()">Collapse All</button>
            <button type="submit" form="delete-form" class="btn-sm" style="background-color: #dc3545; color: white; border: none; font-weight: bold; margin-left: 10px; cursor: pointer; padding: 6px 12px;">Delete Selected</button>
        </div>

        {% with messages = get_flashed_messages(with_categories=true) %}
          {% if messages %}
            <div style="padding: 10px 30px;">
              {% for category, message in messages %}
                <div style="padding: 15px; margin-bottom: 15px; border-radius: 4px; font-weight: bold;
                  {% if category == 'success' %}background-color: #d4edda; color: #155724; border: 1px solid #c3e6cb;
                  {% elif category == 'danger' %}background-color: #f8d7da; color: #721c24; border: 1px solid #f5c6cb;
                  {% else %}background-color: #fff3cd; color: #856404; border: 1px solid #ffeeba;{% endif %}">
                  {{ message }}
                </div>
              {% endfor %}
            </div>
          {% endif %}
        {% endwith %}
        
        <form id="delete-form" action="/patient/{{ filename }}/delete" method="POST">
        <div class="cards-container">
            <div id="demographics" class="card collapsed">
                <div class="card-header" onclick="toggleCard(this)">
                    <h2>Demographics</h2>
                    <span class="toggle-icon">&#x25BE;</span>
                </div>
                <div class="card-content">
                    <div class="header-info">
                        <div><strong>Name:</strong> {{ data.demographics.first_name }} {{ data.demographics.surname }}</div>
                        <div><strong>DOB:</strong> {{ data.demographics.dob }}</div>
                        <div><strong>Medicare No:</strong> {{ data.demographics.medicare }}</div>
                        <div><strong>Mobile:</strong> {{ data.demographics.mobile }}</div>
                        <div class="full-width"><strong>Address:</strong> {{ data.demographics.address }}</div>
                    </div>
                </div>
            </div>

            <div id="allergies" class="card collapsed">
                <div class="card-header" onclick="toggleCard(this)">
                    <h2>Allergies & Reactions ({{ data.allergies|length if data.allergies else 0 }})</h2>
                    <input type="checkbox" onclick="event.stopPropagation(); toggleSectionSelect(this, 'allergies-table')" style="margin-right: 5px; transform: scale(1.2);">
                    <span style="font-size: 0.85rem; color: #6c757d; margin-right: 15px;" onclick="event.stopPropagation();">Select All</span>
                    <span class="toggle-icon">&#x25BE;</span>
                </div>
                <div class="card-content">
                    {% if data.allergies %}
                    <table id="allergies-table">
                        <tr><th style="width: 40px; text-align: center;">Select</th><th>Substance/Item</th><th>Reaction</th></tr>
                        {% for item in data.allergies %}
                        <tr>
                            <td style="text-align: center;"><input type="checkbox" name="selected_items" value="allergies:{{ loop.index0 }}"></td>
                            <td style="font-weight: bold; color: #d32f2f;">{{ item.item }}</td>
                            <td>{{ item.reaction }}</td>
                        </tr>
                        {% endfor %}
                    </table>
                    {% else %}
                    <p>Allergies & Reactions not present in the uploaded data.</p>
                    {% endif %}
                </div>
            </div>

            <div id="history" class="card collapsed">
                <div class="card-header" onclick="toggleCard(this)">
                    <h2>Past Medical History ({{ data.history|length if data.history else 0 }})</h2>
                    <input type="checkbox" onclick="event.stopPropagation(); toggleSectionSelect(this, 'history-table')" style="margin-right: 5px; transform: scale(1.2);">
                    <span style="font-size: 0.85rem; color: #6c757d; margin-right: 15px;" onclick="event.stopPropagation();">Select All</span>
                    <span class="toggle-icon">&#x25BE;</span>
                </div>
                <div class="card-content">
                    {% if data.history %}
                    <table id="history-table">
                        <tr><th style="width: 40px; text-align: center;">Select</th><th style="width: 80px;">Year</th><th>Condition</th><th>Details</th></tr>
                        {% for item in data.history %}
                        <tr>
                            <td style="text-align: center;"><input type="checkbox" name="selected_items" value="history:{{ loop.index0 }}"></td>
                            <td>{{ item.year }}</td>
                            <td style="font-weight: bold;">{{ item.condition }}</td>
                            <td style="color: #6c757d;">{{ item.details if item.details and item.details != 'NIL' else '' }}</td>
                        </tr>
                        {% endfor %}
                    </table>
                    {% else %}
                    <p>Past Medical History not present in the uploaded data.</p>
                    {% endif %}
                </div>
            </div>

            <div id="medications" class="card collapsed">
                <div class="card-header" onclick="toggleCard(this)">
                    <h2>Current Medications ({{ data.medications_current|length if data.medications_current else 0 }})</h2>
                    <input type="checkbox" onclick="event.stopPropagation(); toggleSectionSelect(this, 'medications-table')" style="margin-right: 5px; transform: scale(1.2);">
                    <span style="font-size: 0.85rem; color: #6c757d; margin-right: 15px;" onclick="event.stopPropagation();">Select All</span>
                    <span class="toggle-icon">&#x25BE;</span>
                </div>
                <div class="card-content">
                    {% if data.medications_current %}
                    <table id="medications-table">
                        <tr><th style="width: 40px; text-align: center;">Select</th><th>Drug Name</th><th>Type</th><th>Dose</th><th>Instructions</th><th>Start Date</th><th>Last Prescribed</th></tr>
                        {% for item in data.medications_current %}
                        <tr>
                            <td style="text-align: center;"><input type="checkbox" name="selected_items" value="meds_current:{{ loop.index0 }}"></td>
                            <td style="font-weight: bold; color: #1976d2;">{{ item.drug }}</td>
                            <td>{{ item.type }}</td>
                            <td>{{ item.dose }}</td>
                            <td>{{ item.instructions }}</td>
                            <td>{{ item.start }}</td>
                            <td>{{ item.end }}</td>
                        </tr>
                        {% endfor %}
                    </table>
                    {% else %}
                    <p>Current Medications not present in the uploaded data.</p>
                    {% endif %}
                </div>
            </div>

            <div id="medications-past" class="card collapsed">
                <div class="card-header" onclick="toggleCard(this)">
                    <h2>Past / Ceased Medications ({{ data.medications_past|length if data.medications_past else 0 }})</h2>
                    <input type="checkbox" onclick="event.stopPropagation(); toggleSectionSelect(this, 'medications-past-table')" style="margin-right: 5px; transform: scale(1.2);">
                    <span style="font-size: 0.85rem; color: #6c757d; margin-right: 15px;" onclick="event.stopPropagation();">Select All</span>
                    <span class="toggle-icon">&#x25BE;</span>
                </div>
                <div class="card-content">
                    {% if data.medications_past %}
                    <table id="medications-past-table">
                        <tr><th style="width: 40px; text-align: center;">Select</th><th>Drug Name</th><th>Instructions</th><th>Indication / Reason</th><th>Start Date</th><th>Date Stopped</th></tr>
                        {% for item in data.medications_past %}
                        <tr>
                            <td style="text-align: center;"><input type="checkbox" name="selected_items" value="meds_past:{{ loop.index0 }}"></td>
                            <td style="font-weight: bold; color: #6c757d;">{{ item.drug }}</td>
                            <td style="color: #6c757d;">{{ item.instructions }}</td>
                            <td style="color: #6c757d; font-style: italic;">
                                {{ item.indication if item.indication and item.indication != 'NIL' else '' }}
                                {% if item.del_reason and item.del_reason != 'NIL' %}
                                    (Ceased: {{ item.del_reason }})
                                {% endif %}
                            </td>
                            <td style="color: #6c757d;">{{ item.start }}</td>
                            <td style="color: #6c757d;">{{ item.del_date if item.del_date and item.del_date != 'NIL' else item.end }}</td>
                        </tr>
                        {% endfor %}
                    </table>
                    {% else %}
                    <p>Past Medications not present in the uploaded data.</p>
                    {% endif %}
                </div>
            </div>

            <div id="visits" class="card collapsed">
                <div class="card-header" onclick="toggleCard(this)">
                    <h2>Clinical Visits ({{ data.visits|length if data.visits else 0 }})</h2>
                    <input type="checkbox" onclick="event.stopPropagation(); toggleSectionSelect(this, 'visits-table')" style="margin-right: 5px; transform: scale(1.2);">
                    <span style="font-size: 0.85rem; color: #6c757d; margin-right: 15px;" onclick="event.stopPropagation();">Select All</span>
                    <span class="toggle-icon">&#x25BE;</span>
                </div>
                <div class="card-content">
                    {% if data.visits %}
                    <table id="visits-table">
                        <tr><th style="width: 40px; text-align: center;">Select</th><th style="width: 100px;">Date</th><th style="width: 200px;">Doctor</th><th>Notes</th></tr>
                        {% for item in data.visits %}
                        <tr>
                            <td style="text-align: center;"><input type="checkbox" name="selected_items" value="visits:{{ loop.index0 }}"></td>
                            <td>{{ item.date }}</td>
                            <td style="font-weight: bold;">{{ item.doctor }}</td>
                            <td style="white-space: pre-wrap; font-family: monospace; font-size: 0.9em; color: #495057;">{{ item.notes }}</td>
                        </tr>
                        {% endfor %}
                    </table>
                    {% else %}
                    <p>Clinical Visits not present in the uploaded data.</p>
                    {% endif %}
                </div>
            </div>

            <div id="pathology" class="card collapsed">
                <div class="card-header" onclick="toggleCard(this)" style="display: flex; align-items: center;">
                    <h2 style="flex-grow: 0; margin-right: 15px;">Pathology Results (Atomized) ({{ data.results|length if data.results else 0 }})</h2>
                    <a href="{{ url_for('view_charts', filename=filename) }}" class="btn-sm" style="background-color: #17a2b8; color: white; border: none; text-decoration: none; margin-right: 15px;" onclick="event.stopPropagation();">
                        View Clinical Trends &rarr;
                    </a>
                    <div style="flex-grow: 1;">
                        <input type="checkbox" onclick="event.stopPropagation(); toggleSectionSelect(this, 'pathology-table')" style="margin-right: 5px; transform: scale(1.2);">
                        <span style="font-size: 0.85rem; color: #6c757d; margin-right: 15px;" onclick="event.stopPropagation();">Select All</span>
                    </div>
                    <span class="toggle-icon">&#x25BE;</span>
                </div>
                <div class="card-content">
                    {% if data.results %}
                    <table id="pathology-table">
                        <tr><th style="width: 40px; text-align: center;">Select</th><th style="width: 100px;">Date</th><th style="width: 90px;">LOINC</th><th>Test Name</th><th>Value</th><th>Units</th><th>Reference Range</th><th style="text-align: center;">Flag</th></tr>
                        {% for item in data.results %}
                        <tr style="{% if item.flag %}background-color: #fff3cd;{% endif %}">
                            <td style="text-align: center;"><input type="checkbox" name="selected_items" value="results:{{ loop.index0 }}"></td>
                            <td>{{ item.date }}</td>
                            <td style="color: #6c757d; font-size: 0.9em;">{{ item.loinc }}</td>
                            <td style="font-weight: bold;">{{ item.name }}</td>
                            <td>{{ item.value }}</td>
                            <td style="color: #6c757d;">{{ item.units }}</td>
                            <td style="color: #6c757d; font-size: 0.9em;">{{ item.range }}</td>
                            <td style="text-align: center; font-weight: bold; color: #d32f2f;">{{ item.flag }}</td>
                        </tr>
                        {% endfor %}
                    </table>
                    {% else %}
                    <p>Atomized Pathology Results not present in the uploaded data.</p>
                    {% endif %}
                </div>
            </div>

            <div id="investigations" class="card collapsed">
                <div class="card-header" onclick="toggleCard(this)">
                    <h2>Non-Atomized Pathology / Clinical Reports ({{ data.investigations|length if data.investigations else 0 }})</h2>
                    <input type="checkbox" onclick="event.stopPropagation(); toggleSectionSelect(this, 'investigations-table')" style="margin-right: 5px; transform: scale(1.2);">
                    <span style="font-size: 0.85rem; color: #6c757d; margin-right: 15px;" onclick="event.stopPropagation();">Select All</span>
                    <span class="toggle-icon">&#x25BE;</span>
                </div>
                <div class="card-content">
                    {% if data.investigations %}
                    <table id="investigations-table">
                        <tr><th style="width: 40px; text-align: center;">Select</th><th style="width: 100px;">Date</th><th>Provider</th><th>Test Name</th><th style="text-align: center;">Action</th></tr>
                        {% for item in data.investigations %}
                        <tr>
                            <td style="text-align: center;"><input type="checkbox" name="selected_items" value="investigations:{{ loop.index0 }}"></td>
                            <td>{{ item.date }}</td>
                            <td style="font-weight: bold;">{{ item.provider if item.provider and item.provider != 'NIL' else 'Unknown' }}</td>
                            <td style="color: #495057;">{{ item.subject if item.subject and item.subject != 'NIL' else '' }}</td>
                            <td style="text-align: center;">
                                <a href="/patient/{{ filename }}/viewer/{{ item.source }}/{{ item.id }}" target="_blank" class="btn-sm" style="text-decoration: none;">View Report</a>
                            </td>
                        </tr>
                        {% endfor %}
                    </table>
                    {% else %}
                    <p>Clinical Reports not present in the uploaded data.</p>
                    {% endif %}
                </div>
            </div>

            <div id="correspondence" class="card collapsed">
                <div class="card-header" onclick="toggleCard(this)">
                    <h2>Outward Correspondence (Referrals/Letters) ({{ data.correspondence|length if data.correspondence else 0 }})</h2>
                    <input type="checkbox" onclick="event.stopPropagation(); toggleSectionSelect(this, 'correspondence-table')" style="margin-right: 5px; transform: scale(1.2);">
                    <span style="font-size: 0.85rem; color: #6c757d; margin-right: 15px;" onclick="event.stopPropagation();">Select All</span>
                    <span class="toggle-icon">&#x25BE;</span>
                </div>
                <div class="card-content">
                    {% if data.correspondence %}
                    <table id="correspondence-table">
                        <tr><th style="width: 40px; text-align: center;">Select</th><th style="width: 100px;">Date</th><th>To (Provider)</th><th>Subject</th><th style="text-align: center;">Action</th></tr>
                        {% for item in data.correspondence %}
                        <tr>
                            <td style="text-align: center;"><input type="checkbox" name="selected_items" value="correspondence:{{ item.id }}"></td>
                            <td>{{ item.date }}</td>
                            <td style="font-weight: bold;">{{ item.provider if item.provider and item.provider != 'NIL' else 'Unknown' }}</td>
                            <td style="color: #495057;">{{ item.subject if item.subject and item.subject != 'NIL' else '' }}</td>
                            <td style="text-align: center;">
                                <a href="/patient/{{ filename }}/viewer/{{ item.source }}/{{ item.id }}" target="_blank" class="btn-sm" style="text-decoration: none;">View Letter</a>
                            </td>
                        </tr>
                        {% endfor %}
                    </table>
                    {% else %}
                    <p>Outward Correspondence not present in the uploaded data.</p>
                    {% endif %}
                </div>
            </div>

            <div id="documents" class="card collapsed">
                <div class="card-header" onclick="toggleCard(this)">
                    <h2>Documents ({{ data.documents|length if data.documents else 0 }})</h2>
                    <input type="checkbox" onclick="event.stopPropagation(); toggleSectionSelect(this, 'documents-table')" style="margin-right: 5px; transform: scale(1.2);">
                    <span style="font-size: 0.85rem; color: #6c757d; margin-right: 15px;" onclick="event.stopPropagation();">Select All</span>
                    <span class="toggle-icon">&#x25BE;</span>
                </div>
                <div class="card-content">
                    {% if data.documents %}
                    <table id="documents-table">
                        <tr><th style="width: 40px; text-align: center;">Select</th><th style="width: 100px;">Date</th><th>Provider</th><th>Category</th><th>Subject</th><th>Type</th><th>Size</th><th style="text-align: center;">Action</th></tr>
                        {% for item in data.documents %}
                        <tr>
                            <td style="text-align: center;"><input type="checkbox" name="selected_items" value="documents:{{ item.id }}"></td>
                            <td>{{ item.date }}</td>
                            <td style="font-weight: bold;">{{ item.provider if item.provider and item.provider != 'NIL' else 'Unknown' }}</td>
                            <td>{{ item.category }}</td>
                            <td style="color: #495057;">{{ item.subject if item.subject and item.subject != 'NIL' else '' }}</td>
                            <td style="color: #6c757d; font-size: 0.9em; text-transform: uppercase;">{{ item.type }}</td>
                            <td style="color: #6c757d; font-size: 0.9em;">{{ item.size if item.size else 'N/A' }}</td>
                            <td style="text-align: center; white-space: nowrap;">
                                <a href="/patient/{{ filename }}/viewer/{{ item.source }}/{{ item.id }}" target="_blank" class="btn-sm" style="text-decoration: none;">View File</a>
                            </td>
                        </tr>
                        {% endfor %}
                    </table>
                    {% else %}
                    <p>Documents not present in the uploaded data.</p>
                    {% endif %}
                </div>
            </div>
        </div>
        </form>
    </div>
    
    <script>
        function toggleCard(headerElement) {
            const card = headerElement.parentElement;
            card.classList.toggle('collapsed');
        }

        function expandAll() {
            document.querySelectorAll('.card').forEach(card => card.classList.remove('collapsed'));
        }

        function collapseAll() {
            document.querySelectorAll('.card').forEach(card => card.classList.add('collapsed'));
        }

        function toggleSectionSelect(headerCheckbox, sectionId) {
            const section = document.getElementById(sectionId);
            if (!section) return;
            const checkboxes = section.querySelectorAll('input[type="checkbox"]');
            checkboxes.forEach(cb => {
                cb.checked = headerCheckbox.checked;
            });
        }

        // When clicking a sidebar link, expand that section if it's collapsed
        document.querySelectorAll('.sidebar a[href^="#"]').forEach(link => {
            link.addEventListener('click', (e) => {
                const targetId = link.getAttribute('href').substring(1);
                const targetCard = document.getElementById(targetId);
                if (targetCard && targetCard.classList.contains('collapsed')) {
                    targetCard.classList.remove('collapsed');
                }
            });
        });
    </script>
</body>
</html>
"""

VIEWER_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Document Viewer - {{ data.demographics.first_name }} {{ data.demographics.surname }}</title>
    <style>
        body { font-family: sans-serif; margin: 0; display: flex; flex-direction: column; height: 100vh; overflow: hidden; background: #f8f9fa; }
        .top-bar { 
            height: 60px; background: #2c3e50; color: white; display: flex; align-items: center; 
            padding: 0 20px; box-shadow: 0 2px 5px rgba(0,0,0,0.2); z-index: 10;
        }
        .top-bar h1 { margin: 0; font-size: 1.4rem; flex-grow: 1; }
        .patient-meta { font-size: 1.1rem; opacity: 0.9; }
        
        .main-container { display: flex; flex-grow: 1; overflow: hidden; }
        
        .content-pane { width: 80%; background: #eee; border-right: 1px solid #ccc; position: relative; }
        .content-pane iframe { width: 100%; height: 100%; border: none; background: white; }
        
        .sidebar-pane { width: 20%; background: white; overflow-y: auto; display: flex; flex-direction: column; }
        .sidebar-header { padding: 15px; background: #f1f3f5; border-bottom: 1px solid #dee2e6; font-weight: bold; color: #495057; }
        
        .doc-item { 
            padding: 12px 15px; border-bottom: 1px solid #eee; cursor: pointer; text-decoration: none; 
            color: #333; display: block; transition: background 0.2s;
        }
        .doc-item:hover { background: #f8f9fa; }
        .doc-item.active { background: #e7f1ff; border-left: 4px solid #007bff; }
        .doc-date { font-size: 0.8rem; color: #6c757d; display: block; margin-bottom: 3px; }
        .doc-subject { font-weight: bold; font-size: 0.9rem; display: block; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .doc-provider { font-size: 0.8rem; color: #495057; display: block; }
    </style>
</head>
<body>
    <div class="top-bar">
        <h1>Documents</h1>
        <div class="patient-meta">
            <strong>Patient:</strong> {{ data.demographics.first_name }} {{ data.demographics.surname }} 
            &nbsp; | &nbsp; <strong>DOB:</strong> {{ data.demographics.dob }}
        </div>
        <a href="{{ url_for('view_patient', filename=filename) }}" style="margin-left: 30px; color: #3498db; text-decoration: none; font-size: 0.9rem;">&larr; Back to Dashboard</a>
    </div>
    
    <div class="main-container">
        <div class="content-pane">
            <iframe src="/document/{{ filename }}/{{ active_source }}/{{ active_id }}"></iframe>
        </div>
        
        <div class="sidebar-pane">
            {# Determine which section to show by checking where the active_id resides #}
            {% set ns = namespace(section='document') %}
            {% for d in data.correspondence %}{% if d.id == active_id %}{% set ns.section = 'correspondence' %}{% endif %}{% endfor %}
            {% for d in data.investigations %}{% if d.id == active_id %}{% set ns.section = 'investigation' %}{% endif %}{% endfor %}
            
            <form id="delete-viewer-form" action="/patient/{{ filename }}/delete" method="POST" style="display: flex; flex-direction: column; height: 100%; margin: 0;">
                
                <!-- Selection & Deletion Header Toolbar -->
                <div style="padding: 12px 15px; border-bottom: 1px solid #dee2e6; background: #f8f9fa; display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-shrink: 0;">
                    <div style="display: flex; align-items: center; gap: 5px;">
                        <input type="checkbox" id="select-all-viewer" onclick="toggleViewerSelect(this)" style="transform: scale(1.1); cursor: pointer;">
                        <label for="select-all-viewer" style="font-size: 0.85rem; color: #495057; font-weight: bold; cursor: pointer; user-select: none;">Select All</label>
                    </div>
                    <button type="submit" class="btn-sm" style="background-color: #dc3545; color: white; border: none; font-weight: bold; cursor: pointer; padding: 4px 8px; font-size: 0.8em; border-radius: 4px;">Delete Selected</button>
                </div>
                
                <!-- Document List Container -->
                <div id="viewer-doc-list" style="overflow-y: auto; flex-grow: 1;">
                    {% if ns.section == 'correspondence' %}
                    <div class="sidebar-header">Outward Correspondence ({{ data.correspondence|length }})</div>
                    {% for doc in data.correspondence %}
                    <a href="/patient/{{ filename }}/viewer/{{ doc.source }}/{{ doc.id }}" 
                       class="doc-item {% if doc.id == active_id %}active{% endif %}"
                       style="display: flex; align-items: flex-start; gap: 10px;">
                        <input type="checkbox" name="selected_items" value="correspondence:{{ doc.id }}" 
                               onclick="event.stopPropagation();" 
                               style="transform: scale(1.1); margin-top: 3px; cursor: pointer; flex-shrink: 0;">
                        <div style="flex-grow: 1; min-width: 0;">
                            <span class="doc-date">{{ doc.date }}{% if doc.size %} &nbsp;|&nbsp; {{ doc.size }}{% endif %}</span>
                            <span class="doc-subject" title="{{ doc.subject }}">{{ doc.subject }}</span>
                            <span class="doc-provider">To: {{ doc.provider if doc.provider and doc.provider != 'NIL' else 'Unknown' }}</span>
                        </div>
                    </a>
                    {% endfor %}
                    {% endif %}
                    
                    {% if ns.section == 'investigation' %}
                    <div class="sidebar-header">Pathology & Reports ({{ data.investigations|length }})</div>
                    {% for doc in data.investigations %}
                    <a href="/patient/{{ filename }}/viewer/{{ doc.source }}/{{ doc.id }}" 
                       class="doc-item {% if doc.id == active_id %}active{% endif %}"
                       style="display: flex; align-items: flex-start; gap: 10px;">
                        <input type="checkbox" name="selected_items" value="investigations:{{ loop.index0 }}" 
                               onclick="event.stopPropagation();" 
                               style="transform: scale(1.1); margin-top: 3px; cursor: pointer; flex-shrink: 0;">
                        <div style="flex-grow: 1; min-width: 0;">
                            <span class="doc-date">{{ doc.date }}{% if doc.size %} &nbsp;|&nbsp; {{ doc.size }}{% endif %}</span>
                            <span class="doc-subject" title="{{ doc.subject }}">{{ doc.subject }}</span>
                            <span class="doc-provider">{{ doc.provider if doc.provider and doc.provider != 'NIL' else 'Unknown' }}</span>
                        </div>
                    </a>
                    {% endfor %}
                    {% endif %}
                    
                    {% if ns.section == 'document' %}
                    <div class="sidebar-header">Documents & Images ({{ data.documents|length }})</div>
                    {% for doc in data.documents %}
                    <a href="/patient/{{ filename }}/viewer/{{ doc.source }}/{{ doc.id }}" 
                       class="doc-item {% if doc.id == active_id %}active{% endif %}"
                       style="display: flex; align-items: flex-start; gap: 10px;">
                        <input type="checkbox" name="selected_items" value="documents:{{ doc.id }}" 
                               onclick="event.stopPropagation();" 
                               style="transform: scale(1.1); margin-top: 3px; cursor: pointer; flex-shrink: 0;">
                        <div style="flex-grow: 1; min-width: 0;">
                            <span class="doc-date">{{ doc.date }}{% if doc.size %} &nbsp;|&nbsp; {{ doc.size }}{% endif %}</span>
                            <span class="doc-subject" title="{{ doc.subject }}">{{ doc.subject if doc.subject and doc.subject != 'NIL' else doc.category }}</span>
                            <span class="doc-provider">{{ doc.provider if doc.provider and doc.provider != 'NIL' else 'Unknown' }}</span>
                        </div>
                    </a>
                    {% endfor %}
                    {% endif %}
                </div>
            </form>
        </div>
    </div>
    
    <script>
        function toggleViewerSelect(headerCheckbox) {
            const listContainer = document.getElementById('viewer-doc-list');
            if (!listContainer) return;
            const checkboxes = listContainer.querySelectorAll('input[type="checkbox"]');
            checkboxes.forEach(cb => {
                cb.checked = headerCheckbox.checked;
            });
        }

        document.addEventListener("DOMContentLoaded", function() {
            const sidebar = document.getElementById('viewer-doc-list');
            const filename = "{{ filename }}";
            const scrollKey = 'sidebar-scroll-' + filename;
            const checkedKey = 'checked-docs-' + filename;
            const selectAllCheckbox = document.getElementById('select-all-viewer');
            const deleteForm = document.getElementById('delete-viewer-form');

            // 1. Restore scroll position
            if (sidebar) {
                const savedScroll = sessionStorage.getItem(scrollKey);
                if (savedScroll) {
                    sidebar.scrollTop = parseInt(savedScroll, 10);
                }
                sidebar.addEventListener('scroll', function() {
                    sessionStorage.setItem(scrollKey, sidebar.scrollTop);
                });
            }

            // 2. Function to save checked state
            function saveCheckedState() {
                const checkedValues = [];
                const checkboxes = document.querySelectorAll('#viewer-doc-list input[name="selected_items"]');
                checkboxes.forEach(cb => {
                    if (cb.checked) {
                        checkedValues.push(cb.value);
                    }
                });
                sessionStorage.setItem(checkedKey, JSON.stringify(checkedValues));
                
                // Update Select All checkbox state dynamically
                if (selectAllCheckbox && checkboxes.length > 0) {
                    const checkedCount = checkedValues.length;
                    selectAllCheckbox.checked = (checkedCount === checkboxes.length);
                    selectAllCheckbox.indeterminate = (checkedCount > 0 && checkedCount < checkboxes.length);
                }
            }

            // 3. Function to restore checked state
            function restoreCheckedState() {
                const saved = sessionStorage.getItem(checkedKey);
                const checkboxes = document.querySelectorAll('#viewer-doc-list input[name="selected_items"]');
                if (saved && checkboxes.length > 0) {
                    const checkedValues = JSON.parse(saved);
                    checkboxes.forEach(cb => {
                        if (checkedValues.includes(cb.value)) {
                            cb.checked = true;
                        }
                    });
                }
                
                // Initial update of Select All checkbox
                if (selectAllCheckbox && checkboxes.length > 0) {
                    const checkedCount = document.querySelectorAll('#viewer-doc-list input[name="selected_items"]:checked').length;
                    selectAllCheckbox.checked = (checkedCount === checkboxes.length);
                    selectAllCheckbox.indeterminate = (checkedCount > 0 && checkedCount < checkboxes.length);
                }
            }

            // 4. Register event listeners for checkboxes
            const listCheckboxes = document.querySelectorAll('#viewer-doc-list input[name="selected_items"]');
            listCheckboxes.forEach(cb => {
                cb.addEventListener('change', saveCheckedState);
            });

            if (selectAllCheckbox) {
                selectAllCheckbox.addEventListener('change', saveCheckedState);
            }

            // 5. Clear stored checked states upon form submission (deletion)
            if (deleteForm) {
                deleteForm.addEventListener('submit', function() {
                    sessionStorage.removeItem(checkedKey);
                });
            }

            // 6. Run restore
            restoreCheckedState();
        });
    </script>
</body>
</html>
"""

CHARTS_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Clinical Trends - {{ demo.name }}</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        body { font-family: sans-serif; margin: 0; background: #f4f4f9; color: #333; }
        .header { background: #2c3e50; color: white; padding: 20px; box-shadow: 0 2px 5px rgba(0,0,0,0.1); position: sticky; top: 0; z-index: 100; display: flex; align-items: center; }
        .header h1 { margin: 0; font-size: 1.4rem; flex-grow: 1; }
        .back-link { color: #3498db; text-decoration: none; margin-left: 20px; }
        
        .container { max-width: 1200px; margin: 30px auto; padding: 0 20px; }
        .chart-card { background: white; border-radius: 12px; box-shadow: 0 4px 6px rgba(0,0,0,0.05); padding: 25px; margin-bottom: 40px; border: 1px solid #e0e0e0; }
        .chart-header { display: flex; justify-content: space-between; align-items: flex-end; margin-bottom: 15px; border-bottom: 2px solid #f0f0f0; padding-bottom: 10px; }
        .chart-title { margin: 0; color: #007bff; font-size: 1.3rem; }
        .chart-meta { font-size: 0.9rem; color: #6c757d; text-align: right; }
        
        canvas { width: 100% !important; height: 350px !important; }
        
        @media print {
            .header { display: none !important; }
            body { background: white; color: black; padding: 0; }
            .container { max-width: 100%; margin: 0; padding: 0; }
            .chart-card { 
                page-break-inside: avoid !important; 
                break-inside: avoid !important; 
                box-shadow: none !important;
                border: 1px solid #ddd !important;
                margin-bottom: 30px !important;
                padding: 15px !important;
            }
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>Clinical Trends: {{ demo.name }}</h1>
        <div style="font-size: 1.1rem; opacity: 0.9;"><strong>DOB:</strong> {{ demo.dob }}</div>
        <a href="/patient/{{ filename }}" class="back-link">&larr; Back to Dashboard</a>
    </div>

    <div class="container">
        {% for item in data %}
        <div class="chart-card">
            <div class="chart-header">
                <h2 class="chart-title">{{ item.group.name }}</h2>
                <div class="chart-meta">
                    <strong>LOINC:</strong> {{ item.code }} | 
                    <strong>Units:</strong> {{ item.group.units }}<br>
                    <strong>Normal Range:</strong> 
                    {% if item.group.final_range[0] is not none and item.group.final_range[1] is not none %}
                        {{ item.group.final_range[0] }} - {{ item.group.final_range[1] }}
                    {% elif item.group.final_range[0] is not none %}
                        &gt; {{ item.group.final_range[0] }}
                    {% elif item.group.final_range[1] is not none %}
                        &lt; {{ item.group.final_range[1] }}
                    {% else %}
                        Not Specified
                    {% endif %}
                </div>
            </div>
            <canvas id="chart-{{ loop.index }}"></canvas>
        </div>
        {% endfor %}
    </div>

    <script>
    // Custom plugin to draw background color zones
    const rangeBackgroundPlugin = {
        id: 'rangeBackground',
        beforeDraw: (chart) => {
            const {ctx, chartArea: {top, bottom, left, right}, scales: {y}} = chart;
            const lower = chart.config.options.plugins.rangeBackground.lower;
            const upper = chart.config.options.plugins.rangeBackground.upper;

            if (lower === null && upper === null) return;

            // Helper to get Y pixel coord, clamped to chart area
            const getY = (val) => Math.max(top, Math.min(bottom, y.getPixelForValue(val)));

            // Draw Normal Range (Green)
            ctx.fillStyle = 'rgba(75, 192, 192, 0.15)'; // Light Green
            let gTop = top;
            let gBottom = bottom;
            
            if (upper !== null) gTop = getY(upper);
            if (lower !== null) gBottom = getY(lower);
            ctx.fillRect(left, gTop, right - left, gBottom - gTop);

            // Draw High Range (Red)
            if (upper !== null) {
                ctx.fillStyle = 'rgba(255, 99, 132, 0.1)'; // Light Red
                ctx.fillRect(left, top, right - left, getY(upper) - top);
            }

            // Draw Low Range (Red)
            if (lower !== null && lower > 0) {
                ctx.fillStyle = 'rgba(255, 99, 132, 0.1)'; // Light Red
                ctx.fillRect(left, getY(lower), right - left, bottom - getY(lower));
            }
        }
    };

    Chart.register(rangeBackgroundPlugin);

    {% for item in data %}
    new Chart(document.getElementById('chart-{{ loop.index }}'), {
        type: 'line',
        data: {
            labels: [{% for dp in item.group.data_points %}"{{ dp.date }}"{% if not loop.last %}, {% endif %}{% endfor %}],
            datasets: [{
                label: '{{ item.group.name }}',
                data: [{% for dp in item.group.data_points %}{{ dp.value }}{% if not loop.last %}, {% endif %}{% endfor %}],
                borderColor: '#007bff',
                backgroundColor: '#007bff',
                borderWidth: 3,
                pointRadius: 5,
                pointHoverRadius: 8,
                tension: 0.2,
                fill: false
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                y: {
                    beginAtZero: false,
                    title: { display: true, text: '{{ item.group.units }}' }
                }
            },
            plugins: {
                legend: { display: false },
                rangeBackground: {
                    lower: {{ item.group.final_range[0]|tojson }},
                    upper: {{ item.group.final_range[1]|tojson }}
                }
            }
        }
    });
    {% endfor %}
    </script>
</body>
</html>
"""

# --- Routes ---
@app.route('/', methods=['GET', 'POST'])
def upload_file():
    uploaded_filename = None
    if request.method == 'POST':
        if 'file' not in request.files:
            flash('No file part')
            return redirect(request.url)
        file = request.files['file']
        if file.filename == '':
            flash('No selected file')
            return redirect(request.url)
        if file:
            filename = secure_filename(file.filename)
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            uploaded_filename = filename
    
    # List existing files in working directory
    existing_files = []
    if os.path.exists(app.config['UPLOAD_FOLDER']):
        files = os.listdir(app.config['UPLOAD_FOLDER'])
        # Filter for XML files only
        existing_files = sorted([f for f in files if f.lower().endswith('.xml')], reverse=True)
            
    return render_template_string(HTML_TEMPLATE, 
                                 uploaded_filename=uploaded_filename, 
                                 existing_files=existing_files)

@app.route('/delete/<filename>', methods=['POST'])
def delete_file(filename):
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(filename))
    if os.path.exists(filepath):
        os.remove(filepath)
        flash(f'File {filename} deleted.')
    return redirect(url_for('upload_file'))

@app.route('/patient/<filename>')
def view_patient(filename):
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(filename))
    if not os.path.exists(filepath):
        return "File not found.", 404
    
    try:
        data = parse_ehr_xml(filepath)
    except Exception as e:
        return f"Error parsing XML: {str(e)}", 500
        
    return render_template_string(PATIENT_TEMPLATE, data=data, filename=filename)

@app.route('/patient/<filename>/viewer/<source>/<doc_id>')
def document_viewer(filename, source, doc_id):
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(filename))
    if not os.path.exists(filepath):
        return "File not found.", 404
    
    try:
        data = parse_ehr_xml(filepath)
    except Exception as e:
        return f"Error parsing XML: {str(e)}", 500
        
    return render_template_string(VIEWER_TEMPLATE, 
                                 data=data, 
                                 filename=filename, 
                                 active_source=source, 
                                 active_id=doc_id)

@app.route('/document/<filename>/<source>/<doc_id>')
def view_document(filename, source, doc_id):
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(filename))
    if not os.path.exists(filepath):
        return "File not found.", 404
        
    try:
        with open(filepath, 'rb') as f:
            xml_data = f.read()
        if xml_data.startswith(b'\xef\xbb\xbf'):
            xml_data = xml_data[3:]
        xml_text = xml_data.decode('ISO-8859-1')
        xml_text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', xml_text)
        root = ET.fromstring(xml_text)
        
        is_bps = (root.tag == 'BPSEHRV2')
        
        if source == 'document':
            # Find specific document
            if is_bps:
                doc_node = root.find(f".//Document[DOCUMENTID='{doc_id}']")
                if doc_node is None:
                    return "Document not found in EHR.", 404
                    
                content_node = doc_node.find('.//DocumentPage/Content')
                if content_node is None or not content_node.text:
                    return "Document content is empty.", 404
                    
                b64_data = content_node.text.strip()
                zip_data = base64.b64decode(b64_data)
                
                # Attempt to unzip the binary payload
                extracted_filename = f"document_{doc_id}.bin"
                file_bytes = zip_data
                is_zipped = False
                try:
                    with zipfile.ZipFile(io.BytesIO(zip_data)) as z:
                        file_list = z.namelist()
                        if file_list:
                            extracted_filename = file_list[0]
                            file_bytes = z.read(extracted_filename)
                            is_zipped = True
                except zipfile.BadZipFile:
                    pass # Use raw decoded bytes if it wasn't actually zipped
                    
                # Determine mime type and filename
                doc_type_node = doc_node.find('.//DocumentPage/DocType')
                doc_type = doc_type_node.text.strip().lower() if doc_type_node is not None else ''
                
                filename_node = doc_node.find('.//DocumentPage/FileName')
                original_filename = filename_node.text.strip() if filename_node is not None and filename_node.text else ''
                
                if not is_zipped:
                    if original_filename:
                        extracted_filename = original_filename
                    else:
                        extracted_filename = f"document_{doc_id}.{doc_type if doc_type else 'bin'}"
            else:
                doc_node = root.find(f".//DOCUMENT[DOC_NO='{doc_id}']")
                if doc_node is None:
                    return "Document not found in EHR.", 404
                    
                content_node = doc_node.find('BASE64_DATA')
                if content_node is None or not content_node.text:
                    return "Document content is empty.", 404
                    
                b64_data = content_node.text.strip()
                file_bytes = base64.b64decode(b64_data)
                
                filename_node = doc_node.find('FILENAME')
                original_filename = filename_node.text.strip() if filename_node is not None and filename_node.text else ''
                extracted_filename = original_filename if original_filename else f"document_{doc_id}.bin"
                
                doc_type_node = doc_node.find('DOCTYPE')
                doc_type = doc_type_node.text.strip().lower() if doc_type_node is not None else ''
                
            # Determine extension
            ext = extracted_filename.split('.')[-1].lower() if '.' in extracted_filename else doc_type
            
            # Handle TIF conversion for browser compatibility
            if ext in ['tif', 'tiff', 'mtif']:
                try:
                    img = Image.open(io.BytesIO(file_bytes))
                    img_io = io.BytesIO()
                    # Convert to RGB if necessary (TIFs can be CMYK or other modes)
                    if img.mode != 'RGB':
                        img = img.convert('RGB')
                    img.save(img_io, 'PNG')
                    img_io.seek(0)
                    return send_file(img_io, mimetype='image/png')
                except Exception as e:
                    pass # Fallback to raw download if conversion fails

            # If it's an RTF, we can try to strip it to Plain Text so it renders in the browser natively
            if doc_type == 'rtf' or ext == 'rtf':
                try:
                    rtf_content = file_bytes.decode('utf-8', errors='ignore')
                    plain_text = rtf_to_text(rtf_content)
                    
                    # Convert pipe-separated data into HTML tables
                    formatted_html_content = format_text_tables(plain_text)
                    
                    return render_template_string("""
                    <!DOCTYPE html>
                    <html><head><title>{{ title }}</title></head>
                    <body style="font-family: monospace; padding: 20px; background-color: #f4f4f9; max-width: 1000px; margin: 0 auto; background: white; box-shadow: 0 0 10px rgba(0,0,0,0.1);">
                    {{ content|safe }}
                    </body></html>
                    """, title=extracted_filename, content=formatted_html_content)
                except Exception as e:
                    pass # Fallback to downloading the RTF file if conversion fails

            mimetypes = {
                'pdf': 'application/pdf',
                'rtf': 'application/rtf',
                'bmp': 'image/bmp',
                'jpg': 'image/jpeg',
                'jpeg': 'image/jpeg',
                'png': 'image/png',
                'txt': 'text/plain',
                'html': 'text/html'
            }
            
            # Extract extension from filename to be sure
            ext = extracted_filename.split('.')[-1].lower() if '.' in extracted_filename else doc_type
            mimetype = mimetypes.get(ext, mimetypes.get(doc_type, 'application/octet-stream'))
            
            return send_file(
                io.BytesIO(file_bytes),
                mimetype=mimetype,
                as_attachment=False,
                download_name=extracted_filename
            )
            
        elif source == 'investigation':
            inv_node = root.find(f".//Investigation[REPORTID='{doc_id}']")
            if inv_node is None:
                return "Investigation not found in EHR.", 404
                
            rtf_body = get_text(inv_node, 'REPORTBODY')
            rtf_header = get_text(inv_node, 'REPORTHEADER')
            test_name = get_text(inv_node, 'TESTNAME')
            
            # If there's an RTF body, render it as text
            if rtf_body:
                try:
                    plain_header = rtf_to_text(rtf_header) if rtf_header else ""
                    plain_body = rtf_to_text(rtf_body)
                    
                    combined_content = ""
                    if plain_header:
                        combined_content += f"{plain_header}\n{'='*80}\n\n"
                    combined_content += plain_body
                    
                    formatted_html_content = format_text_tables(combined_content)
                        
                    return render_template_string("""
                    <!DOCTYPE html>
                    <html><head><title>{{ title }}</title></head>
                    <body style="font-family: monospace; padding: 20px; background-color: #f4f4f9; max-width: 1000px; margin: 0 auto; background: white; box-shadow: 0 0 10px rgba(0,0,0,0.1);">
                    {{ content|safe }}
                    </body></html>
                    """, title=test_name, content=formatted_html_content)
                except Exception as e:
                    return f"Error parsing RTF text: {str(e)}", 500
                    
            # If no body but there is a binary content page (like PDF/HTML attached to an Investigation)
            content_node = inv_node.find('.//InvestigationPage/Content')
            if content_node is not None and content_node.text:
                b64_data = content_node.text.strip()
                zip_data = base64.b64decode(b64_data)
                
                # Attempt to unzip
                extracted_filename = f"investigation_{doc_id}.bin"
                file_bytes = zip_data
                is_zipped = False
                try:
                    with zipfile.ZipFile(io.BytesIO(zip_data)) as z:
                        file_list = z.namelist()
                        if file_list:
                            extracted_filename = file_list[0]
                            file_bytes = z.read(extracted_filename)
                            is_zipped = True
                except zipfile.BadZipFile:
                    pass
                
                doc_type_node = inv_node.find('.//InvestigationPage/DocType')
                doc_type = doc_type_node.text.strip().lower() if doc_type_node is not None else ''
                
                if not is_zipped:
                    extracted_filename = f"investigation_{doc_id}.{doc_type if doc_type else 'bin'}"
                    
                ext = extracted_filename.split('.')[-1].lower() if '.' in extracted_filename else doc_type
                
                # Handle TIF
                if ext in ['tif', 'tiff', 'mtif']:
                    try:
                        img = Image.open(io.BytesIO(file_bytes))
                        img_io = io.BytesIO()
                        if img.mode != 'RGB':
                            img = img.convert('RGB')
                        img.save(img_io, 'PNG')
                        img_io.seek(0)
                        return send_file(img_io, mimetype='image/png')
                    except Exception as e:
                        pass
                        
                mimetypes = {
                    'pdf': 'application/pdf',
                    'rtf': 'application/rtf',
                    'bmp': 'image/bmp',
                    'jpg': 'image/jpeg',
                    'jpeg': 'image/jpeg',
                    'png': 'image/png',
                    'txt': 'text/plain',
                    'html': 'text/html'
                }
                mimetype = mimetypes.get(ext, mimetypes.get(doc_type, 'application/octet-stream'))
                
                return send_file(
                    io.BytesIO(file_bytes),
                    mimetype=mimetype,
                    as_attachment=False,
                    download_name=extracted_filename
                )
            
            # If there's neither a body nor an attachment, but there's a header
            elif rtf_header:
                try:
                    plain_header = rtf_to_text(rtf_header)
                    formatted_html_content = format_text_tables(plain_header)
                    return render_template_string("""
                    <!DOCTYPE html>
                    <html><head><title>{{ title }}</title></head>
                    <body style="font-family: monospace; padding: 20px; background-color: #f4f4f9; max-width: 1000px; margin: 0 auto; background: white; box-shadow: 0 0 10px rgba(0,0,0,0.1);">
                    <h3>Report Header Only (No Body Attached)</h3>
                    {{ content|safe }}
                    </body></html>
                    """, title=test_name, content=formatted_html_content)
                except Exception as e:
                    return f"Error parsing RTF text: {str(e)}", 500

            return "Investigation content is empty.", 404
                
        elif source == 'correspondence':
            corr_node = root.find(f".//CorrespondenceOut/Correspondence[RECORDID='{doc_id}']")
            if corr_node is None:
                return "Correspondence not found in EHR.", 404
                
            rtf_content = get_text(corr_node, 'CONTENT')
            if not rtf_content:
                return "Correspondence content is empty.", 404
                
            try:
                plain_text = rtf_to_text(rtf_content)
                subject = get_text(corr_node, 'SUBJECT')
                formatted_html_content = format_text_tables(plain_text)
                
                return render_template_string("""
                <!DOCTYPE html>
                <html><head><title>{{ title }}</title></head>
                <body style="font-family: monospace; padding: 20px; background-color: #f4f4f9; max-width: 1000px; margin: 0 auto; background: white; box-shadow: 0 0 10px rgba(0,0,0,0.1);">
                {{ content|safe }}
                </body></html>
                """, title=subject, content=formatted_html_content)
            except Exception as e:
                return f"Error parsing RTF text: {str(e)}", 500
                
        else:
            return "Invalid document source.", 400
            
    except Exception as e:
        return f"Error retrieving document: {str(e)}", 500

def parse_numeric(value_str):
    if not value_str: return None
    # Strip everything except numbers and decimal points
    # This handles "> 90" -> "90" and "< 0.5" -> "0.5"
    nums = re.findall(r"[-+]?\d*\.\d+|\d+", value_str)
    if nums:
        return float(nums[0])
    return None

def parse_range(range_str):
    if not range_str or range_str.upper() == 'NIL':
        return None, None
    
    # Handle "n - m" or "n-m"
    if '-' in range_str:
        parts = range_str.split('-')
        if len(parts) == 2:
            return parse_numeric(parts[0]), parse_numeric(parts[1])
            
    # Handle "below n" or "< n"
    if 'BELOW' in range_str.upper() or '<' in range_str:
        return 0, parse_numeric(range_str)
        
    # Handle "above n" or "> n"
    if 'ABOVE' in range_str.upper() or '>' in range_str:
        return parse_numeric(range_str), None
        
    return None, None

@app.route('/patient/<filename>/charts')
def view_charts(filename):
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(filename))
    if not os.path.exists(filepath):
        return "File not found.", 404
        
    try:
        with open(filepath, 'rb') as f:
            xml_data = f.read()
        if xml_data.startswith(b'\xef\xbb\xbf'):
            xml_data = xml_data[3:]
        xml_text = xml_data.decode('ISO-8859-1')
        xml_text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', xml_text)
        root = ET.fromstring(xml_text)
        
        is_bps = (root.tag == 'BPSEHRV2')
        
        # Get patient info for header
        if is_bps:
            patient = root.find('.//Demographics/Patient') or root.find('.//Patient')
            demo = {
                'name': f"{get_text(patient, 'FIRSTNAME')} {get_text(patient, 'SURNAME')}",
                'dob': get_text(patient, 'DOB')
            }
        else:
            patient = root.find('.//DEMOGRAPHICS')
            demo = {
                'name': f"{get_text(patient, 'FIRSTNAME')} {get_text(patient, 'SURNAME')}",
                'dob': normalize_date(get_text(patient, 'DOB'))
            }

        # LOINC filter from user
        allowed_loinc = {
            '10334-1','10535-3','12841-3','13965-9','14334-7','14631-6','14635-7','14646-4','14647-2','14685-2',
            '14771-0','14804-9','14805-6','14920-3','14927-8','14928-6','14933-6','1742-6','1743-4','17856-6',
            '1920-8','1988-5','2064-4','2157-6','22748-8','2276-4','2324-2','24108-3','2532-0',
            '2601-3','2823-3','2857-1','2885-2','2890-2','29265-6','2951-2','3016-3','32294-1','32309-7',
            '33914-3','39469-2','43396-1','4537-7','4544-3','4548-4','50210-4','59261-8','61152-5','6690-2',
            '6768-6','70204-3','711-2','718-7','72160-5','731-0','742-7','751-8','777-3','787-2','789-8','9830-1'
        }

        # Group data by LOINC
        loinc_groups = {}
        results_nodes = root.findall('.//Result') if is_bps else [r for r in root.findall('.//RESULT') if r.find('LOINC') is not None]
        
        for res in results_nodes:
            if is_bps:
                code = get_text(res, 'LOINCCODE')
                val_str = get_text(res, 'RESULTVALUE')
                name_str = get_text(res, 'RESULTNAME')
                units_str = get_text(res, 'UNITS')
                range_str = get_text(res, 'RANGE')
                date_str = get_text(res, 'REPORTDATE')
            else:
                code = get_text(res, 'LOINC')
                val_str = get_text(res, 'RESULT')
                name_str = get_text(res, 'TESTNAME')
                units_str = get_text(res, 'UNITS')
                range_str = get_text(res, 'RANGE')
                date_str = normalize_date(get_text(res, 'RESULTDATE'))
                
            if code not in allowed_loinc: continue
            
            val = parse_numeric(val_str)
            if val is None: continue # Skip if not numeric
            
            if code not in loinc_groups:
                loinc_groups[code] = {
                    'name_variants': set(),
                    'data_points': [],
                    'units': units_str,
                    'ranges': set()
                }
            
            loinc_groups[code]['name_variants'].add(name_str)
            lower, upper = parse_range(range_str)
            
            loinc_groups[code]['data_points'].append({
                'date': date_str,
                'value': val,
                'lower': lower,
                'upper': upper
            })

        # Finalize and Harmonize
        final_charts = []
        for code, group in loinc_groups.items():
            # Pick shortest name
            group['name'] = min(group['name_variants'], key=len)
            # Sort by date
            group['data_points'].sort(key=lambda x: [int(part) for part in x['date'].split('/')[::-1]] if '/' in x['date'] else [0])
            
            # Use most common range if available
            ranges = [ (dp['lower'], dp['upper']) for dp in group['data_points'] if dp['lower'] is not None or dp['upper'] is not None]
            if ranges:
                group['final_range'] = max(set(ranges), key=ranges.count)
            else:
                group['final_range'] = (None, None)
                
            final_charts.append({'code': code, 'group': group})
            
        # Sort charts alphabetically by name
        final_charts.sort(key=lambda x: x['group']['name'])

        return render_template_string(CHARTS_TEMPLATE, data=final_charts, demo=demo, filename=filename)
        
    except Exception as e:
        return f"Error creating charts: {str(e)}", 500

@app.route('/proposal')
def show_proposal():
    proposal_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'proposal.md')
    if os.path.exists(proposal_path):
        with open(proposal_path, 'r', encoding='utf-8') as f:
            content = f.read()
            html_content = markdown.markdown(content)
            
            page_html = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <title>Extraction Proposal</title>
                <style>
                    body {{ font-family: sans-serif; line-height: 1.6; max-width: 800px; margin: 40px auto; padding: 20px; color: #333; }}
                    h1, h2, h3 {{ color: #007bff; }}
                    a {{ color: #007bff; text-decoration: none; }}
                    a:hover {{ text-decoration: underline; }}
                    code {{ background-color: #f4f4f4; padding: 2px 4px; border-radius: 4px; }}
                    .back-link {{ display: inline-block; margin-bottom: 20px; font-weight: bold; }}
                </style>
            </head>
            <body>
                <a href="/" class="back-link">&larr; Back to Upload</a>
                {html_content}
            </body>
            </html>
            """
            return render_template_string(page_html)
    return "Proposal file not found.", 404

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5002, debug=True)
