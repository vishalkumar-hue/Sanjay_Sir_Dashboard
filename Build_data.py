"""
build_data.py
--------------
Reads the Google Sheet (Payment Report) CSV, cleans it, and builds a
JSON payload that gets embedded into the HTML dashboard.

Can be run in two ways:

1) Streamlit app (deployed on Streamlit Cloud):
       streamlit run build_data.py
   -> Reads data directly from the "Compile Report" tab of the Google
      Sheet and opens the dashboard directly (no upload / sidebar).
      The dashboard's .html file must sit alongside this file in the repo
      (name it dashboard_template.html).

2) Command line:
       python3 build_data.py <csv_path_or_sheet_link> <output_json_path> ["Tab Name"]
"""
import sys
import json
import re
from pathlib import Path
from urllib.parse import quote
import pandas as pd

TEXT_COLS = ["Financial Year", "Quarter", "Month", "Sheet Name", "Project Code", "Expense Type"]
BASE_COLS = TEXT_COLS + ["Budget", "Amount"]


# ---------------------------------------------------------------- cleaning
def to_number(series):
    s = series.fillna("").astype(str).str.strip()
    negative = s.str.match(r"^\(.*\)$")
    s = s.str.replace(r"[₹,\s()]", "", regex=True)
    num = pd.to_numeric(s, errors="coerce")
    return num.where(~negative, -num)


def fy_of(d):
    if pd.isna(d):
        return ""
    start = d.year if d.month >= 4 else d.year - 1
    return f"{start}-{str(start + 1)[-2:]}"


def quarter_of(d):
    if pd.isna(d):
        return ""
    return f"Q{((d.month - 4) % 12) // 3 + 1}"



def expense_group(v):
    """Maps ~60 messy expense-type labels to a small set of readable groups."""
    t = str(v).strip().lower()
    if not t:
        return "Not Specified"
    rules = [
        ("Food & Catering", ["food", "catering", "lunch", "snack", "beverage"]),
        ("Internet & Broadband", ["internet", "broadband"]),
        ("Manpower & Labour", ["manpower", "labour", "labor", "waiter", "serving", "helping"]),
        ("Purchase - Stock", ["stock", "inventory"]),
        ("Purchase - Consumables", ["consumable"]),
        ("Purchase - Project (Local/Central)", ["purchase"]),
        ("Service (Fixed/Variable/Technical)", ["service"]),
        ("Fit-out, Furniture & Panels", ["panel", "table", "carpet", "plant", "pvc", "partition",
                                          "wooden", "ply", "acrylic", "decorative"]),
        ("Equipment (TV/UPS/Fridge)", ["tv", "ups", "refrig"]),
    ]
    for name, keys in rules:
        if any(k in t for k in keys):
            return name
    return "Others"


def normalize_columns(raw):
    df = raw.copy()
    df.columns = [str(c).strip() for c in df.columns]
    df = df.rename(columns={
        "Expence Type": "Expense Type",
        "Expense type": "Expense Type",
        "Subtotal After Deduction": "Amount",
        "Overall Revenew": "Revenue",
        "Overall Revenue": "Revenue",
    })
    missing = [c for c in BASE_COLS if c not in df.columns]
    if missing:
        raise ValueError("Missing columns: " + ", ".join(missing))
    if "Revenue" not in df.columns:
        df["Revenue"] = 0
    return df[BASE_COLS + ["Revenue"]].copy()


