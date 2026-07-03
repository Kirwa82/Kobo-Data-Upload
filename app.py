import streamlit as st
import pandas as pd
import requests
import xml.etree.ElementTree as ET
import uuid  
from typing import Dict, List, Optional, Tuple
from datetime import datetime

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
if 'df' not in st.session_state:
    st.session_state.df = None

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
# HELPER FUNCTIONS
# ============================================
def build_openrosa_xml(record: Dict, form_id: str, version: str) -> bytes:
    """Converts a standard mapped dictionary row into an OpenRosa compliant XML string structure."""
    root = ET.Element("data")
    root.set("id", form_id)
    root.set("version", version)
    
    # Append genuine question nodes
    for key, value in record.items():
        if value is not None and str(value).strip() != "" and str(value).strip() != "None":
            child = ET.SubElement(root, key)
            child.text = str(value).strip()
            
    # Add mandatory OpenRosa submission metadata instanceID block
    meta = ET.SubElement(root, "meta")
    instance_id = ET.SubElement(meta, "instanceID")
    instance_id.text = f"uuid:{str(uuid.uuid4())}"
            
    return ET.tostring(root, encoding="utf-8", method="xml")

# ============================================
# API FUNCTIONS
# ============================================

def test_api_connection(base_url: str, token: str) -> Tuple[bool, str]:
    """Test basic API connectivity."""
    headers = {"Authorization": f"Token {token}"}
    try:
        resp = requests.get(f"{base_url}/api/v2/assets/", headers=headers, timeout=10)
        return resp.status_code == 200, f"Status: {resp.status_code}"
    except Exception as e:
        return False, str(e)

def get_form_metadata(base_url: str, form_uid: str, token: str) -> Optional[Dict]:
    """Get form metadata including deployment status."""
    headers = {"Authorization": f"Token {token}"}
    try:
        resp = requests.get(f"{base_url}/api/v2/assets/{form_uid}/", headers=headers)
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
    """Check OpenRosa submission endpoint accessibility."""
    headers = {"Authorization": f"Token {token}"}
    url = f"{kc_url}/submission"
    result = {"url": url, "accessible": False, "post_allowed": False}
    try:
        head = requests.head(url, headers=headers, timeout=10)
        if head.status_code in [200, 204, 405]:
            result["accessible"] = True
            result["post_allowed"] = True
    except:
        pass
    return result

def run_full_diagnostics(base_url: str, kc_url: str, form_uid: str, token: str) -> Dict:
    """Run comprehensive diagnostics and return results."""
    results = {
        "timestamp": datetime.now().isoformat(),
        "connection": {"success": False, "message": ""},
        "form": {"found": False, "metadata": None},
        "endpoint": {"accessible": False, "methods": []},
        "issues": [],
        "recommendations": []
    }
    
    connected, msg = test_api_connection(base_url, token)
    results["connection"]["success"] = connected
    results["connection"]["message"] = msg
    
    if not connected:
        results["issues"].append("Cannot connect to Kobo KPI API")
        results["recommendations"].append("Verify server URL and internet connection")
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
        results["recommendations"].append("Check the Asset UID is correct")
        return results
    
    endpoint = check_submission_endpoint(kc_url, token)
    results["endpoint"] = {"accessible": endpoint["accessible"], "url": endpoint["url"]}
    if not endpoint["accessible"]:
        results["issues"].append("OpenRosa /submission endpoint not matching context check")
        results["recommendations"].append("Verify Legacy KoBoCAT Server URL matches server context profiles")
    
    return results

def fetch_form_schema(base_url: str, form_uid: str, token: str) -> Optional[Dict]:
    """Fetch complete form schema with field details and crucial OpenRosa unique tokens."""
    headers = {"Authorization": f"Token {token}"}
    try:
        resp = requests.get(f"{base_url}/api/v2/assets/{form_uid}/", headers=headers)
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
                choice_lists[list_name].append({
                    "name": choice.get("name"),
                    "label": choice.get("label", [choice.get("name")])[0] if isinstance(choice.get("label"), list) else choice.get("label")
                })
            
            fields = []
            skip_types = ["begin_group", "end_group", "begin_repeat", "end_repeat", "note", "calculate", "hidden"]
            
            for item in survey:
                item_type = item.get("type", "")
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
                    "relevant": item.get("relevant"),
                    "constraint": item.get("constraint"),
                    "default": item.get("default"),
                    "hint": item.get("hint"),
                    "appearance": item.get("appearance")
                }
                
                if item_type in ["select_one", "select_multiple"]:
                    list_name = item.get("select_from_list_name")
                    if list_name and list_name in choice_lists:
                        field["choices"] = choice_lists[list_name]
                
                fields.append(field)
            
            return {
                "name": data.get("name"),
                "uid": form_uid,
                "id_string": str(data.get("id_string") or data.get("uid")),
                "version_id": str(data.get("version_id", "v1")),
                "status": data.get("deployment_status"),
                "fields": fields,
                "total_fields": len(fields),
                "required_fields": sum(1 for f in fields if f["required"])
            }
    except Exception as e:
        st.error(f"Schema error: {e}")
    return None

