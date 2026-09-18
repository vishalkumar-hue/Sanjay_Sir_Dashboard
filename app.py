"""
Expense Dashboard - Live Streamlit App
Fetches live data from the "Compile Report" tab of the Sanjay Jha Report
Google Sheet, cleans it, derives Client/Entity/Project Type from the
Project Code, and sends row-level data to the Chart.js HTML/CSS dashboard,
which does all filtering + aggregation client-side across ALL tabs
(Overview, Monthly Trend, Expense Type Analysis, Client Analysis,
Sheet-wise Analysis, Quarter Comparison, Comparison, All Entries).
"""

import json
import re
import urllib.parse
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

# ----------------------------------------------------------------------
# CONFIG
# ----------------------------------------------------------------------
DEFAULT_SHEET_ID = "1P8awjtc-dwxCce1WJLDixljqL37yqCxnOe5QZ75_gIw"
DEFAULT_SHEET_NAME = "Compile Report"
TEMPLATE_PATH = Path(__file__).parent / "assets" / "dashboard_template.html"

st.set_page_config(page_title="Expense Dashboard", layout="wide", page_icon="💸", initial_sidebar_state="collapsed")
st.markdown("""
<style>
.stApp{background:#0b1220;}
[data-testid="collapsedControl"]{display:none;}
section[data-testid="stSidebar"]{display:none;}
div.block-container{padding-top:3.5rem;}
div.stButton > button{
  background:#17233a; color:#e7ecf5; border:1px solid #223252; border-radius:6px;
}
div.stButton > button:hover{border-color:#d9a441; color:#d9a441;}
</style>
""", unsafe_allow_html=True)

sheet_id = DEFAULT_SHEET_ID
sheet_name = DEFAULT_SHEET_NAME

_, refresh_col = st.columns([8, 1])
with refresh_col:
    if st.button("🔄 Refresh"):
        st.cache_data.clear()
        st.rerun()

# ----------------------------------------------------------------------
# DATA LOADING
# ----------------------------------------------------------------------
def build_csv_url(sheet_id: str, sheet_name: str) -> str:
    encoded_name = urllib.parse.quote(sheet_name)
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?tqx=out:csv&sheet={encoded_name}"


@st.cache_data(ttl=60, show_spinner="Geting Data From Google Sheet...")
def load_raw_data(sheet_id: str, sheet_name: str) -> pd.DataFrame:
    url = build_csv_url(sheet_id, sheet_name)
    df = pd.read_csv(url)
    df.columns = [c.strip() for c in df.columns]
    return df


def clean_numeric(series: pd.Series) -> pd.Series:
    if series.dtype.kind in "if":
        return series.astype(float)
    cleaned = (
        series.astype(str)
        .str.replace(r"[₹,%\s]", "", regex=True)
        .replace({"": None, "nan": None, "None": None, "-": None})
    )
    return pd.to_numeric(cleaned, errors="coerce")


# ----------------------------------------------------------------------
# COLUMN RESOLUTION
# ----------------------------------------------------------------------
def find_col(df: pd.DataFrame, target: str):
    def normalize(s: str) -> str:
        return "".join(ch for ch in s.strip().lower() if ch.isalnum())

    target_norm = normalize(target)
    for c in df.columns:
        if normalize(c) == target_norm:
            return c
    return None


COLUMN_TARGETS = {
    "FY": ["Financial Year", "FY"],
    "Quarter": ["Quarter", "Quater", "Qtr"],
    "Month": ["Month"],
    "SheetName": ["Sheet Name"],
    "ProjectCode": ["Project Code"],
    "Budget": ["Budget"],
    "ExpenseType": ["Expence Type", "Expense Type"],
    "Subtotal": ["Subtotal After Deduction"],
}


def resolve_columns(df: pd.DataFrame):
    resolved = {}
    for logical, candidates in COLUMN_TARGETS.items():
        found = None
        for cand in candidates:
            found = find_col(df, cand)
            if found:
                break
        resolved[logical] = found
    return resolved


# ----------------------------------------------------------------------
# JUNK-ROW FILTER
# ----------------------------------------------------------------------
JUNK_MARKERS = {"project code", "nature of expense", "expence type", "expense type", "sheet name"}


def is_junk_row(project_code: str, expense_type: str) -> bool:
    pc = str(project_code).strip().lower()
    et = str(expense_type).strip().lower()
    return pc in JUNK_MARKERS or et in JUNK_MARKERS


# ----------------------------------------------------------------------
# PROJECT CODE PARSING -> Client / Entity / Project Type
# ----------------------------------------------------------------------
def parse_project_code(code: str):
    code = str(code).strip()
    if not code or code.lower() == "nan":
        return "", "", ""
    parts = code.split("/")
    client = parts[0].strip() if parts else code
    entity = parts[-1].strip() if len(parts) >= 3 else ""
    project_type = parts[-2].strip() if len(parts) >= 4 else ""
    return client, project_type, entity