def clean_data(raw):
    df = normalize_columns(raw)
    issues = {}

    for c in TEXT_COLS:
        df[c] = df[c].fillna("").astype(str).str.strip()

    is_header = df["Project Code"].str.lower().eq("project code")
    is_blank = df["Project Code"].eq("")
    issues["Repeated header rows removed"] = int(is_header.sum())
    issues["Blank project-code rows removed"] = int(is_blank.sum())
    df = df[~(is_header | is_blank)].copy()

    df["Amount"] = to_number(df["Amount"])
    df["Budget"] = to_number(df["Budget"])
    issues["Non-numeric amount (treated as 0)"] = int(df["Amount"].isna().sum())
    df["Amount"] = df["Amount"].fillna(0.0)
    df["Budget"] = df["Budget"].fillna(0.0)
    df["Revenue"] = to_number(df["Revenue"]).fillna(0.0)

    trunc_pat = r"(?:\.{2,}|…)\s*$"
    issues["Truncated project codes fixed ('...')"] = int(df["Project Code"].str.contains(trunc_pat, regex=True).sum())
    df["Project Code"] = df["Project Code"].str.replace(trunc_pat, "", regex=True).str.strip()

    # normalise project-code variants (case, spaces, trailing '.', VOIP vs VO-IP) so one project = one code
    df["Project Code"] = (df["Project Code"].str.upper()
                          .str.replace(r"\s+", "", regex=True)
                          .str.rstrip(".")
                          .str.replace("/VOIP/", "/VO-IP/", regex=False))

    df["Expense Type Original"] = df["Expense Type"]
    df["Expense Type"] = df["Expense Type"].apply(expense_group)

    parts = df["Project Code"].str.split("/", expand=True).reindex(columns=range(5))
    df["Client"] = parts[0]
    df["Location"] = parts[1]
    df["Category"] = parts[3]
    df["Entity"] = parts[4]
    for c in ["Client", "Location", "Category", "Entity"]:
        df[c] = df[c].fillna("").astype(str).str.strip().replace("", "Unknown")
    df["Client"] = df["Client"].str.replace(r"^RAILTEL.*$", "RAILTEL", regex=True)   # RAILTEL-UP / -DELHI / -HARYANA -> RAILTEL

    start_raw = parts[2].fillna("").astype(str).str.strip()
    df["Start Date"] = pd.to_datetime(start_raw, format="%d%m%y", errors="coerce")
    issues["Project codes with invalid date part"] = int(df["Start Date"].isna().sum())

    df["Month Date"] = pd.to_datetime(df["Month"], format="%b_%Y", errors="coerce")
    issues["Rows without valid Month"] = int(df["Month Date"].isna().sum())

    miss_fy = df["Financial Year"].eq("")
    miss_q = df["Quarter"].eq("")
    issues["FY/Quarter blank, filled from Month"] = int(((miss_fy | miss_q) & df["Month Date"].notna()).sum())
    df.loc[miss_fy, "Financial Year"] = df.loc[miss_fy, "Month Date"].apply(fy_of)
    df.loc[miss_q, "Quarter"] = df.loc[miss_q, "Month Date"].apply(quarter_of)
    for c in ["Financial Year", "Quarter", "Sheet Name", "Expense Type"]:
        df[c] = df[c].replace("", "Unknown")

    df["Month Key"] = df["Month Date"].dt.strftime("%Y-%m").fillna("Unknown")

    issues["Rows with a budget value"] = int((df["Budget"] != 0).sum())
    issues["Rows with a revenue value"] = int((df["Revenue"] != 0).sum())
    issues["Zero-amount rows"] = int((df["Amount"] == 0).sum())
    issues["Exact duplicate rows"] = int(df.duplicated().sum())
    return df.reset_index(drop=True), issues


# ---------------------------------------------------------------- helpers
def to_sheet_csv_url(s):
    """Converts a Google Sheet link into a CSV export URL."""
    s = s.strip()
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", s)
    if not m:
        return s
    sheet_id = m.group(1)
    gid_m = re.search(r"[?#&]gid=([0-9]+)", s)
    gid = gid_m.group(1) if gid_m else "0"
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}"


def sheet_csv_url(link, tab=None, gid=None):
    """Converts a Google Sheet link + tab name (or gid) -> CSV URL."""
    link = link.strip()
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", link)
    if not m:
        return link
    sid = m.group(1)
    if gid:
        return f"https://docs.google.com/spreadsheets/d/{sid}/export?format=csv&gid={gid}"
    if tab:
        return f"https://docs.google.com/spreadsheets/d/{sid}/gviz/tq?tqx=out:csv&sheet={quote(tab)}"
    return to_sheet_csv_url(link)


def build_payload(df, issues):
    rows = []
    for _, r in df.iterrows():
        start = None if pd.isna(r["Start Date"]) else r["Start Date"].strftime("%Y-%m-%d")
        rows.append({
            "sheetName": r["Sheet Name"],
            "fy": r["Financial Year"],
            "quarter": r["Quarter"],
            "month": r["Month"],
            "monthKey": r["Month Key"],
            "projectCode": r["Project Code"],
            "client": r["Client"],
            "location": r["Location"],
            "projectType": r["Category"],
            "entity": r["Entity"],
            "expenseType": r["Expense Type"],
            "budget": float(r["Budget"]),
            "subtotal": float(r["Amount"]),
            "revenue": float(r["Revenue"]),
            "startDate": start,
            "allColumns": {
                "Financial Year": r["Financial Year"],
                "Quarter": r["Quarter"],
                "Month": r["Month"],
                "Sheet Name": r["Sheet Name"],
                "Project Code": r["Project Code"],
                "Client": r["Client"],
                "Location": r["Location"],
                "Category": r["Category"],
                "Entity": r["Entity"],
                "Expense Type": r["Expense Type"],
                "Expense Type (Original)": r["Expense Type Original"],
                "Budget": float(r["Budget"]),
                "Expense": float(r["Amount"]),
                "Revenue": float(r["Revenue"]),
            },
        })
    month_order = (df.loc[df["Month Key"] != "Unknown", ["Month", "Month Key"]]
                     .drop_duplicates().sort_values("Month Key")["Month"].tolist())
    return {
        "rows": rows,
        "monthOrder": month_order,
        "allHeaders": list(rows[0]["allColumns"].keys()) if rows else [],
        "issues": issues,
        "loadedAt": pd.Timestamp.now().strftime("%d %b %Y, %H:%M"),
    }