def smart_match_columns(kobo_field: str, csv_columns: List[str]) -> Tuple[Optional[str], float]:
    """Intelligently match Kobo field to CSV column with confidence score."""
    kobo_clean = kobo_field.lower().replace("_", " ").replace("/", " ").strip()
    kobo_words = set(kobo_clean.split())
    best_match = None
    best_score = 0.0
    
    for col in csv_columns:
        col_clean = col.lower().replace("_", " ").replace("/", " ").strip()
        if kobo_clean == col_clean: return col, 1.0
        if kobo_clean in col_clean or col_clean in kobo_clean: return col, 0.9
        
        col_words = set(col_clean.split())
        overlap = len(kobo_words & col_words)
        if overlap > 0:
            score = overlap / max(len(kobo_words), len(col_words))
            if score > best_score:
                best_score = score
                best_match = col
                
        for kw in kobo_words:
            if len(kw) > 2:
                for cw in col_words:
                    if len(cw) > 2 and (kw in cw or cw in kw):
                        score = 0.7
                        if score > best_score:
                            best_score = score
                            best_match = col
    return best_match, best_score

def submit_batch(records: List[Dict], kc_url: str, form_id_string: str, version_id: str, token: str) -> Tuple[int, List[Dict]]:
    """Submit records using OpenRosa Multipart XML Payloads."""
    url = f"{kc_url}/submission"
    headers = {"Authorization": f"Token {token}"}
    success = 0
    errors = []
    
    for i, record in enumerate(records):
        clean = {}
        for k, v in record.items():
            if v is None or (isinstance(v, float) and pd.isna(v)) or str(v).strip() == '' or str(v).strip() == 'None':
                continue
            clean[k] = str(v).strip()
        
        if not clean:
            errors.append({"row": i+1, "error": "All mapped fields are empty or null", "status": "Skipped"})
            continue
            
        xml_data = build_openrosa_xml(clean, form_id_string, version_id)
        files = {
            'xml_submission_file': ('submission.xml', xml_data, 'text/xml')
        }
        
        try:
            resp = requests.post(url, headers=headers, files=files, timeout=30)
            if resp.status_code in [200, 201, 202]:
                success += 1
            else:
                error_entry = {
                    "row": i + 1,
                    "status": resp.status_code,
                    "data": clean,
                    "response": resp.text
                }
                errors.append(error_entry)
        except Exception as e:
            errors.append({
                "row": i + 1,
                "status": "Exception",
                "error": str(e),
                "data": clean
            })
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
    api_token = st.text_input("API Token", placeholder="input your API", type="password")
    form_uid = st.text_input("Form Asset UID", placeholder="Input your Form UID")
    
    st.markdown("---")
    st.header("🔧 Diagnostics")
    
    if st.button("🔍 Run Full Diagnostics", use_container_width=True):
        if not all([kobo_url, kobo_kc_url, api_token, form_uid]):
            st.error("Fill all credential options first")
        else:
            with st.spinner("Running diagnostics..."):
                st.session_state.diagnostics = run_full_diagnostics(kobo_url, kobo_kc_url, form_uid, api_token)
    
    if st.session_state.diagnostics:
        diag = st.session_state.diagnostics
        st.subheader("Results")
        if diag["connection"]["success"]: st.success("✅ API Connected")
        else: st.error("❌ Connection Failed")
        
        if diag["form"]["found"]:
            st.success("✅ Form Found")
            meta = diag["form"]["metadata"]
            if meta: st.info(f"**Name:** {meta['name']}\n\n**Status:** {meta['status']}\n\n**Submissions:** {meta['submissions']}")
        else: st.error("❌ Form Not Found")
        
        if diag["endpoint"]["accessible"]: st.success("✅ Submission Receiver Online")
        else: st.warning("⚠️ Endpoint Context Check Warning")

# ============================================
# MAIN CONTENT
# ============================================
st.title("🔄 KoboToolbox V2 Data Importer")
st.caption("Import Excel/CSV data into KoboToolbox forms via API OpenRosa XML Engine")

# Step 1: Upload
st.header("📂 Step 1: Upload Dataset")
uploaded_file = st.file_uploader("Choose Excel (.xlsx) or CSV file", type=["csv", "xlsx"])

if uploaded_file:
    try:
        df = pd.read_csv(uploaded_file) if uploaded_file.name.endswith('.csv') else pd.read_excel(uploaded_file, engine='openpyxl')
        st.session_state.df = df
        
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Rows", len(df))
        col2.metric("Columns", len(df.columns))
        col3.metric("Missing Values", df.isnull().sum().sum())
        col4.metric("Complete Rows", df.dropna().shape[0])
        
        with st.expander("👁️ Data Preview"):
            st.dataframe(df.head(10), use_container_width=True)
            st.markdown("**Column Names:**")
            st.code(", ".join(df.columns.tolist()))
    except Exception as e:
        st.error(f"❌ File Error: {str(e)}")
        st.stop()

