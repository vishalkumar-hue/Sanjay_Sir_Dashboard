"""
Payment / Expense Report Dashboard  (Streamlit)

Data source : Google Sheet (link-viewable) -> CSV export, ya sidebar se xlsx/csv upload
Run locally : streamlit run app.py
Deploy      : GitHub pe push -> share.streamlit.io se deploy

Optional secrets (.streamlit/secrets.toml ya Streamlit Cloud > Settings > Secrets):
    SHEET_ID  = "1P8awjtc-dwxCce1WJLDixljqL37yqCxnOe5QZ75_gIw"
    SHEET_GID = "0"
"""

import io
from datetime import datetime

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

# --------------------------------------------------------------------------- #
# CONFIG
# --------------------------------------------------------------------------- #
TITLE = "Payment report dashboard"
DEFAULT_SHEET_ID = "1P8awjtc-dwxCce1WJLDixljqL37yqCxnOe5QZ75_gIw"
DEFAULT_GID = "0"
CACHE_TTL_SEC = 300          # Google Sheet ka data 5 min cache hota hai
TOP_EXPENSE_TYPES = 8        # charts me top-N expense types, baaki "Others"

UNITS = {
    "Crore (Cr)": (1e7, "Cr"),
    "Lakh (L)": (1e5, "L"),
    "Rupees (₹)": (1.0, "₹"),
}

TEXT_COLS = ["Financial Year", "Quarter", "Month", "Sheet Name", "Project Code", "Expense Type"]
BASE_COLS = TEXT_COLS + ["Budget", "Amount"]
OTHERS_COLOR = "#B4BAC4"
PALETTE = [
    "#1F5F8B", "#E08E2B", "#2A9D8F", "#B5473A",
    "#7A6BB0", "#5C8A3A", "#C2648F", "#8A7A5C",
]