def prepare_data(raw: pd.DataFrame):
    df = raw.copy()
    cols = resolve_columns(df)

    pc_col = cols.get("ProjectCode")
    et_col = cols.get("ExpenseType")
    budget_col = cols.get("Budget")
    subtotal_col = cols.get("Subtotal")

    if pc_col and et_col:
        junk_mask = df.apply(lambda r: is_junk_row(r[pc_col], r[et_col]), axis=1)
        df = df[~junk_mask]

    if pc_col:
        df = df[df[pc_col].astype(str).str.strip().replace({"nan": ""}) != ""]

    if budget_col:
        df[budget_col] = clean_numeric(df[budget_col])
    if subtotal_col:
        df[subtotal_col] = clean_numeric(df[subtotal_col])

    for logical in ["FY", "Quarter", "Month", "SheetName", "ProjectCode", "ExpenseType"]:
        c = cols.get(logical)
        if c:
            df[c] = df[c].astype(str).str.strip().replace({"nan": ""})

    if pc_col:
        parsed = df[pc_col].apply(parse_project_code)
        df["_Client"] = parsed.apply(lambda t: t[0])
        df["_ProjectType"] = parsed.apply(lambda t: t[1])
        df["_Entity"] = parsed.apply(lambda t: t[2])
    else:
        df["_Client"] = ""
        df["_ProjectType"] = ""
        df["_Entity"] = ""

    return df, cols


def month_sort_key(m: str):
    for fmt in ("%b_%Y", "%b_%y"):
        try:
            return datetime.strptime(m, fmt)
        except Exception:
            continue
    return datetime.max


def _series_or_blank(df, cols, logical, length):
    col = cols.get(logical)
    if col:
        return df[col].fillna("").astype(str)
    return pd.Series([""] * length, index=df.index)


def build_rows(df: pd.DataFrame, cols: dict):
    n = len(df)
    budget = df[cols["Budget"]] if cols.get("Budget") else pd.Series([0.0] * n, index=df.index)
    subtotal = df[cols["Subtotal"]] if cols.get("Subtotal") else pd.Series([0.0] * n, index=df.index)

    all_cols_df = df.drop(columns=["_Client", "_ProjectType", "_Entity"], errors="ignore")
    all_cols_df = all_cols_df.where(all_cols_df.notna(), "")
    all_columns_records = all_cols_df.to_dict("records")

    out = pd.DataFrame({
        "fy": _series_or_blank(df, cols, "FY", n),
        "quarter": _series_or_blank(df, cols, "Quarter", n),
        "month": _series_or_blank(df, cols, "Month", n),
        "sheetName": _series_or_blank(df, cols, "SheetName", n),
        "projectCode": _series_or_blank(df, cols, "ProjectCode", n),
        "client": df["_Client"],
        "projectType": df["_ProjectType"],
        "entity": df["_Entity"],
        "expenseType": _series_or_blank(df, cols, "ExpenseType", n),
        "budget": budget.fillna(0),
        "subtotal": subtotal.fillna(0),
    })
    out["allColumns"] = pd.Series(all_columns_records, index=df.index)
    return out.to_dict("records")


# ----------------------------------------------------------------------
# LOAD + BUILD
# ----------------------------------------------------------------------
try:
    raw_df = load_raw_data(sheet_id, sheet_name)
    prepared_df, resolved_cols = prepare_data(raw_df)
except Exception as e:
    st.error(f"Sheet load nahi ho payi. Sharing settings aur tab name check karo. Error: {e}")
    st.stop()

if prepared_df.empty:
    st.warning("Sheet se koi valid row nahi mili. Column headers check karo.")
    st.stop()

if not TEMPLATE_PATH.exists():
    st.error(
        f"Template file nahi mili: `{TEMPLATE_PATH}`.\n\n"
        "GitHub repo mein `assets/dashboard_template.html` file exist karti hai ya nahi check karo, "
        "aur ye ki `ap.py` repo ke root mein hi hai (kisi subfolder mein nahi)."
    )
    st.stop()

rows = build_rows(prepared_df, resolved_cols)

months_present = sorted(
    {r["month"] for r in rows if r["month"]},
    key=month_sort_key,
)
period_label = f"{months_present[0]} – {months_present[-1]}" if months_present else ""

all_headers_list = [c for c in prepared_df.columns if not str(c).startswith("_")]

_default_logical_order = ["FY", "Quarter", "Month", "SheetName", "ProjectCode", "ExpenseType", "Budget", "Subtotal"]
default_display_columns = [
    resolved_cols.get(k) for k in _default_logical_order if resolved_cols.get(k)
]

template_html = TEMPLATE_PATH.read_text(encoding="utf-8")
final_html = (
    template_html
    .replace("__ROWS_JSON__", json.dumps(rows, default=str))
    .replace("__MONTH_ORDER_JSON__", json.dumps(months_present, default=str))
    .replace("__PERIOD_LABEL__", period_label)
    .replace("__ALL_HEADERS_JSON__", json.dumps(all_headers_list, default=str))
    .replace("__DEFAULT_COLUMNS_JSON__", json.dumps(default_display_columns, default=str))
)

components.html(final_html, height=3200, scrolling=True)
