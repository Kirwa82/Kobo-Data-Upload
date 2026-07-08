# KoboToolbox V2 Importer

A Streamlit application for bulk-importing Excel/CSV data into deployed KoboToolbox forms using the OpenRosa XML submission protocol. It handles schema discovery, intelligent field mapping, and batch submission with error reporting.

---

## 1. Installation & Requirements

### Prerequisites

- Python 3.9+
- A deployed KoboToolbox form (status must be `deployed`, not draft)
- Your KoboToolbox **API Token**
- Your form's **Asset UID**
- Access to both the KPI server (e.g. `https://kf.kobotoolbox.org`) and the legacy KoBoCAT server (e.g. `https://kc.kobotoolbox.org`), or your organization's equivalent self-hosted URLs

### Step 1 — Install Python dependencies

```bash
pip install streamlit pandas requests openpyxl
```

### Step 2 — Save and run the app

1. Save the app script as `app.py` (or your preferred filename).
2. Run it:

```bash
streamlit run app.py
```

3. Streamlit will open the app in your browser (default: `http://localhost:8501`).

## 2. Features

- **Connection diagnostics** — verifies API connectivity, form existence, deployment status, and OpenRosa submission endpoint accessibility before you import anything.
- **Schema fetching** — pulls the live form structure (fields, types, choice lists, required flags) directly from KoboToolbox.
- **Smart column matching** — automatically suggests CSV column → Kobo field mappings based on name similarity, with a confidence score per match.
- **Manual mapping override** — every field can be remapped or skipped via dropdown.
- **Test mode** — optionally import only the first 5 rows to validate mappings before a full run.
- **Batch submission** — converts each row into a proper OpenRosa XML payload (including a unique `instanceID`) and posts it to the submission endpoint.
- **Error reporting** — failed rows are shown inline and can be downloaded as a CSV report.

---

## 3. Step-by-Step Usage

### Step 0 — Enter credentials (sidebar)

In the left sidebar, fill in:

| Field | Description |
|---|---|
| **Server URL (KPI)** | Your KoboToolbox KPI API base URL |
| **Legacy Server URL (KoBoCAT)** | The OpenRosa submission server URL |
| **API Token** | Your personal Kobo API token (kept masked as a password field) |
| **Form Asset UID** | The unique ID of the target form |

### Step 1 — Run diagnostics (recommended)

Click **🔍 Run Full Diagnostics**. This checks:
- ✅ API connectivity
- ✅ Form exists and its deployment status
- ✅ Submission endpoint is reachable

Resolve any ❌ or ⚠️ results before proceeding — a common issue is trying to import into a form that hasn't been deployed yet.

### Step 2 — Upload your dataset

Under **📂 Step 1: Upload Dataset**, upload a `.csv` or `.xlsx` file. The app will show:
- Row and column counts
- Missing value count
- Complete row count
- A preview of the first 10 rows and the full column list

### Step 3 — Fetch the form schema

Once both credentials and a dataset are present, click **📋 Fetch Form Schema** under **🔀 Step 2: Field Mapping**. This retrieves all importable fields (skipping groups, notes, and calculated fields) along with their types, choice lists, and required status.

### Step 4 — Map your columns to Kobo fields

For each Kobo field, the app auto-suggests the best-matching CSV column with a confidence percentage. You can:
- Accept the suggested match
- Choose a different column from the dropdown
- Select **"-- Skip --"** to leave a field unmapped

Required fields are flagged in red. A summary shows how many required fields are mapped.

You can also override the **Form XML ID String** in the sidebar if the auto-detected `id_string` doesn't match your form's submission identifier.

### Step 5 — Import

Under **🚀 Step 3: Import Data**:
1. Optionally enable **Test mode** to import only the first 5 rows first.
2. Review the mapping summary (total mapped / required mapped / records to import).
3. Click **▶️ Start Import**.

Each row is converted to an OpenRosa XML payload and submitted individually. On completion you'll see:
- ✅ Success count
- ❌ Failed count
- 📊 Total processed

### Step 6 — Review errors (if any)

Failed submissions are listed with their HTTP status and server response. You can download a CSV error report via **📥 Download Error Report** for offline review or re-import after correction.

---

## How It Works (Technical Notes)

- Each row is serialized into an XML `<data>` element with the form's `id` and `version` attributes, then submitted as a multipart file (`xml_submission_file`) to the `/submission` endpoint — this is the standard OpenRosa/ODK submission protocol used by KoboToolbox.
- Empty, `None`, or NaN values are excluded from the payload so they don't overwrite defaults or fail constraints unnecessarily.
- A unique `instanceID` (UUID) is generated per submission to satisfy OpenRosa's deduplication requirement.
- Column matching uses a mix of exact match, substring match, and word-overlap scoring — always double-check the suggested mappings before importing, especially for similarly named fields.

---

## Troubleshooting

| Symptom | Likely Cause |
|---|---|
| "Cannot connect to Kobo KPI API" | Wrong server URL, network issue, or invalid token |
| "Form not found" | Incorrect Asset UID |
| "Form not deployed" | Form is still a draft in KoboToolbox |
| "OpenRosa /submission endpoint not matching context check" | Legacy KoBoCAT URL is wrong or doesn't match your server's deployment context |
| Rows fail during import | Check the error report — often a required field is unmapped, or a value fails a form constraint |

---

## Disclaimer

This tool submits data directly into a live KoboToolbox form. Always test with a small batch (**Test mode**) before running a full import, and confirm you're pointing at the correct form and environment (production vs. staging).