def inject_into_html(html, payload):
    """Replaces the __PLACEHOLDER__ tokens in the dashboard HTML."""
    heads = payload["allHeaders"]
    reps = {
        "__ROWS_JSON__": json.dumps(payload["rows"]),
        "__MONTH_ORDER_JSON__": json.dumps(payload["monthOrder"]),
        "__ALL_HEADERS_JSON__": json.dumps(heads),
        "__DEFAULT_COLUMNS_JSON__": json.dumps(heads),
        "__PERIOD_LABEL__": "Loaded " + payload["loadedAt"],
    }
    for k, v in reps.items():
        if k not in html:
            raise ValueError(f"Placeholder {k} not found in the HTML")
        html = html.replace(k, v)
    return html


# ---------------------------------------------------------------- CLI mode
def run_cli():
    if len(sys.argv) < 3:
        print(__doc__)
        print("Usage: python3 build_data.py <csv_path_or_sheet_link> <output_json_path> [\"Tab Name\"]")
        sys.exit(1)
    src = sys.argv[1]
    out_path = sys.argv[2]
    tab = sys.argv[3] if len(sys.argv) > 3 else None
    url = sheet_csv_url(src, tab) if "docs.google.com" in src else src
    raw = pd.read_csv(url, dtype=str)
    df, issues = clean_data(raw)
    payload = build_payload(df, issues)
    with open(out_path, "w") as f:
        json.dump(payload, f)
    print(f"Wrote {len(payload['rows'])} rows to {out_path}")


# ---------------------------------------------------------------- Streamlit mode
DEFAULT_SHEET_LINK = "https://docs.google.com/spreadsheets/d/1P8awjtc-dwxCce1WJLDixljqL37yqCxnOe5QZ75_gIw/edit?gid=0#gid=0"
DEFAULT_TAB = "Compile Report"
TEMPLATE_FILE = "dashboard_template.html"   # optional; if absent, any dashboard .html found in the repo is used
CACHE_SECONDS = 600                         # sheet is not re-read again within this window


def find_template():
    """Looks for the dashboard HTML in the repo (the one containing a '__ROWS_JSON__' placeholder)."""
    here = Path(__file__).resolve().parent
    preferred = here / TEMPLATE_FILE
    if preferred.exists():
        return preferred.read_text(encoding="utf-8")
    for p in sorted(here.rglob("*.htm*")):
        try:
            t = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if "__ROWS_JSON__" in t:
            return t
    return None


def run_streamlit():
    import streamlit as st

    st.set_page_config(page_title="Payment Report Dashboard", layout="wide",
                       initial_sidebar_state="collapsed")
    st.markdown(
        """<style>
        [data-testid="stSidebar"], [data-testid="collapsedControl"],
        [data-testid="stSidebarCollapsedControl"], footer {display:none !important;}
        .block-container {padding:2.5rem 0 0 0 !important; max-width:100% !important;}
        iframe {height:calc(100vh - 3rem) !important;}
        </style>""",
        unsafe_allow_html=True,
    )

    @st.cache_data(ttl=CACHE_SECONDS, show_spinner="Fetching data from the sheet...")
    def load_payload(url):
        raw = pd.read_csv(url, dtype=str)
        df, issues = clean_data(raw)
        return build_payload(df, issues)

    template = find_template()
    if template is None:
        st.error("Could not find the dashboard's HTML file in the repo. Please add your dashboard "
                 ".html file to the GitHub repo alongside build_data.py "
                 "(or name it dashboard_template.html).")
        return

    url = sheet_csv_url(DEFAULT_SHEET_LINK, DEFAULT_TAB)
    try:
        payload = load_payload(url)
    except Exception as e:
        st.error(
            f"Could not fetch data from the sheet: {e}\n\n"
            "Please check: the sheet is shared as 'Anyone with the link - Viewer', the tab name "
            f"is correctly '{DEFAULT_TAB}', and the tab contains these columns: " + ", ".join(BASE_COLS)
        )
        return

    try:
        html = inject_into_html(template, payload)
    except Exception as e:
        st.error(f"Could not inject data into the dashboard HTML: {e}")
        return

    # st.iframe on newer Streamlit, components.html on older versions
    if hasattr(st, "iframe"):
        st.iframe(html, height=1000)
    else:
        import streamlit.components.v1 as components
        components.html(html, height=1000, scrolling=True)


# ---------------------------------------------------------------- entry point
def _running_in_streamlit():
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx
        return get_script_run_ctx() is not None
    except Exception:
        return False


if __name__ == "__main__":
    if _running_in_streamlit():
        run_streamlit()
    else:
        run_cli()
