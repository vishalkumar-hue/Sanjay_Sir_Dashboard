"""
Expense Dashboard — Sanjay Jha Report
Live analysis of the "Compile Report" tab of the Google Sheet.

Built from scratch, directly on the real columns found in the sheet:
Financial Year | Quarter | Month | Sheet Name | Project Code | Budget |
Expence Type | Subtotal After Deduction

No separate HTML template file — everything (data load, cleaning,
analysis, charts, tables) lives in this one file so there's nothing
extra to keep in sync on GitHub / Streamlit Cloud.
"""

import re
import urllib.parse
from datetime import datetime

import pandas as pd
import plotly.express as px
import streamlit as st

# ----------------------------------------------------------------------
# CONFIG
# ----------------------------------------------------------------------
SHEET_ID = "1P8awjtc-dwxCce1WJLDixljqL37yqCxnOe5QZ75_gIw"
SHEET_NAME = "Compile Report"

st.set_page_config(page_title="Expense Dashboard", layout="wide", page_icon="💸")

st.markdown(
    """
    <style>
    .stApp { background:#0b1220; }
    div[data-testid="stMetric"] {
        background:#121b2e; border:1px solid #223252; border-radius:10px;
        padding:12px 16px;
    }
    div[data-testid="stMetricLabel"] { color:#8ea0c2; }
    div[data-testid="stMetricValue"] { color:#e7ecf5; }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("💸 Expense Dashboard")
st.caption("Live analysis of the Compile Report sheet")

# ----------------------------------------------------------------------
# DATA LOADING
# ----------------------------------------------------------------------
def build_csv_url(sheet_id: str, sheet_name: str) -> str:
    encoded_name = urllib.parse.quote(sheet_name)
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?tqx=out:csv&sheet={encoded_name}"


@st.cache_data(ttl=300, show_spinner="Sheet se data la rahe hain...")
def load_raw_data(sheet_id: str, sheet_name: str) -> pd.DataFrame:
    url = build_csv_url(sheet_id, sheet_name)
    df = pd.read_csv(url)
    df.columns = [str(c).strip() for c in df.columns]
    return df


def clean_numeric(series: pd.Series) -> pd.Series:
    """Strip ₹, commas, % and blanks, then convert to float."""
    if series.dtype.kind in "if":
        return series.astype(float)
    cleaned = (
        series.astype(str)
        .str.replace(r"[₹,%\s]", "", regex=True)
        .replace({"": None, "nan": None, "None": None, "-": None})
    )
    return pd.to_numeric(cleaned, errors="coerce")


# ----------------------------------------------------------------------
# COLUMN RESOLUTION — tolerant to header typos/renames in the sheet
# ----------------------------------------------------------------------
def find_col(df: pd.DataFrame, candidates):
    def normalize(s: str) -> str:
        return "".join(ch for ch in str(s).strip().lower() if ch.isalnum())

    for cand in candidates:
        target = normalize(cand)
        for c in df.columns:
            if normalize(c) == target:
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
    "Subtotal": ["Subtotal After Deduction", "Subtotal"],
}


def resolve_columns(df: pd.DataFrame):
    return {logical: find_col(df, cands) for logical, cands in COLUMN_TARGETS.items()}


# ----------------------------------------------------------------------
# JUNK-ROW FILTER — repeated header rows that show up mid-sheet
# ----------------------------------------------------------------------
JUNK_MARKERS = {"project code", "nature of expense", "expence type", "expense type", "sheet name"}


def is_junk_row(project_code, expense_type) -> bool:
    pc = str(project_code).strip().lower()
    et = str(expense_type).strip().lower()
    return pc in JUNK_MARKERS or et in JUNK_MARKERS


# ----------------------------------------------------------------------
# PROJECT CODE PARSING -> Client / Project Type / Entity
# e.g. "BSEB/PATNA/010924/CENTRE-SETUP/IIPLD"
#       Client=BSEB, ProjectType=CENTRE-SETUP, Entity=IIPLD
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


def month_sort_key(m: str):
    for fmt in ("%b_%Y", "%b_%y"):
        try:
            return datetime.strptime(str(m), fmt)
        except Exception:
            continue
    return datetime.max


def fy_quarter_sort_key(fy: str, quarter: str):
    m = re.search(r"(\d{4})", str(fy))
    year = int(m.group(1)) if m else 9999
    qm = re.search(r"(\d)", str(quarter))
    q = int(qm.group(1)) if qm else 9
    return year * 10 + q


def fmt_inr(v) -> str:
    if v is None or pd.isna(v):
        return "-"
    v = float(v)
    abs_v = abs(v)
    if abs_v >= 1e7:
        return f"₹{v/1e7:.2f} Cr"
    if abs_v >= 1e5:
        return f"₹{v/1e5:.2f} L"
    return f"₹{v:,.0f}"


@st.cache_data(ttl=300)
def prepare_data(sheet_id: str, sheet_name: str):
    raw = load_raw_data(sheet_id, sheet_name)
    cols = resolve_columns(raw)

    pc_col = cols.get("ProjectCode")
    et_col = cols.get("ExpenseType")
    budget_col = cols.get("Budget")
    subtotal_col = cols.get("Subtotal")

    df = raw.copy()

    # Drop rows missing the two columns everything depends on.
    if not pc_col or not subtotal_col:
        return pd.DataFrame(), cols, "Sheet me 'Project Code' ya 'Subtotal After Deduction' column nahi mila."

    # Remove repeated-header junk rows.
    if pc_col and et_col:
        junk_mask = df.apply(lambda r: is_junk_row(r[pc_col], r[et_col]), axis=1)
        df = df[~junk_mask]

    # Drop rows with a blank project code (section gaps in the sheet).
    df = df[df[pc_col].fillna("").astype(str).str.strip() != ""]

    df[subtotal_col] = clean_numeric(df[subtotal_col])
    if budget_col:
        df[budget_col] = clean_numeric(df[budget_col])
    else:
        df["_Budget"] = 0.0
        budget_col = "_Budget"

    df = df[df[subtotal_col].notna()]

    for logical in ["FY", "Quarter", "Month", "SheetName", "ProjectCode", "ExpenseType"]:
        c = cols.get(logical)
        if c:
            df[c] = df[c].fillna("").astype(str).str.strip().replace({"nan": ""})

    parsed = df[pc_col].apply(parse_project_code)
    df["Client"] = parsed.apply(lambda t: t[0])
    df["ProjectType"] = parsed.apply(lambda t: t[1])
    df["Entity"] = parsed.apply(lambda t: t[2])

    fy_col, q_col = cols.get("FY"), cols.get("Quarter")
    if fy_col and q_col:
        df["FYQuarter"] = df[fy_col] + " " + df[q_col]
    else:
        df["FYQuarter"] = ""

    df = df.rename(columns={
        subtotal_col: "Expense",
        budget_col: "Budget",
        cols.get("FY") or "FY": "FY",
        cols.get("Quarter") or "Quarter": "Quarter",
        cols.get("Month") or "Month": "Month",
        cols.get("SheetName") or "SheetName": "SheetName",
        cols.get("ProjectCode") or "ProjectCode": "ProjectCode",
        cols.get("ExpenseType") or "ExpenseType": "ExpenseType",
    })

    keep_cols = ["FY", "Quarter", "Month", "SheetName", "ProjectCode", "Client",
                 "ProjectType", "Entity", "ExpenseType", "Budget", "Expense", "FYQuarter"]
    keep_cols = [c for c in keep_cols if c in df.columns]
    df = df[keep_cols].reset_index(drop=True)

    return df, cols, None


# ----------------------------------------------------------------------
# LOAD
# ----------------------------------------------------------------------
top_col1, top_col2 = st.columns([8, 1])
with top_col2:
    if st.button("🔄 Refresh"):
        st.cache_data.clear()
        st.rerun()

try:
    df, resolved_cols, err = prepare_data(SHEET_ID, SHEET_NAME)
except Exception as e:
    st.error(f"Sheet load nahi ho payi. Sharing settings aur tab name check karo.\n\nError: {e}")
    st.stop()

if err:
    st.error(err)
    st.stop()

if df.empty:
    st.warning("Sheet se koi valid row nahi mili. Column headers check karo.")
    st.stop()

# ----------------------------------------------------------------------
# FILTERS (sidebar)
# ----------------------------------------------------------------------
st.sidebar.header("Filters")


def multiselect_filter(label, col):
    if col not in df.columns:
        return None
    options = sorted([v for v in df[col].unique() if v])
    selected = st.sidebar.multiselect(label, options)
    return selected or None


f_fy = multiselect_filter("Financial Year", "FY")
f_quarter = multiselect_filter("Quarter", "Quarter")
f_month = multiselect_filter("Month", "Month")
f_sheet = multiselect_filter("Sheet Name", "SheetName")
f_client = multiselect_filter("Client", "Client")
f_expense_type = multiselect_filter("Expense Type", "ExpenseType")

filtered = df.copy()
for col, sel in [("FY", f_fy), ("Quarter", f_quarter), ("Month", f_month),
                  ("SheetName", f_sheet), ("Client", f_client), ("ExpenseType", f_expense_type)]:
    if sel:
        filtered = filtered[filtered[col].isin(sel)]

if st.sidebar.button("Clear all filters"):
    st.rerun()

# ----------------------------------------------------------------------
# KPIs
# ----------------------------------------------------------------------
total_expense = filtered["Expense"].sum()
total_budget = filtered["Budget"].sum() if "Budget" in filtered.columns else 0
total_entries = len(filtered)
unique_clients = filtered["Client"].nunique()
unique_types = filtered["ExpenseType"].nunique() if "ExpenseType" in filtered.columns else 0
avg_entry = total_expense / total_entries if total_entries else 0

k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Total Expense", fmt_inr(total_expense))
k2.metric("Total Budget", fmt_inr(total_budget))
k3.metric("Total Entries", f"{total_entries:,}")
k4.metric("Unique Clients", f"{unique_clients:,}")
k5.metric("Avg / Entry", fmt_inr(avg_entry))

st.divider()

# ----------------------------------------------------------------------
# TABS
# ----------------------------------------------------------------------
tab_overview, tab_monthly, tab_type, tab_client, tab_sheet, tab_quarter, tab_data = st.tabs(
    ["Overview", "Monthly Trend", "Expense Type", "Clients", "Sheet-wise", "Quarter Comparison", "All Entries"]
)

CHART_TEMPLATE = "plotly_dark"
PALETTE = px.colors.qualitative.Set2

# ---- Overview ----
with tab_overview:
    c1, c2 = st.columns(2)

    with c1:
        st.subheader("Expense Trend by Month")
        if "Month" in filtered.columns:
            monthly = filtered.groupby("Month", as_index=False)["Expense"].sum()
            monthly["_sort"] = monthly["Month"].apply(month_sort_key)
            monthly = monthly.sort_values("_sort")
            fig = px.bar(monthly, x="Month", y="Expense", template=CHART_TEMPLATE,
                         color_discrete_sequence=PALETTE)
            fig.update_layout(showlegend=False, xaxis_title="", yaxis_title="Expense (₹)")
            st.plotly_chart(fig, use_container_width=True)

    with c2:
        st.subheader("Expense Type Split")
        if "ExpenseType" in filtered.columns:
            by_type = filtered.groupby("ExpenseType", as_index=False)["Expense"].sum()
            by_type = by_type.sort_values("Expense", ascending=False).head(12)
            fig = px.pie(by_type, names="ExpenseType", values="Expense", template=CHART_TEMPLATE,
                         color_discrete_sequence=PALETTE, hole=0.45)
            st.plotly_chart(fig, use_container_width=True)

    c3, c4 = st.columns(2)
    with c3:
        st.subheader("Top 10 Clients by Expense")
        by_client = filtered.groupby("Client", as_index=False)["Expense"].sum()
        by_client = by_client.sort_values("Expense", ascending=False).head(10)
        fig = px.bar(by_client, x="Expense", y="Client", orientation="h", template=CHART_TEMPLATE,
                     color_discrete_sequence=PALETTE)
        fig.update_layout(yaxis={"categoryorder": "total ascending"}, showlegend=False, yaxis_title="")
        st.plotly_chart(fig, use_container_width=True)

    with c4:
        st.subheader("Source Sheet Split")
        if "SheetName" in filtered.columns:
            by_sheet = filtered.groupby("SheetName", as_index=False)["Expense"].sum()
            by_sheet = by_sheet.sort_values("Expense", ascending=False)
            fig = px.pie(by_sheet, names="SheetName", values="Expense", template=CHART_TEMPLATE,
                         color_discrete_sequence=PALETTE, hole=0.45)
            st.plotly_chart(fig, use_container_width=True)

# ---- Monthly Trend ----
with tab_monthly:
    st.subheader("Expense by Month")
    if "Month" in filtered.columns:
        monthly = filtered.groupby("Month", as_index=False).agg(Expense=("Expense", "sum"),
                                                                  Budget=("Budget", "sum"),
                                                                  Entries=("Expense", "count"))
        monthly["_sort"] = monthly["Month"].apply(month_sort_key)
        monthly = monthly.sort_values("_sort").drop(columns="_sort")
        fig = px.bar(monthly, x="Month", y=["Expense", "Budget"], barmode="group",
                     template=CHART_TEMPLATE, color_discrete_sequence=PALETTE)
        fig.update_layout(yaxis_title="₹", xaxis_title="")
        st.plotly_chart(fig, use_container_width=True)

        monthly_display = monthly.copy()
        monthly_display["Expense"] = monthly_display["Expense"].apply(fmt_inr)
        monthly_display["Budget"] = monthly_display["Budget"].apply(fmt_inr)
        st.dataframe(monthly_display, use_container_width=True, hide_index=True)

# ---- Expense Type ----
with tab_type:
    st.subheader("Expense by Type")
    if "ExpenseType" in filtered.columns:
        by_type = filtered.groupby("ExpenseType", as_index=False).agg(Expense=("Expense", "sum"),
                                                                        Entries=("Expense", "count"))
        by_type = by_type.sort_values("Expense", ascending=False)
        by_type["AvgPerEntry"] = by_type["Expense"] / by_type["Entries"]
        by_type["% of Total"] = (by_type["Expense"] / by_type["Expense"].sum() * 100).round(1)

        search = st.text_input("Search expense type...", key="search_type")
        view = by_type[by_type["ExpenseType"].str.contains(search, case=False, na=False)] if search else by_type

        fig = px.bar(view.head(25), x="Expense", y="ExpenseType", orientation="h",
                     template=CHART_TEMPLATE, color_discrete_sequence=PALETTE)
        fig.update_layout(yaxis={"categoryorder": "total ascending"}, showlegend=False, yaxis_title="")
        st.plotly_chart(fig, use_container_width=True)

        display = view.copy()
        display["Expense"] = display["Expense"].apply(fmt_inr)
        display["AvgPerEntry"] = display["AvgPerEntry"].apply(fmt_inr)
        display["% of Total"] = display["% of Total"].astype(str) + "%"
        st.dataframe(display, use_container_width=True, hide_index=True)

# ---- Clients ----
with tab_client:
    st.subheader("Client Analysis")
    by_client = filtered.groupby("Client", as_index=False).agg(Expense=("Expense", "sum"),
                                                                 Entries=("Expense", "count"))
    by_client = by_client.sort_values("Expense", ascending=False)
    by_client["AvgPerEntry"] = by_client["Expense"] / by_client["Entries"]

    max_clients = len(by_client)
    if max_clients <= 5:
        top_n = max_clients
    else:
        top_n = st.slider("Show top N clients", 5, min(50, max_clients), min(15, max_clients))
    search_c = st.text_input("Search client...", key="search_client")
    view = by_client[by_client["Client"].str.contains(search_c, case=False, na=False)] if search_c else by_client

    fig = px.bar(view.head(top_n), x="Expense", y="Client", orientation="h",
                 template=CHART_TEMPLATE, color_discrete_sequence=PALETTE)
    fig.update_layout(yaxis={"categoryorder": "total ascending"}, showlegend=False, yaxis_title="")
    st.plotly_chart(fig, use_container_width=True)

    display = view.copy()
    display["Expense"] = display["Expense"].apply(fmt_inr)
    display["AvgPerEntry"] = display["AvgPerEntry"].apply(fmt_inr)
    st.dataframe(display, use_container_width=True, hide_index=True)

# ---- Sheet-wise ----
with tab_sheet:
    st.subheader("Sheet-wise Analysis")
    if "SheetName" in filtered.columns:
        by_sheet = filtered.groupby("SheetName", as_index=False).agg(Expense=("Expense", "sum"),
                                                                       Entries=("Expense", "count"))
        by_sheet = by_sheet.sort_values("Expense", ascending=False)
        by_sheet["AvgPerEntry"] = by_sheet["Expense"] / by_sheet["Entries"]

        fig = px.bar(by_sheet, x="Expense", y="SheetName", orientation="h",
                     template=CHART_TEMPLATE, color_discrete_sequence=PALETTE)
        fig.update_layout(yaxis={"categoryorder": "total ascending"}, showlegend=False, yaxis_title="")
        st.plotly_chart(fig, use_container_width=True)

        display = by_sheet.copy()
        display["Expense"] = display["Expense"].apply(fmt_inr)
        display["AvgPerEntry"] = display["AvgPerEntry"].apply(fmt_inr)
        st.dataframe(display, use_container_width=True, hide_index=True)

# ---- Quarter Comparison ----
with tab_quarter:
    st.subheader("Expense & Budget by Quarter")
    if "FYQuarter" in filtered.columns:
        by_q = filtered.groupby(["FY", "Quarter", "FYQuarter"], as_index=False).agg(
            Expense=("Expense", "sum"), Budget=("Budget", "sum"), Entries=("Expense", "count")
        )
        by_q = by_q[by_q["FYQuarter"].str.strip() != ""]
        by_q["_sort"] = by_q.apply(lambda r: fy_quarter_sort_key(r["FY"], r["Quarter"]), axis=1)
        by_q = by_q.sort_values("_sort").drop(columns="_sort")

        fig = px.bar(by_q, x="FYQuarter", y=["Expense", "Budget"], barmode="group",
                     template=CHART_TEMPLATE, color_discrete_sequence=PALETTE)
        fig.update_layout(yaxis_title="₹", xaxis_title="")
        st.plotly_chart(fig, use_container_width=True)

        by_q["QoQ Expense Δ%"] = by_q["Expense"].pct_change().mul(100).round(1)
        display = by_q[["FYQuarter", "Expense", "Budget", "Entries", "QoQ Expense Δ%"]].copy()
        display["Expense"] = display["Expense"].apply(fmt_inr)
        display["Budget"] = display["Budget"].apply(fmt_inr)
        display["QoQ Expense Δ%"] = display["QoQ Expense Δ%"].apply(
            lambda v: "-" if pd.isna(v) else (f"+{v}%" if v >= 0 else f"{v}%")
        )
        st.dataframe(display, use_container_width=True, hide_index=True)

# ---- All Entries ----
with tab_data:
    st.subheader("All Entries")
    search_all = st.text_input("Search project code, client, expense type...", key="search_all")
    view = filtered.copy()
    if search_all:
        term = search_all.lower()
        mask = view.apply(lambda r: term in str(r["ProjectCode"]).lower()
                           or term in str(r["Client"]).lower()
                           or term in str(r.get("ExpenseType", "")).lower(), axis=1)
        view = view[mask]

    st.caption(f"{len(view):,} of {len(df):,} entries")
    display = view.copy()
    display["Expense"] = display["Expense"].apply(fmt_inr)
    display["Budget"] = display["Budget"].apply(fmt_inr)
    st.dataframe(display, use_container_width=True, hide_index=True, height=500)

    csv = view.to_csv(index=False).encode("utf-8")
    st.download_button("⬇️ Download filtered data as CSV", csv, "filtered_expenses.csv", "text/csv")
