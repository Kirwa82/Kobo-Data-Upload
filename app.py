import ast
import re
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any

import pandas as pd
import requests
import streamlit as st

# ============================================
# CONFIGURATION
# ============================================
st.set_page_config(
    page_title="KoboToolbox V2 Importer",
    page_icon="🔄",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ============================================
# SESSION STATE INITIALIZATION
# ============================================
if 'diagnostics' not in st.session_state:
    st.session_state.diagnostics = None
if 'schema' not in st.session_state:
    st.session_state.schema = None
if 'mapping' not in st.session_state:
    st.session_state.mapping = {}
if 'dfs' not in st.session_state:
    st.session_state.dfs = {}
if 'main_sheet' not in st.session_state:
    st.session_state.main_sheet = None

# ============================================
# CUSTOM CSS
# ============================================
st.markdown("""
<style>
    .stButton > button {
        width: 100%;
    }
    .success-metric {
        background-color: #d4edda;
        padding: 10px;
        border-radius: 5px;
    }
    .error-metric {
        background-color: #f8d7da;
        padding: 10px;
        border-radius: 5px;
    }
    .field-required {
        color: #dc3545;
        font-weight: bold;
    }
</style>
""", unsafe_allow_html=True)

# ============================================
# HELPER FUNCTIONS & CHOICE MAPPERS
# ============================================

def clean_cell_value(val: Any) -> Optional[str]:
    """Clean pandas values to prevent literal 'nan' or float string issues."""
    if pd.isna(val) or val is None:
        return None

    if isinstance(val, float):
        if val.is_integer():
            return str(int(val))
        return str(val)

    text = str(val).strip()
    if text in {"", "nan", "NaN", "None", "null"}:
        return None
    return text


def map_choice_to_xml_value(val: Any, choice_list: List[Dict[str, str]], is_multiple: bool = False) -> Optional[str]:
    """Translate human-readable labels into XML names based on schema choice definitions."""
    if val is None:
        return None

    label_to_name = {str(item.get("label")).strip().lower(): str(item.get("name")).strip() for item in choice_list if item.get("label") and item.get("name")}
    name_set = {str(item.get("name")).strip().lower() for item in choice_list if item.get("name")}

    def lookup_single(token: str) -> str:
        token_clean = token.strip()
        token_lower = token_clean.lower()
        if token_lower in label_to_name:
            return label_to_name[token_lower]
        if token_lower in name_set:
            return token_clean
        return token_clean

    text = str(val).strip()
    if not text:
        return None

    if is_multiple:
        tokens = re.split(r'[,;|\n]+', text) if any(sep in text for sep in [",", ";", "|", "\n"]) else text.split()
        mapped_tokens = [lookup_single(t) for t in tokens if t.strip()]
        return " ".join(mapped_tokens) if mapped_tokens else None

    return lookup_single(text)


def normalize_choice_value(value: Any, field_info: Optional[Dict] = None) -> Optional[str]:
    """Normalize choice values for Kobo select_one/select_multiple fields."""
    cell_str = clean_cell_value(value)
    if cell_str is None:
        return None

    if field_info and field_info.get("type") in ["select_one", "select_multiple"]:
        choices = field_info.get("choices", [])
        if choices:
            is_mult = field_info.get("type") == "select_multiple"
            return map_choice_to_xml_value(cell_str, choices, is_multiple=is_mult)

    return cell_str


def build_xml_nodes(
    parent_elem: ET.Element,
    structure: List[Dict],
    record: Dict,
    schema_fields: Dict[str, Dict],
    repeats_data: Optional[Dict[str, List[Dict]]] = None
) -> None:
    """
    Recursively render the form's ordered structure (fields, non-repeat groups,
    and repeats - each of which may nest further groups/fields/repeats) into XML,
    preserving the exact nesting the form definition expects.
    """
    for item in structure:
        kind = item.get("kind")
        name = item.get("name")

        if kind == "field":
            if name in record:
                field_info = schema_fields.get(name, {})
                normalized = normalize_choice_value(record[name], field_info)
                if normalized is not None:
                    child = ET.SubElement(parent_elem, name)
                    child.text = normalized

        elif kind == "group":
            # Ordinary (non-repeating) group: create the wrapping element and recurse
            # into it so fields land at the correct depth in the submission XML.
            group_node = ET.SubElement(parent_elem, name)
            build_xml_nodes(group_node, item.get("children", []), record, schema_fields, repeats_data)
            # Drop the group element again if nothing was actually written inside it,
            # so we don't submit a pile of empty wrapper elements.
            if len(group_node) == 0:
                parent_elem.remove(group_node)

        elif kind == "repeat":
            if repeats_data and name in repeats_data:
                child_rows = repeats_data[name]
                for child_row in child_rows:
                    repeat_node = ET.SubElement(parent_elem, name)
                    for c_key, c_val in child_row.items():
                        c_field_info = schema_fields.get(c_key, {})
                        c_normalized = normalize_choice_value(c_val, c_field_info)
                        if c_normalized is not None:
                            c_child = ET.SubElement(repeat_node, c_key)
                            c_child.text = c_normalized
                    if len(repeat_node) == 0:
                        parent_elem.remove(repeat_node)


def build_openrosa_xml(
    record: Dict,
    form_id: str,
    version: str,
    schema: Dict,
    repeats_data: Optional[Dict[str, List[Dict]]] = None
) -> bytes:
    """Converts a row into OpenRosa XML preserving exact field/group/repeat sequence and nesting."""
    root = ET.Element("data")
    root.set("id", form_id)
    root.set("version", version)

    schema_fields = schema.get("fields_dict", {})
    structure = schema.get("structure", [])

    build_xml_nodes(root, structure, record, schema_fields, repeats_data)

    # Standard metadata block
    meta = ET.SubElement(root, "meta")
    instance_id = ET.SubElement(meta, "instanceID")
    instance_id.text = f"uuid:{str(uuid.uuid4())}"

    return ET.tostring(root, encoding="utf-8", method="xml")

# ============================================
# API FUNCTIONS
# ============================================

def test_api_connection(base_url: str, token: str) -> Tuple[bool, str]:
    headers = {"Authorization": f"Token {token}"}
    try:
        resp = requests.get(f"{base_url}/api/v2/assets/", headers=headers, timeout=10)
        return resp.status_code == 200, f"Status: {resp.status_code}"
    except Exception as e:
        return False, str(e)


def get_form_metadata(base_url: str, form_uid: str, token: str) -> Optional[Dict]:
    headers = {"Authorization": f"Token {token}"}
    try:
        resp = requests.get(f"{base_url}/api/v2/assets/{form_uid}/", headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            return {
                "name": data.get("name"),
                "uid": data.get("uid"),
                "status": data.get("deployment_status"),
                "active": data.get("deployment__active"),
                "submissions": data.get("deployment__submission_count", 0),
                "owner": data.get("owner__username"),
                "created": data.get("date_created"),
                "modified": data.get("date_modified")
            }
    except Exception as e:
        st.error(f"Metadata error: {e}")
    return None


def check_submission_endpoint(kc_url: str, token: str) -> Dict:
    headers = {"Authorization": f"Token {token}"}
    url = f"{kc_url}/submission"
    result = {"url": url, "accessible": False, "post_allowed": False}
    try:
        head = requests.head(url, headers=headers, timeout=10)
        if head.status_code in [200, 204, 405]:
            result["accessible"] = True
            result["post_allowed"] = True
    except Exception:
        pass
    return result


def run_full_diagnostics(base_url: str, kc_url: str, form_uid: str, token: str) -> Dict:
    results = {
        "timestamp": datetime.now().isoformat(),
        "connection": {"success": False, "message": ""},
        "form": {"found": False, "metadata": None},
        "endpoint": {"accessible": False, "url": ""},
        "issues": [],
        "recommendations": []
    }

    connected, msg = test_api_connection(base_url, token)
    results["connection"]["success"] = connected
    results["connection"]["message"] = msg

    if not connected:
        results["issues"].append("Cannot connect to Kobo KPI API")
        results["recommendations"].append("Verify server URL and API token")
        return results

    metadata = get_form_metadata(base_url, form_uid, token)
    if metadata:
        results["form"]["found"] = True
        results["form"]["metadata"] = metadata
        if metadata["status"] != "deployed":
            results["issues"].append(f"Form not deployed (status: {metadata['status']})")
            results["recommendations"].append("Deploy the form in Kobo settings before importing")
    else:
        results["issues"].append("Form not found")
        results["recommendations"].append("Check that the Form Asset UID is correct")
        return results

    endpoint = check_submission_endpoint(kc_url, token)
    results["endpoint"] = {"accessible": endpoint["accessible"], "url": endpoint["url"]}
    if not endpoint["accessible"]:
        results["issues"].append("OpenRosa /submission endpoint unreachable")
        results["recommendations"].append("Verify Legacy Server URL (KoBoCAT)")

    return results


def fetch_form_schema(base_url: str, form_uid: str, token: str) -> Optional[Dict]:
    """
    Fetch complete form schema, preserving the exact sequential order AND the
    nesting of fields inside repeats and ordinary (non-repeating) groups.

    Two bugs from the previous version are fixed here:
      1. `current_repeat` is now properly reset when an `end_repeat` row is
         hit, using an explicit stack instead of a single mutable variable.
         Previously, every field that appeared *after* a repeat group but
         *before* the next one silently inherited the previous repeat's name,
         which caused most top-level fields in forms with multiple repeat
         groups to disappear from the mapping UI entirely.
      2. Ordinary `begin_group`/`end_group` blocks are no longer discarded.
         They're preserved in a nested `structure` tree so the generated
         submission XML nests fields inside their group elements exactly the
         way the form (and OpenRosa/KoBoCAT) expects.
    """
    headers = {"Authorization": f"Token {token}"}
    try:
        resp = requests.get(f"{base_url}/api/v2/assets/{form_uid}/", headers=headers, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            content = data.get("content", {})
            survey = content.get("survey", [])
            choices = content.get("choices", [])

            choice_lists = {}
            for choice in choices:
                list_name = choice.get("list_name")
                if list_name not in choice_lists:
                    choice_lists[list_name] = []

                label_val = choice.get("label")
                if isinstance(label_val, list):
                    label_val = label_val[0] if label_val else choice.get("name")

                choice_lists[list_name].append({
                    "name": str(choice.get("name")),
                    "label": str(label_val) if label_val is not None else str(choice.get("name"))
                })

            fields = []
            repeat_groups = []

            # `structure` is a nested tree mirroring the form's actual layout:
            #   {"kind": "field", "name": ...}
            #   {"kind": "group", "name": ..., "children": [...]}
            #   {"kind": "repeat", "name": ..., "children": [...]}
            structure: List[Dict] = []
            stack: List[List[Dict]] = [structure]   # stack of "children" lists we're currently appending to
            group_stack: List[str] = []             # names of currently-open ordinary groups (for group_path)
            repeat_stack: List[str] = []            # names of currently-open repeats (for repeat_group)

            skip_types = ["note", "calculate", "hidden"]

            for item in survey:
                item_type = item.get("type", "")

                if item_type == "begin_group":
                    gname = item.get("name") or f"group_{len(stack[-1])}"
                    node = {"kind": "group", "name": gname, "children": []}
                    stack[-1].append(node)
                    stack.append(node["children"])
                    group_stack.append(gname)
                    continue

                if item_type == "end_group":
                    if group_stack:
                        group_stack.pop()
                    if len(stack) > 1:
                        stack.pop()
                    continue

                if item_type == "begin_repeat":
                    rname = item.get("name")
                    node = {"kind": "repeat", "name": rname, "children": []}
                    stack[-1].append(node)
                    stack.append(node["children"])
                    if rname:
                        repeat_groups.append(rname)
                    repeat_stack.append(rname)
                    continue

                if item_type == "end_repeat":
                    if repeat_stack:
                        repeat_stack.pop()
                    if len(stack) > 1:
                        stack.pop()
                    continue

                if item_type in skip_types or "name" not in item:
                    continue

                label = item.get("label", item["name"])
                if isinstance(label, list):
                    label = label[0] if label else item["name"]

                field = {
                    "name": item["name"],
                    "type": item_type,
                    "label": str(label),
                    "required": item.get("required", False),
                    # nearest enclosing repeat, if any - drives the "parent form" vs
                    # "repeat group" split in the mapping UI
                    "repeat_group": repeat_stack[-1] if repeat_stack else None,
                    # full chain of enclosing ordinary groups, kept for reference/matching
                    "group_path": list(group_stack),
                    "choices": []
                }

                if item_type in ["select_one", "select_multiple"]:
                    list_name = item.get("select_from_list_name")
                    if list_name and list_name in choice_lists:
                        field["choices"] = choice_lists[list_name]

                fields.append(field)
                stack[-1].append({"kind": "field", "name": item["name"]})

            return {
                "name": data.get("name"),
                "uid": form_uid,
                "id_string": str(data.get("id_string") or data.get("uid")),
                "version_id": str(data.get("version_id", "v1")),
                "status": data.get("deployment_status"),
                "fields": fields,
                "structure": structure,
                "repeat_groups": repeat_groups,
                "fields_dict": {f["name"]: f for f in fields},
                "total_fields": len(fields),
                "required_fields": sum(1 for f in fields if f["required"])
            }
    except Exception as e:
        st.error(f"Schema error: {e}")
    return None


def smart_match_columns(kobo_field: str, csv_columns: List[str]) -> Tuple[Optional[str], float]:
    """Smart matcher aware of group prefixes, slash paths, and leaf question names."""
    kobo_clean = kobo_field.lower().replace("_", " ").replace("/", " ").strip()
    kobo_words = set(kobo_clean.split())
    best_match = None
    best_score = 0.0

    for col in csv_columns:
        col_str = str(col).strip()
        leaf = col_str.split("/")[-1].strip().lower()
        leaf_clean = leaf.replace("_", " ").strip()

        # 1. Direct leaf match (e.g. 'group_xyz/hh_affected_additional' -> 'hh_affected_additional')
        if kobo_field.lower() == leaf:
            return col_str, 1.0

        if kobo_clean == leaf_clean:
            return col_str, 0.98

        col_clean = col_str.lower().replace("_", " ").replace("/", " ").strip()
        if kobo_clean == col_clean:
            return col_str, 0.95

        # 2. Substring / Leaf contained match
        if kobo_clean in col_clean or col_clean in kobo_clean:
            score = 0.85
            if score > best_score:
                best_score = score
                best_match = col_str

        # 3. Word Overlap
        col_words = set(col_clean.split())
        overlap = len(kobo_words & col_words)
        if overlap > 0:
            score = overlap / max(len(kobo_words), len(col_words))
            if score > best_score:
                best_score = score
                best_match = col_str

    return best_match, best_score


def submit_batch(
    records: List[Dict],
    kc_url: str,
    form_id_string: str,
    version_id: str,
    token: str,
    schema: Dict,
    child_records_map: Optional[Dict[Any, Dict[str, List[Dict]]]] = None,
    pk_col: Optional[str] = None,
    progress_bar=None,
    status_text=None,
    delay_sec: float = 0.1
) -> Tuple[int, List[Dict]]:
    url = f"{kc_url}/submission"
    headers = {"Authorization": f"Token {token}"}
    success = 0
    errors = []
    total = len(records)

    for i, record in enumerate(records):
        if progress_bar:
            progress_bar.progress((i + 1) / total)
        if status_text:
            status_text.text(f"Uploading row {i + 1} of {total}...")

        repeats_data = None
        if child_records_map and pk_col and pk_col in record:
            parent_id_val = record[pk_col]
            repeats_data = child_records_map.get(parent_id_val)

        xml_data = build_openrosa_xml(record, form_id_string, version_id, schema, repeats_data=repeats_data)
        files = {'xml_submission_file': ('submission.xml', xml_data, 'text/xml')}

        try:
            resp = requests.post(url, headers=headers, files=files, timeout=30)

            if resp.status_code == 429:
                time.sleep(2.0)
                resp = requests.post(url, headers=headers, files=files, timeout=30)

            if resp.status_code in [200, 201, 202]:
                success += 1
            else:
                errors.append({
                    "row": i + 1,
                    "status": resp.status_code,
                    "data": record,
                    "response": resp.text
                })
        except Exception as e:
            errors.append({
                "row": i + 1,
                "status": "Exception",
                "error": str(e),
                "data": record
            })

        time.sleep(delay_sec)

    return success, errors

# ============================================
# SIDEBAR - CREDENTIALS & DIAGNOSTICS
# ============================================
with st.sidebar:
    st.image("https://www.kobotoolbox.org/assets/img/kobotoolbox-logo.svg", width=200)
    st.markdown("---")

    st.header("🔑 Credentials")
    kobo_url = st.text_input("Server URL (KPI)", value="https://kf.kobotoolbox.org")
    kobo_kc_url = st.text_input("Legacy Server URL (KoBoCAT)", value="https://kc.kobotoolbox.org")
    api_token = st.text_input("API Token", type="password", placeholder="Paste API Token here")
    form_uid = st.text_input("Form Asset UID", placeholder="e.g. aX7p9M...")

    st.markdown("---")
    st.header("🔧 Diagnostics")

    if st.button("🔍 Run Full Diagnostics", use_container_width=True):
        if not all([kobo_url, kobo_kc_url, api_token, form_uid]):
            st.error("Please fill in all credential fields first.")
        else:
            with st.spinner("Running diagnostics..."):
                st.session_state.diagnostics = run_full_diagnostics(kobo_url, kobo_kc_url, form_uid, api_token)

    if st.session_state.diagnostics:
        diag = st.session_state.diagnostics
        st.subheader("Results")
        if diag["connection"]["success"]:
            st.success("✅ API Connected")
        else:
            st.error("❌ API Connection Failed")

        if diag["form"]["found"]:
            st.success("✅ Form Found")
            meta = diag["form"]["metadata"]
            if meta:
                st.info(f"**Name:** {meta['name']}\n\n**Status:** {meta['status']}\n\n**Submissions:** {meta['submissions']}")
        else:
            st.error("❌ Form Not Found")

        if diag["endpoint"]["accessible"]:
            st.success("✅ Submission Receiver Online")
        else:
            st.warning("⚠️ Endpoint Warning")

# ============================================
# MAIN CONTENT
# ============================================
st.title("🔄 KoboToolbox V2 Data Importer")
st.caption("Import multi-sheet Excel files, CSVs, and repeat groups into KoboToolbox via API")

# Step 1: Upload
st.header("📂 Step 1: Upload Dataset")
uploaded_file = st.file_uploader("Choose Excel (.xlsx) or CSV file", type=["csv", "xlsx"])

if uploaded_file:
    try:
        if uploaded_file.name.endswith('.csv'):
            st.session_state.dfs = {"Sheet1": pd.read_csv(uploaded_file)}
            st.session_state.main_sheet = "Sheet1"
        else:
            uploaded_file.seek(0)
            xls = pd.ExcelFile(uploaded_file, engine='openpyxl')
            sheet_names = xls.sheet_names

            st.session_state.dfs = {sheet: xls.parse(sheet) for sheet in sheet_names}

            if len(sheet_names) > 1:
                st.session_state.main_sheet = st.selectbox(
                    "Select Primary/Parent Data Sheet",
                    sheet_names,
                    index=0
                )
            else:
                st.session_state.main_sheet = sheet_names[0]

        parent_df = st.session_state.dfs[st.session_state.main_sheet]

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Primary Rows", len(parent_df))
        col2.metric("Primary Columns", len(parent_df.columns))
        col3.metric("Total Sheets", len(st.session_state.dfs))
        col4.metric("Missing Values", parent_df.isnull().sum().sum())

        # MULTI-SHEET PREVIEW FEATURE
        with st.expander("👁️ Data Preview", expanded=True):
            if len(st.session_state.dfs) > 1:
                preview_sheet = st.selectbox(
                    "Select sheet to preview:",
                    list(st.session_state.dfs.keys()),
                    index=list(st.session_state.dfs.keys()).index(st.session_state.main_sheet)
                )
            else:
                preview_sheet = list(st.session_state.dfs.keys())[0]

            preview_df = st.session_state.dfs[preview_sheet]
            st.markdown(f"**Previewing Sheet:** `{preview_sheet}` ({len(preview_df)} rows, {len(preview_df.columns)} columns)")
            st.dataframe(preview_df.head(10), use_container_width=True)
            st.markdown("**Column Names:**")
            st.code(", ".join(preview_df.columns.tolist()))

    except Exception as e:
        st.error(f"❌ File Error: {str(e)}")
        st.stop()

# Step 2: Schema & Mapping
if st.session_state.dfs and all([kobo_url, api_token, form_uid]):
    st.markdown("---")
    st.header("🔀 Step 2: Field & Repeat Group Mapping")

    if st.button("📋 Fetch Form Schema", type="primary"):
        with st.spinner("Fetching form structure..."):
            st.session_state.schema = fetch_form_schema(kobo_url, form_uid, api_token)

    if st.session_state.schema:
        schema = st.session_state.schema
        st.sidebar.markdown("---")
        st.sidebar.subheader("⚙️ XML Identifiers")
        final_id_string = st.sidebar.text_input("Form XML ID String", value=schema['id_string'])

        st.success(f"**{schema['name']}** — {schema['total_fields']} fields ({schema['required_fields']} required) | ID String: `{final_id_string}`")
        if schema['status'] != 'deployed':
            st.error("⚠️ Form is not deployed! Please deploy it in KoboToolbox before importing.")

        parent_df = st.session_state.dfs[st.session_state.main_sheet]
        csv_cols = list(parent_df.columns)

        # OPTIONAL: LEGACY MAPPING FILE UPLOAD BUTTON
        st.markdown("#### Optional: Load Header Mapping File")
        mapping_file = st.file_uploader("Upload kobo_name_mapping.xlsx to auto-fill headers", type=["xlsx", "csv"], key="legacy_map_uploader")

        legacy_lookup = {}
        if mapping_file:
            try:
                m_df = pd.read_csv(mapping_file) if mapping_file.name.endswith('.csv') else pd.read_excel(mapping_file)
                if 'old_name' in m_df.columns and 'new_name' in m_df.columns:
                    legacy_lookup = dict(zip(m_df['old_name'].astype(str), m_df['new_name'].astype(str)))
                    st.success(f"Loaded {len(legacy_lookup)} header mappings from `{mapping_file.name}`")
            except Exception as e:
                st.warning(f"Could not parse mapping file: {e}")

        mapping = {}

        st.subheader("Parent Form Fields Mapping")
        col1, col2, col3, col4 = st.columns([2, 3, 1, 1])
        col1.markdown("**Kobo Field**")
        col2.markdown("**Excel/CSV Column**")
        col3.markdown("**Match**")
        col4.markdown("**Required**")

        auto_matches = 0
        parent_fields = [f for f in schema['fields'] if not f.get('repeat_group')]

        for field in parent_fields:
            col1, col2, col3, col4 = st.columns([2, 3, 1, 1])
            with col1:
                st.markdown(f"`{field['name']}`")
                caption = field['type']
                if field.get('group_path'):
                    caption += f"  ·  in {' / '.join(field['group_path'])}"
                st.caption(caption)
            with col2:
                # First check if user legacy lookup file maps an old header
                matched, confidence = smart_match_columns(field['name'], csv_cols)

                # Override match if legacy mapping lookup provides exact match
                if legacy_lookup:
                    for old_h, new_h in legacy_lookup.items():
                        if new_h == field['name'] and old_h in csv_cols:
                            matched = old_h
                            confidence = 1.0
                            break

                default_idx = csv_cols.index(matched) + 1 if matched and matched in csv_cols else 0
                sel = st.selectbox(
                    f"Map {field['name']}",
                    ["-- Skip --"] + csv_cols,
                    index=default_idx if default_idx <= len(csv_cols) else 0,
                    key=f"map_{field['name']}",
                    label_visibility="collapsed"
                )
                if sel != "-- Skip --":
                    mapping[sel] = field['name']
            with col3:
                if matched:
                    st.progress(confidence, text=f"{confidence:.0%}")
                    if confidence > 0.7:
                        auto_matches += 1
            with col4:
                if field['required']:
                    st.markdown('<span class="field-required">Required</span>', unsafe_allow_html=True)

        st.session_state.mapping = mapping

        # Repeat Group Config
        child_records_map = {}
        pk_col = None

        if schema['repeat_groups'] and len(st.session_state.dfs) > 1:
            st.markdown("---")
            st.subheader("🔁 Repeat Groups Configuration")
            st.info("Configure parent-child sheet links to nest repeat groups inside parent entries.")

            pk_col = st.selectbox("Select Parent Sheet Primary Key Column (e.g. _id)", parent_df.columns)

            # Common Kobo export column names that link a repeat-group sheet back to the
            # parent submission, in priority order.
            FK_CANDIDATE_NAMES = ["_submission__id", "_parent_index", "_submission__uuid"]

            unlinked_groups = []
            incomplete_groups = []

            for group in schema['repeat_groups']:
                st.markdown(f"**Repeat Group: `{group}`**")

                sheet_options = ["-- None --"] + list(st.session_state.dfs.keys())
                # Auto-select the sheet whose name matches the repeat group's name -
                # this is the common case (e.g. Kobo's own multi-sheet exports name
                # the sheet after the repeat group), and avoids silently skipping a
                # repeat group just because nobody clicked the dropdown.
                default_sheet_idx = sheet_options.index(group) if group in st.session_state.dfs else 0
                g_sheet = st.selectbox(f"Sheet for `{group}`", sheet_options, index=default_sheet_idx, key=f"sheet_{group}")

                if g_sheet == "-- None --":
                    st.warning(f"⚠️ No sheet linked for `{group}` — this repeat group's data will **not** be imported.")
                    unlinked_groups.append(group)
                    continue

                child_df = st.session_state.dfs[g_sheet]
                fk_options = list(child_df.columns)
                fk_default = next((c for c in FK_CANDIDATE_NAMES if c in fk_options), None)
                if fk_default is None and pk_col in fk_options:
                    fk_default = pk_col
                fk_default_idx = fk_options.index(fk_default) if fk_default else 0
                fk_col = st.selectbox(
                    f"Foreign Key Column in `{g_sheet}` (maps to parent PK)",
                    fk_options,
                    index=fk_default_idx,
                    key=f"fk_{group}"
                )

                group_fields = [f for f in schema['fields'] if f.get('repeat_group') == group]
                child_map = {}
                st.caption(f"Map fields for repeat group `{group}`:")
                for g_field in group_fields:
                    g_matched, _ = smart_match_columns(g_field['name'], list(child_df.columns))

                    if legacy_lookup:
                        for old_h, new_h in legacy_lookup.items():
                            if new_h == g_field['name'] and old_h in child_df.columns:
                                g_matched = old_h
                                break

                    g_idx = list(child_df.columns).index(g_matched) + 1 if g_matched and g_matched in child_df.columns else 0
                    g_sel = st.selectbox(f"Map `{g_field['name']}` ({g_sheet})", ["-- Skip --"] + list(child_df.columns), index=g_idx, key=f"gmap_{group}_{g_field['name']}")
                    if g_sel != "-- Skip --":
                        child_map[g_sel] = g_field['name']

                if not child_map:
                    st.warning(f"⚠️ No fields mapped for `{group}` — this repeat group's data will **not** be imported.")
                    incomplete_groups.append(group)
                elif not fk_col:
                    st.warning(f"⚠️ No Foreign Key column selected for `{group}` — this repeat group's data will **not** be imported.")
                    incomplete_groups.append(group)
                else:
                    for parent_id, group_rows in child_df.groupby(fk_col):
                        mapped_rows = []
                        for _, r in group_rows.iterrows():
                            row_dict = {child_map[col]: r[col] for col in child_map.keys() if col in r}
                            mapped_rows.append(row_dict)

                        if parent_id not in child_records_map:
                            child_records_map[parent_id] = {}
                        child_records_map[parent_id][group] = mapped_rows
                    st.success(f"✅ `{group}` linked via `{fk_col}` — {len(child_df.groupby(fk_col))} parent record(s) will get nested repeat data.")

            if unlinked_groups or incomplete_groups:
                st.error(
                    "🚫 Before importing, note that the following repeat groups are **not fully configured** "
                    f"and their data will be skipped: {', '.join(unlinked_groups + incomplete_groups)}."
                )

        # Step 3: Execution
        st.markdown("---")
        st.header("🚀 Step 3: Import Data")

        col1, col2 = st.columns(2)
        with col1:
            test_mode = st.checkbox("Test mode (Import only first 5 rows)", value=False)
        with col2:
            delay_sec = st.slider("Throttling Delay between requests (seconds)", 0.0, 1.0, 0.1, 0.05)

        if mapping:
            st.info(f"**Ready to import:** {5 if test_mode else len(parent_df)} parent rows using {len(mapping)} mapped fields.")

        if st.button("▶️ Start Import", type="primary", disabled=not mapping, use_container_width=True):
            parent_mapped_df = parent_df[list(mapping.keys())].copy()
            if pk_col and pk_col not in parent_mapped_df.columns:
                parent_mapped_df[pk_col] = parent_df[pk_col]

            parent_mapped_df.rename(columns=mapping, inplace=True)
            if test_mode:
                parent_mapped_df = parent_mapped_df.head(5)

            records = parent_mapped_df.to_dict('records')

            progress_bar = st.progress(0)
            status_text = st.empty()

            success, errors = submit_batch(
                records=records,
                kc_url=kobo_kc_url,
                form_id_string=final_id_string,
                version_id=schema['version_id'],
                token=api_token,
                schema=schema,
                child_records_map=child_records_map,
                pk_col=pk_col,
                progress_bar=progress_bar,
                status_text=status_text,
                delay_sec=delay_sec
            )

            progress_bar.progress(1.0)
            status_text.empty()

            col1, col2, col3 = st.columns(3)
            col1.metric("✅ Success", success)
            col2.metric("❌ Failed", len(errors))
            col3.metric("📊 Total Processed", len(records))

            if success > 0:
                st.balloons()
            if errors:
                st.warning(f"⚠️ {len(errors)} records encountered server processing issues.")
                with st.expander("🔍 Error Details", expanded=True):
                    for err in errors:
                        st.markdown(f"### Row {err['row']} — Status: {err.get('status', 'N/A')}")
                        st.code(err.get('response', err.get('error', 'Unknown Error')))
                        st.markdown("---")

                error_df = pd.DataFrame([{'Row': e['row'], 'Status': e.get('status'), 'Error': str(e.get('response', e.get('error', '')))[:300]} for e in errors])
                st.download_button("📥 Download Error Report", error_df.to_csv(index=False), "kobo_import_errors.csv", "text/csv")

# ============================================
# FOOTER
# ============================================
st.markdown("---")
st.caption("KoboToolbox V2 Importer | Built with Streamlit | OpenRosa Multi-Sheet Engine")