st.set_page_config(page_title=TITLE, page_icon="📊", layout="wide")
st.markdown(
    """
    <style>
    div[data-testid="stMetric"] {
        background: rgba(128,128,128,0.08);
        padding: 12px 16px;
        border-radius: 8px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# --------------------------------------------------------------------------- #
# HELPERS
# --------------------------------------------------------------------------- #
def get_secret(key, default):
    try:
        return st.secrets[key]
    except Exception:
        return default


def show(fig, height=420):
    """Plotly chart ko consistent layout ke saath dikhata hai (Streamlit versions ke across safe)."""
    fig.update_layout(
        height=height,
        margin=dict(l=10, r=10, t=50, b=10),
        legend_title_text="",
        template="plotly_white",
    )
    try:
        st.plotly_chart(fig, width="stretch")
    except TypeError:
        st.plotly_chart(fig, use_container_width=True)


def show_df(df, **kwargs):
    try:
        st.dataframe(df, width="stretch", hide_index=True, **kwargs)
    except TypeError:
        st.dataframe(df, use_container_width=True, hide_index=True, **kwargs)


def fy_of(d):
    if pd.isna(d):
        return ""
    start = d.year if d.month >= 4 else d.year - 1
    return f"{start}-{str(start + 1)[-2:]}"


def quarter_of(d):
    if pd.isna(d):
        return ""
    return f"Q{((d.month - 4) % 12) // 3 + 1}"


def to_number(series):
    cleaned = series.fillna("").astype(str).str.replace(r"[₹,\s]", "", regex=True)
    return pd.to_numeric(cleaned, errors="coerce")


# --------------------------------------------------------------------------- #
# DATA LOADING + CLEANING
# --------------------------------------------------------------------------- #
def normalize_columns(raw):
    df = raw.copy()
    df.columns = [str(c).strip() for c in df.columns]
    df = df.rename(
        columns={
            "Expence Type": "Expense Type",
            "Expense type": "Expense Type",
            "Subtotal After Deduction": "Amount",
        }
    )
    missing = [c for c in BASE_COLS if c not in df.columns]
    if missing:
        sheet_names = {"Expense Type": "Expence Type", "Amount": "Subtotal After Deduction"}
        raise ValueError(
            "Ye columns nahi mile: " + ", ".join(sheet_names.get(c, c) for c in missing)
            + ". Sheet public nahi hai ya tab (gid) galat hai, ya header row badli hui hai."
        )
    return df[BASE_COLS].copy()


def clean_data(raw):
    """Raw sheet -> analysis-ready dataframe + data-quality issues dict."""
    df = normalize_columns(raw)
    issues = {}

    for c in TEXT_COLS:
        df[c] = df[c].fillna("").astype(str).str.strip()

    # Header rows jo data ke beech me repeat ho gaye + blank rows
    is_header = df["Project Code"].str.lower().eq("project code")
    is_blank = df["Project Code"].eq("")
    issues["Repeated header rows removed"] = int(is_header.sum())
    issues["Blank project-code rows removed"] = int(is_blank.sum())
    df = df[~(is_header | is_blank)].copy()

    # Numbers
    df["Amount"] = to_number(df["Amount"])
    df["Budget"] = to_number(df["Budget"])
    issues["Non-numeric amount (treated as 0)"] = int(df["Amount"].isna().sum())
    df["Amount"] = df["Amount"].fillna(0.0)
    df["Budget"] = df["Budget"].fillna(0.0)

    # Truncated codes like "IIL/SEC-80/090525/CENTRE-SETUP/IILUP..."
    trunc_pat = r"(?:\.{2,}|…)\s*$"
    issues["Truncated project codes fixed ('...')"] = int(df["Project Code"].str.contains(trunc_pat, regex=True).sum())
    df["Project Code"] = df["Project Code"].str.replace(trunc_pat, "", regex=True).str.strip()

    # Project Code -> Client / Location / Start Date / Category / Entity
    parts = df["Project Code"].str.split("/", expand=True).reindex(columns=range(5))
    df["Client"] = parts[0]
    df["Location"] = parts[1]
    df["Category"] = parts[3]
    df["Entity"] = parts[4]
    for c in ["Client", "Location", "Category", "Entity"]:
        df[c] = df[c].fillna("").astype(str).str.strip().replace("", "Unknown")

    start_raw = parts[2].fillna("").astype(str).str.strip()
    df["Start Date"] = pd.to_datetime(start_raw, format="%d%m%y", errors="coerce")
    issues["Project codes with invalid date part (e.g. 5 digits)"] = int(df["Start Date"].isna().sum())

    # Month / FY / Quarter
    df["Month Date"] = pd.to_datetime(df["Month"], format="%b_%Y", errors="coerce")
    issues["Rows without valid Month (trend me nahi aayenge)"] = int(df["Month Date"].isna().sum())

    miss_fy = df["Financial Year"].eq("")
    miss_q = df["Quarter"].eq("")
    issues["FY/Quarter blank tha, Month se bhara"] = int(((miss_fy | miss_q) & df["Month Date"].notna()).sum())
    df.loc[miss_fy, "Financial Year"] = df.loc[miss_fy, "Month Date"].apply(fy_of)
    df.loc[miss_q, "Quarter"] = df.loc[miss_q, "Month Date"].apply(quarter_of)
    for c in ["Financial Year", "Quarter", "Sheet Name", "Expense Type"]:
        df[c] = df[c].replace("", "Unknown")

    df["Month Key"] = df["Month Date"].dt.strftime("%Y-%m").fillna("Unknown")

    issues["Zero-amount rows"] = int((df["Amount"] == 0).sum())
    issues["Exact duplicate rows"] = int(df.duplicated().sum())
    return df.reset_index(drop=True), issues


@st.cache_data(ttl=CACHE_TTL_SEC, show_spinner="Google Sheet se data la raha hoon...")
def load_google_sheet(sheet_id, gid):
    url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}"
    raw = pd.read_csv(url, dtype=str)
    df, issues = clean_data(raw)
    return df, issues, datetime.now().strftime("%d %b %Y, %H:%M")


# --------------------------------------------------------------------------- #
# SIDEBAR: DATA SOURCE
# --------------------------------------------------------------------------- #
with st.sidebar:
    st.header("Data source")
    source = st.radio("Source", ["Google Sheet", "Upload file"], horizontal=True, label_visibility="collapsed")
    upload = None
    sheet_id, gid = DEFAULT_SHEET_ID, DEFAULT_GID
    if source == "Google Sheet":
        sheet_id = st.text_input("Sheet ID", str(get_secret("SHEET_ID", DEFAULT_SHEET_ID)))
        gid = st.text_input("Tab gid", str(get_secret("SHEET_GID", DEFAULT_GID)))
        if st.button("Refresh data"):
            st.cache_data.clear()
            st.rerun()
    else:
        upload = st.file_uploader("xlsx ya csv", type=["xlsx", "csv"])

try:
    if source == "Google Sheet":
        data, issues, loaded_at = load_google_sheet(sheet_id.strip(), gid.strip())
    else:
        if upload is None:
            st.title(TITLE)
            st.info("Sidebar se xlsx ya csv upload karo.")
            st.stop()
        raw_df = pd.read_csv(upload, dtype=str) if upload.name.lower().endswith(".csv") else pd.read_excel(upload, dtype=str)
        data, issues = clean_data(raw_df)
        loaded_at = datetime.now().strftime("%d %b %Y, %H:%M")
except Exception as exc:  # noqa: BLE001
    st.title(TITLE)
    st.error(f"Data load nahi hua: {exc}")
    st.caption(
        "Check karo: Sheet 'Anyone with the link – Viewer' pe hai, Sheet ID / gid sahi hai, "
        "ya sidebar me 'Upload file' use karo."
    )
    st.stop()

if data.empty:
    st.title(TITLE)
    st.warning("Sheet me koi valid row nahi mili.")
    st.stop()

# --------------------------------------------------------------------------- #
# SIDEBAR: FILTERS (cascading)
# --------------------------------------------------------------------------- #
with st.sidebar:
    st.header("Filters")

    sheet_totals = data.groupby("Sheet Name")["Amount"].sum().sort_values(ascending=False)
    sheet_options = list(sheet_totals.index)
    # Alag sheets ke amounts ka scale alag hota hai -> default me sabse bada sheet
    default_sheets = sheet_options[:1] if len(sheet_options) > 1 else sheet_options
    sel_sheets = st.multiselect("Sheet name", sheet_options, default=default_sheets)
    if len(sheet_options) > 1:
        st.caption("Alag sheets ka amount scale alag hota hai, isliye default me sirf sabse bada sheet select hai.")

    scope = data[data["Sheet Name"].isin(sel_sheets)] if sel_sheets else data.iloc[0:0]

    total_scope = scope["Amount"].sum()
    default_unit = 0 if total_scope >= 1e7 else 1 if total_scope >= 1e5 else 2
    unit_label = st.selectbox("Amount unit", list(UNITS.keys()), index=default_unit)

    filtered = scope
    for label, col in [
        ("Financial year", "Financial Year"),
        ("Quarter", "Quarter"),
        ("Client / prefix", "Client"),
        ("Entity", "Entity"),
        ("Category", "Category"),
        ("Expense type", "Expense Type"),
    ]:
        options = sorted(filtered[col].unique())
        picked = st.multiselect(label, options, placeholder="All")
        if picked:
            filtered = filtered[filtered[col].isin(picked)]

f = filtered.copy()
if f.empty:
    st.title(TITLE)
    st.warning("Selected filters me koi data nahi hai. Filters badlo.")
    st.stop()

divisor, unit_sym = UNITS[unit_label]
f["Amt"] = f["Amount"] / divisor
f["Budget_u"] = f["Budget"] / divisor
axis_title = f"Amount ({unit_sym})"


def fmt(value):
    if unit_sym == "₹":
        return f"₹{value:,.0f}"
    return f"₹{value / divisor:,.2f} {unit_sym}"


# Top-N expense types + Others (charts ko readable rakhne ke liye)
type_rank = f.groupby("Expense Type")["Amount"].sum().sort_values(ascending=False)
top_types = list(type_rank.index[:TOP_EXPENSE_TYPES])
f["Type Group"] = f["Expense Type"].where(f["Expense Type"].isin(top_types), "Others")
type_order = top_types + (["Others"] if (f["Type Group"] == "Others").any() else [])
color_map = {t: PALETTE[i % len(PALETTE)] for i, t in enumerate(top_types)}
color_map["Others"] = OTHERS_COLOR

# --------------------------------------------------------------------------- #
# HEADER + KPIs
# --------------------------------------------------------------------------- #
st.title(TITLE)
st.caption(f"Sheet: {', '.join(sel_sheets)}   |   Data refreshed: {loaded_at}   |   Rows in view: {len(f):,}")

total = f["Amount"].sum()
proj = f.groupby("Project Code")["Amount"].sum().sort_values(ascending=False)
n_proj = int(proj.shape[0])
avg_proj = total / n_proj if n_proj else 0
top_code = proj.index[0]
top_val = proj.iloc[0]
top_share = (top_val / total * 100) if total > 0 else 0
zero_rows = int((f["Amount"] == 0).sum())

k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Total spend", fmt(total))
k2.metric("Projects", f"{n_proj:,}")
k3.metric("Avg per project", fmt(avg_proj))
k4.metric("Biggest project", fmt(top_val), delta=f"{top_share:.1f}% of total", delta_color="off")
k4.caption(top_code)
k5.metric("Zero-amount rows", f"{zero_rows:,}")

tab_over, tab_ce, tab_proj, tab_heat, tab_budget, tab_data, tab_dq = st.tabs(
    ["Overview", "Client & entity", "Projects", "Heatmap", "Budget vs actual", "Data", "Data quality"]
)

# --------------------------------------------------------------------------- #
# TAB: OVERVIEW
# --------------------------------------------------------------------------- #
with tab_over:
    view = st.radio("Trend view", ["Monthly", "Quarterly"], horizontal=True)

    if view == "Monthly":
        m = f.dropna(subset=["Month Date"]).copy()
        if m.empty:
            st.info("Valid Month wali rows nahi hain.")
        else:
            m["Period"] = m["Month Date"].dt.strftime("%b %y")
            full_range = pd.period_range(m["Month Date"].min(), m["Month Date"].max(), freq="M")
            order = [p.strftime("%b %y") for p in full_range]
            g = m.groupby(["Period", "Type Group"], as_index=False)["Amt"].sum()
            fig = px.bar(
                g, x="Period", y="Amt", color="Type Group",
                category_orders={"Period": order, "Type Group": type_order},
                color_discrete_map=color_map,
                labels={"Amt": axis_title, "Period": ""},
                title="Monthly spend by expense type (khaali mahine bhi dikhte hain)",
            )
            show(fig)
    else:
        q = f.copy()
        q["Period"] = q["Financial Year"] + " " + q["Quarter"]
        g = q.groupby(["Period", "Type Group"], as_index=False)["Amt"].sum()
        fig = px.bar(
            g, x="Period", y="Amt", color="Type Group",
            category_orders={"Period": sorted(g["Period"].unique()), "Type Group": type_order},
            color_discrete_map=color_map,
            labels={"Amt": axis_title, "Period": ""},
            title="Quarterly spend by expense type",
        )
        show(fig)

    c1, c2 = st.columns(2)
    with c1:
        fq = f.groupby(["Financial Year", "Quarter"], as_index=False)["Amt"].sum()
        fig = px.bar(
            fq, x="Financial Year", y="Amt", color="Quarter", barmode="group",
            text_auto=".2s",
            category_orders={"Quarter": ["Q1", "Q2", "Q3", "Q4"]},
            color_discrete_sequence=PALETTE,
            labels={"Amt": axis_title},
            title="Financial year and quarter",
        )
        show(fig, 380)
    with c2:
        mix = f.groupby("Type Group", as_index=False)["Amt"].sum()
        mix = mix[mix["Amt"] > 0]
        fig = px.pie(
            mix, names="Type Group", values="Amt", hole=0.55,
            color="Type Group", color_discrete_map=color_map,
            category_orders={"Type Group": type_order},
            title="Expense type share",
        )
        fig.update_traces(textinfo="percent", sort=False)
        show(fig, 380)

# --------------------------------------------------------------------------- #
# TAB: CLIENT & ENTITY
# --------------------------------------------------------------------------- #
with tab_ce:
    c1, c2 = st.columns(2)
    with c1:
        cl = f.groupby("Client", as_index=False)["Amt"].sum().sort_values("Amt")
        fig = px.bar(
            cl, x="Amt", y="Client", orientation="h", text_auto=".2f",
            color_discrete_sequence=[PALETTE[0]],
            labels={"Amt": axis_title, "Client": ""},
            title="Spend by client / prefix",
        )
        show(fig, max(320, 32 * len(cl) + 120))
    with c2:
        en = f.groupby(["Entity", "Type Group"], as_index=False)["Amt"].sum()
        ent_order = list(f.groupby("Entity")["Amt"].sum().sort_values(ascending=False).index)
        fig = px.bar(
            en, x="Entity", y="Amt", color="Type Group",
            category_orders={"Entity": ent_order, "Type Group": type_order},
            color_discrete_map=color_map,
            labels={"Amt": axis_title, "Entity": ""},
            title="Spend by entity and expense type",
        )
        show(fig, max(320, 32 * len(cl) + 120))

    c3, c4 = st.columns([2, 1])
    with c3:
        tm = f[f["Amt"] > 0].groupby(["Client", "Location"], as_index=False)["Amt"].sum()
        if tm.empty:
            st.info("Treemap ke liye positive amount nahi hai.")
        else:
            fig = px.treemap(
                tm, path=["Client", "Location"], values="Amt",
                color_discrete_sequence=PALETTE,
                title="Client to location breakdown",
            )
            fig.update_traces(textinfo="label+value", texttemplate="%{label}<br>%{value:,.1f}")
            show(fig, 460)
    with c4:
        cat = f.groupby("Category", as_index=False)["Amt"].sum().sort_values("Amt", ascending=False)
        fig = px.bar(
            cat, x="Category", y="Amt", text_auto=".2f",
            color_discrete_sequence=[PALETTE[2]],
            labels={"Amt": axis_title, "Category": ""},
            title="By category",
        )
        show(fig, 460)

# --------------------------------------------------------------------------- #
# TAB: PROJECTS
# --------------------------------------------------------------------------- #
with tab_proj:
    max_n = int(min(30, max(5, n_proj)))
    top_n = st.slider("Top N projects", 5, max_n, min(10, max_n)) if max_n > 5 else 5

    pg = f.groupby("Project Code", as_index=False)["Amt"].sum().sort_values("Amt", ascending=False)
    positive_total = pg.loc[pg["Amt"] > 0, "Amt"].sum()
    pg["Share %"] = np.where(positive_total > 0, pg["Amt"] / positive_total * 100, 0.0)
    pg["Cumulative %"] = pg["Share %"].cumsum()

    n_80 = int((pg["Cumulative %"] < 80).sum() + 1) if positive_total > 0 else 0
    st.markdown(f"**{n_80} projects** total spend ka 80% bana rahe hain (out of {n_proj}).")

    top = pg.head(top_n)
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(
        go.Bar(x=top["Project Code"], y=top["Amt"], name=axis_title, marker_color=PALETTE[0]),
        secondary_y=False,
    )
    fig.add_trace(
        go.Scatter(
            x=top["Project Code"], y=top["Cumulative %"], name="Cumulative %",
            mode="lines+markers", line=dict(color=PALETTE[1], width=2),
        ),
        secondary_y=True,
    )
    fig.update_yaxes(title_text=axis_title, secondary_y=False)
    fig.update_yaxes(title_text="Cumulative %", range=[0, 105], secondary_y=True)
    fig.update_xaxes(tickangle=-40)
    fig.update_layout(title=f"Top {top_n} projects (Pareto)")
    show(fig, 520)

    tl = f[f["Start Date"].notna()].groupby(["Project Code", "Start Date", "Client"], as_index=False)["Amt"].sum()
    tl = tl[tl["Amt"] > 0]
    if not tl.empty:
        fig = px.scatter(
            tl, x="Start Date", y="Amt", size="Amt", color="Client",
            hover_name="Project Code", color_discrete_sequence=PALETTE,
            labels={"Amt": axis_title},
            title="Project start date vs spend",
        )
        show(fig, 420)

    tbl = pg.head(top_n).rename(columns={"Amt": axis_title})
    tbl["Share %"] = tbl["Share %"].round(1)
    tbl["Cumulative %"] = tbl["Cumulative %"].round(1)
    show_df(tbl)

# --------------------------------------------------------------------------- #
# TAB: HEATMAP
# --------------------------------------------------------------------------- #
with tab_heat:
    dims = {
        "Client": "Client",
        "Entity": "Entity",
        "Location": "Location",
        "Category": "Category",
        "Expense type": "Type Group",
        "Financial year": "Financial Year",
        "Quarter": "Quarter",
        "Month": "Month Key",
    }
    dim_names = list(dims.keys())
    h1, h2 = st.columns(2)
    row_dim = h1.selectbox("Rows", dim_names, index=dim_names.index("Client"))
    col_dim = h2.selectbox("Columns", dim_names, index=dim_names.index("Expense type"))
    if row_dim == col_dim:
        st.warning("Rows aur Columns alag chuno.")
    else:
        pv = f.pivot_table(index=dims[row_dim], columns=dims[col_dim], values="Amt", aggfunc="sum", fill_value=0)
        fig = px.imshow(
            pv, text_auto=".1f", aspect="auto", color_continuous_scale="Blues",
            labels=dict(x=col_dim, y=row_dim, color=axis_title),
            title=f"{row_dim} x {col_dim}",
        )
        show(fig, max(360, 34 * len(pv) + 140))

# --------------------------------------------------------------------------- #
# TAB: BUDGET VS ACTUAL
# --------------------------------------------------------------------------- #
with tab_budget:
    if f["Budget"].abs().sum() == 0:
        st.info(
            "Is data me 'Budget' column poora 0 hai, isliye Budget vs Actual nahi ban sakta. "
            "Sheet ke 'Budget' column me values aa jaayein to ye tab apne aap chal jayega."
        )
    else:
        by = st.selectbox("Group by", ["Project Code", "Client", "Entity", "Location", "Financial Year"])
        bg = f.groupby(by, as_index=False).agg(Budget=("Budget_u", "sum"), Actual=("Amt", "sum"))
        bg["Variance (Budget - Actual)"] = bg["Budget"] - bg["Actual"]
        bg["Utilisation %"] = np.where(bg["Budget"] > 0, bg["Actual"] / bg["Budget"] * 100, np.nan)
        bg = bg.sort_values("Actual", ascending=False)

        plot = bg.head(20).melt(id_vars=by, value_vars=["Budget", "Actual"], var_name="Type", value_name="Value")
        fig = px.bar(
            plot, x=by, y="Value", color="Type", barmode="group",
            color_discrete_map={"Budget": OTHERS_COLOR, "Actual": PALETTE[0]},
            labels={"Value": axis_title, by: ""},
            title=f"Budget vs actual by {by.lower()} (top 20 by actual)",
        )
        fig.update_xaxes(tickangle=-40)
        show(fig, 480)
        st.caption("Budget rows ka sum liya gaya hai. Agar ek project ke multiple rows me same budget repeat hota hai, to total double count hoga.")
        show_df(bg.round(2))

# --------------------------------------------------------------------------- #
# TAB: DATA
# --------------------------------------------------------------------------- #
with tab_data:
    show_cols = [
        "Sheet Name", "Financial Year", "Quarter", "Month", "Project Code", "Client", "Location",
        "Start Date", "Category", "Entity", "Expense Type", "Budget", "Amount",
    ]
    table = f[show_cols].copy()
    table["Start Date"] = table["Start Date"].dt.date
    table["Amount"] = table["Amount"].round(2)
    st.caption(f"{len(table):,} rows (sidebar filters lage hue hain)")
    show_df(table)

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        table.to_excel(xw, sheet_name="Data", index=False)
        f.groupby("Client", as_index=False)["Amount"].sum().sort_values("Amount", ascending=False).to_excel(
            xw, sheet_name="By client", index=False)
        f.groupby("Entity", as_index=False)["Amount"].sum().sort_values("Amount", ascending=False).to_excel(
            xw, sheet_name="By entity", index=False)
        f.groupby("Expense Type", as_index=False)["Amount"].sum().sort_values("Amount", ascending=False).to_excel(
            xw, sheet_name="By expense type", index=False)
        f.groupby(["Financial Year", "Quarter"], as_index=False)["Amount"].sum().to_excel(
            xw, sheet_name="By quarter", index=False)

    d1, d2 = st.columns(2)
    d1.download_button(
        "Download Excel (data + summaries)", buf.getvalue(), "payment_report.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    d2.download_button(
        "Download CSV", table.to_csv(index=False).encode("utf-8-sig"), "payment_report.csv", mime="text/csv",
    )

# --------------------------------------------------------------------------- #
# TAB: DATA QUALITY
# --------------------------------------------------------------------------- #
with tab_dq:
    st.caption("Ye checks poori sheet par hain (sidebar filters se independent).")
    dq = pd.DataFrame({"Check": list(issues.keys()), "Count": list(issues.values())})
    show_df(dq)

    z = data[data["Amount"] == 0][["Sheet Name", "Month", "Project Code", "Expense Type"]]
    if not z.empty:
        st.markdown("**Zero-amount rows**")
        show_df(z)
    bad_dt = data[data["Start Date"].isna()][["Sheet Name", "Project Code"]]
    if not bad_dt.empty:
        st.markdown("**Project codes jinme date part invalid hai**")
        show_df(bad_dt)