# Step 2: Schema & Mapping
if st.session_state.df is not None and all([kobo_url, api_token, form_uid]):
    st.markdown("---")
    st.header("🔀 Step 2: Field Mapping")
    
    if st.button("📋 Fetch Form Schema", type="primary"):
        with st.spinner("Fetching form structure..."):
            st.session_state.schema = fetch_form_schema(kobo_url, form_uid, api_token)
            
    if st.session_state.schema:
        schema = st.session_state.schema
        st.sidebar.markdown("---")
        st.sidebar.subheader("⚙️ XML Matching Keys")
        final_id_string = st.sidebar.text_input("Form XML ID String", value=schema['id_string'])
        
        st.success(f"**{schema['name']}** — {schema['total_fields']} fields ({schema['required_fields']} required) | XML Identifier: `{final_id_string}`")
        if schema['status'] != 'deployed':
            st.error("⚠️ Form not deployed! Deploy it in KoboToolbox before importing.")
            
        csv_cols = list(st.session_state.df.columns)
        mapping = {}
        
        col1, col2, col3, col4 = st.columns([2, 3, 1, 1])
        col1.markdown("**Kobo Field**")
        col2.markdown("**CSV Column**")
        col3.markdown("**Match**")
        col4.markdown("**Required**")
        
        auto_matches = 0
        for field in schema['fields']:
            col1, col2, col3, col4 = st.columns([2, 3, 1, 1])
            with col1:
                st.markdown(f"`{field['name']}`")
                st.caption(f"{field['type']}")
            with col2:
                matched, confidence = smart_match_columns(field['name'], csv_cols)
                default_idx = csv_cols.index(matched) + 1 if matched else 0
                sel = st.selectbox(f"Map {field['name']}", ["-- Skip --"] + csv_cols, index=default_idx if default_idx <= len(csv_cols) else 0, key=f"map_{field['name']}", label_visibility="collapsed")
                if sel != "-- Skip --": mapping[sel] = field['name']
            with col3:
                if matched:
                    st.progress(confidence, text=f"{confidence:.0%}")
                    if confidence > 0.7: auto_matches += 1
            with col4:
                if field['required']: st.markdown('<span class="field-required">Required</span>', unsafe_allow_html=True)
                    
        st.session_state.mapping = mapping
        if auto_matches > 0: st.info(f"🤖 Auto-matched {auto_matches}/{len(schema['fields'])} fields")
            
        # Step 3: Import
        st.markdown("---")
        st.header("🚀 Step 3: Import Data")
        
        col1, col2 = st.columns(2)
        # REFACTORED: Test mode defaults to False to push all data natively
        with col1: test_mode = st.checkbox("Test mode (Import only first 5 rows)", value=False)
        
        if mapping:
            required_fields = [f for f in schema['fields'] if f['required']]
            required_mapped = sum(1 for f in required_fields if f['name'] in mapping.values())
            st.info(f"**Mapping Summary:**\n- Total mapped: {len(mapping)}/{schema['total_fields']}\n- Required mapped: {required_mapped}/{len(required_fields)}\n- Records to import: {5 if test_mode else len(st.session_state.df)}")
            
        if st.button("▶️ Start Import", type="primary", disabled=not mapping, use_container_width=True):
            mapped_df = st.session_state.df[list(mapping.keys())].copy()
            mapped_df.rename(columns=mapping, inplace=True)
            if test_mode: mapped_df = mapped_df.head(5)
                
            mapped_df = mapped_df.astype(str).replace('nan', None)
            records = mapped_df.to_dict('records')
            
            progress_bar = st.progress(0)
            status_text = st.empty()
            
            success, errors = submit_batch(records, kobo_kc_url, final_id_string, schema['version_id'], api_token)
            
            progress_bar.progress(1.0)
            status_text.empty()
            
            col1, col2, col3 = st.columns(3)
            col1.metric("✅ Success", success)
            col2.metric("❌ Failed", len(errors))
            col3.metric("📊 Total", len(records))
            
            if success > 0: st.balloons()
            if errors:
                st.warning(f"⚠️ {len(errors)} records had server processing issues")
                with st.expander("🔍 Error Details", expanded=True):
                    for i, err in enumerate(errors):
                        st.markdown(f"### Row {err['row']} — Status: {err.get('status', 'N/A')}")
                        st.code(err.get('response', err.get('error', 'Unknown Error')))
                        if i < len(errors) - 1: st.markdown("---")
                        
                error_df = pd.DataFrame([{'Row': e['row'], 'Status': e.get('status'), 'Error': str(e.get('response', e.get('error', '')))[:300]} for e in errors])
                st.download_button("📥 Download Error Report", error_df.to_csv(index=False), "kobo_import_errors.csv", "text/csv")

# ============================================
# FOOTER
# ============================================
st.markdown("---")
st.caption("KoboToolbox V2 Importer | Built with Streamlit | OpenRosa XML Ingestion Pipeline")