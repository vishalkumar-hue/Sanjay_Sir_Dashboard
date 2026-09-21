"""
build_data.py
--------------
Google Sheet (Payment Report) ka CSV padhta hai, cleaning karta hai, aur
ek JSON banata hai jo HTML dashboard me embed hoti hai.

Do tarah se chalta hai:

1) Streamlit app (Streamlit Cloud par deploy):
       streamlit run build_data.py
   -> Google Sheet ke "Compile Report" tab se seedha data leta hai aur
      dashboard seedha khol deta hai (koi upload / sidebar nahi).
      Dashboard ki .html file repo me isi file ke saath honi chahiye.

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


def normalize_columns(raw):
    df = raw.copy()
    df.columns = [str(c).strip() for c in df.columns]
    df = df.rename(columns={
        "Expence Type": "Expense Type",
        "Expense type": "Expense Type",
        "Subtotal After Deduction": "Amount",
    })
    missing = [c for c in BASE_COLS if c not in df.columns]
    if missing:
        raise ValueError("Ye columns nahi mile: " + ", ".join(missing))
    return df[BASE_COLS].copy()


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

    trunc_pat = r"(?:\.{2,}|…)\s*$"
    issues["Truncated project codes fixed ('...')"] = int(df["Project Code"].str.contains(trunc_pat, regex=True).sum())
    df["Project Code"] = df["Project Code"].str.replace(trunc_pat, "", regex=True).str.strip()

    parts = df["Project Code"].str.split("/", expand=True).reindex(columns=range(5))
    df["Client"] = parts[0]
    df["Location"] = parts[1]
    df["Category"] = parts[3]
    df["Entity"] = parts[4]
    for c in ["Client", "Location", "Category", "Entity"]:
        df[c] = df[c].fillna("").astype(str).str.strip().replace("", "Unknown")

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
    issues["Zero-amount rows"] = int((df["Amount"] == 0).sum())
    issues["Exact duplicate rows"] = int(df.duplicated().sum())
    return df.reset_index(drop=True), issues


# ---------------------------------------------------------------- helpers
def to_sheet_csv_url(s):
    """Google Sheet link ko CSV export URL me badalta hai."""
    s = s.strip()
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", s)
    if not m:
        return s
    sheet_id = m.group(1)
    gid_m = re.search(r"[?#&]gid=([0-9]+)", s)
    gid = gid_m.group(1) if gid_m else "0"
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}"


def sheet_csv_url(link, tab=None, gid=None):
    """Google Sheet link + tab ka naam (ya gid) -> CSV URL."""
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
        rows.append({
            "sheetName": r["Sheet Name"],
            "financialYear": r["Financial Year"],
            "quarter": r["Quarter"],
            "month": r["Month"],
            "monthKey": r["Month Key"],
            "projectCode": r["Project Code"],
            "client": r["Client"],
            "location": r["Location"],
            "category": r["Category"],
            "entity": r["Entity"],
            "expenseType": r["Expense Type"],
            "budget": float(r["Budget"]),
            "amount": float(r["Amount"]),
            "startDate": None if pd.isna(r["Start Date"]) else r["Start Date"].strftime("%Y-%m-%d"),
        })
    month_order = sorted(df.loc[df["Month Key"] != "Unknown", "Month Key"].unique().tolist())
    return {
        "rows": rows,
        "monthOrder": month_order,
        "issues": issues,
        "loadedAt": pd.Timestamp.now().strftime("%d %b %Y, %H:%M"),
    }


def inject_into_html(html, payload):
    """Dashboard HTML ke ROWS / MONTH_ORDER / ISSUES / LOADED_AT lines replace karta hai."""
    def sub_line(text, name, value_js):
        pat = re.compile(r"^const " + name + r" = .*;[ \t]*$", re.M)
        if not pat.search(text):
            raise ValueError(f"HTML me 'const {name} = ...;' line nahi mili")
        return pat.sub(lambda m: f"const {name} = {value_js};", text, count=1)

    html = sub_line(html, "ROWS", json.dumps(payload["rows"]))
    html = sub_line(html, "MONTH_ORDER", json.dumps(payload["monthOrder"]))
    html = sub_line(html, "ISSUES", json.dumps(payload["issues"]))
    html = sub_line(html, "LOADED_AT", json.dumps(payload["loadedAt"]))
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
TEMPLATE_FILE = "dashboard_template.html"   # optional; na ho to repo ki koi bhi dashboard .html mil jaati hai
CACHE_SECONDS = 600                         # itni der tak sheet dobara nahi padhta


def find_template():
    """Repo me dashboard HTML dhundta hai (jisme 'const ROWS = ' line ho)."""
    here = Path(__file__).resolve().parent
    preferred = here / TEMPLATE_FILE
    if preferred.exists():
        return preferred.read_text(encoding="utf-8")
    for p in sorted(here.rglob("*.htm*")):
        try:
            t = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if re.search(r"^const ROWS = ", t, re.M):
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

    @st.cache_data(ttl=CACHE_SECONDS, show_spinner="Sheet se data la raha hoon...")
    def load_payload(url):
        raw = pd.read_csv(url, dtype=str)
        df, issues = clean_data(raw)
        return build_payload(df, issues)

    template = find_template()
    if template is None:
        st.error("Dashboard ki HTML file repo me nahi mili. Apni dashboard .html file GitHub repo me "
                 "build_data.py ke saath daal do (ya uska naam dashboard_template.html rakh do).")
        return

    url = sheet_csv_url(DEFAULT_SHEET_LINK, DEFAULT_TAB)
    try:
        payload = load_payload(url)
    except Exception as e:
        st.error(
            f"Sheet se data nahi la paya: {e}\n\n"
            "Check karein: sheet 'Anyone with the link - Viewer' par shared ho, tab ka naam "
            f"'{DEFAULT_TAB}' sahi ho, aur tab me ye columns hon: " + ", ".join(BASE_COLS)
        )
        return

    try:
        html = inject_into_html(template, payload)
    except Exception as e:
        st.error(f"Dashboard HTML me data daal nahi paya: {e}")
        return

    # naye Streamlit me st.iframe, purane me components.html
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
