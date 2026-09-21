"""
Payment / Expense Report Dashboard  (Streamlit)

Data source : Google Sheet (link-viewable) -> CSV export, ya "Data source" se xlsx/csv upload
Run locally : streamlit run app.py
Themes      : .streamlit/config.toml me light + dark dono defined hain
              (app ke top-right ⋮ menu -> Settings -> Theme; default = device ka system theme)

Optional secrets (Streamlit Cloud > Settings > Secrets):
    SHEET_ID  = "1P8awjtc-dwxCce1WJLDixljqL37yqCxnOe5QZ75_gIw"
    SHEET_GID = "0"
"""

import html
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

# Chart colours: light aur dark dono background pe padhne layak
PALETTE = ["#3B82C4", "#F0A03C", "#35B0A0", "#D9614C", "#8E7CC3", "#7FB05B", "#D4739F", "#A8916A"]
OTHERS_COLOR = "#8A94A3"
OVER_COLOR = "#D9614C"

FILTERS = [  # (label, column, session key)
    ("Financial year", "Financial Year", "f_fy"),
    ("Quarter", "Quarter", "f_q"),
    ("Client / prefix", "Client", "f_client"),
    ("Entity", "Entity", "f_entity"),
    ("Category", "Category", "f_cat"),
    ("Expense type", "Expense Type", "f_type"),
]

st.set_page_config(page_title=TITLE, page_icon="📊", layout="wide", initial_sidebar_state="collapsed")

# --------------------------------------------------------------------------- #
# THEME TOKENS (Streamlit ka current light/dark theme padh ke)
# --------------------------------------------------------------------------- #
try:
    THEME = st.context.theme.type or "light"
except Exception:  # noqa: BLE001
    THEME = "light"

TOKENS = {
    "light": dict(card="#FFFFFF", border="#DCE2EA", grid="#E8EDF3", muted="#5B6775", track="#EEF2F7"),
    "dark": dict(card="#16212C", border="#273443", grid="#243141", muted="#9AA7B6", track="#1D2B3A"),
}[THEME]

HEAT_SCALE = (
    [[0, "#EAF2FA"], [1, "#1B5E8C"]] if THEME == "light" else [[0, "#16212C"], [1, "#5AA9E6"]]
)

CSS = """
<style>
.block-container { padding-top: 2.2rem; padding-bottom: 3rem; max-width: 1440px; }
#MainMenu, footer { visibility: hidden; }

div[data-testid="stVerticalBlockBorderWrapper"] {
    background: __CARD__;
    border-color: __BORDER__;
}

.hdr-title { font-size: 1.85rem; font-weight: 700; line-height: 1.15; margin: 0; }
.hdr-meta { display: flex; flex-wrap: wrap; gap: 1.4rem; margin-top: .45rem;
            font-size: .85rem; color: __MUTED__; }

.kpi { background: __CARD__; border: 1px solid __BORDER__; border-radius: 10px;
       padding: 14px 18px 12px 18px; min-height: 132px; }
.kpi-label { font-size: .82rem; color: __MUTED__; }
.kpi-value { font-size: 1.8rem; font-weight: 600; line-height: 1.25; margin: 2px 0 4px 0;
             font-variant-numeric: tabular-nums; white-space: nowrap; }
.kpi-sub { font-size: .78rem; color: __MUTED__; white-space: nowrap; overflow: hidden;
           text-overflow: ellipsis; }

.insight { font-size: .88rem; color: __MUTED__; margin: -.2rem 0 .2rem 0; }
</style>
"""
st.markdown(
    CSS.replace("__CARD__", TOKENS["card"]).replace("__BORDER__", TOKENS["border"]).replace("__MUTED__", TOKENS["muted"]),
    unsafe_allow_html=True,
)


# --------------------------------------------------------------------------- #
# HELPERS
# --------------------------------------------------------------------------- #
def get_secret(key, default):
    try:
        return st.secrets[key]
    except Exception:  # noqa: BLE001
        return default


def _legend_names(fig):
    names = []
    for t in fig.data:
        if getattr(t, "showlegend", None) is False:
            continue
        if t.type == "pie":
            names += [str(x) for x in (t.labels if getattr(t, "labels", None) is not None else [])]
        elif t.type != "treemap" and getattr(t, "name", None):
            names.append(str(t.name))
    return names


def show(fig, height=420, chars=110, legend="top"):
    """Plotly chart, consistent layout. Colours Streamlit theme se aate hain (light/dark auto).
    chars = ek legend row me kitne characters aate hain (half-width chart ke liye ~55)."""
    if legend == "right":
        top = 56
        legend_cfg = dict(orientation="v", x=1.0, xanchor="left", y=0.5, yanchor="middle", title_text="")
    else:
        names = _legend_names(fig)
        rows = -(-sum(len(n) + 7 for n in names) // chars) if names else 0
        top = 58 + 22 * rows if rows else 56
        legend_cfg = dict(orientation="h", yanchor="bottom", y=1.0, xanchor="left", x=0, title_text="")
    fig.update_layout(
        height=height,
        margin=dict(l=8, r=8, t=top, b=8),
        title=dict(x=0, xanchor="left", font=dict(size=15)),
        legend=legend_cfg,
    )
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(gridcolor=TOKENS["grid"], zerolinecolor=TOKENS["grid"])
    try:
        st.plotly_chart(fig, width="stretch")
    except TypeError:
        st.plotly_chart(fig, use_container_width=True)


def show_df(df, **kwargs):
    try:
        st.dataframe(df, width="stretch", hide_index=True, **kwargs)
    except TypeError:
        st.dataframe(df, use_container_width=True, hide_index=True, **kwargs)


def kpi(col, label, value, subs=(), tip=""):
    lines = "".join(f'<div class="kpi-sub">{html.escape(str(s))}</div>' for s in subs)
    col.markdown(
        f'<div class="kpi" title="{html.escape(tip)}"><div class="kpi-label">{html.escape(label)}</div>'
        f'<div class="kpi-value">{html.escape(value)}</div>{lines}</div>',
        unsafe_allow_html=True,
    )


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
    """'1,23,456.50', '₹ 5,000', '(1,000)' (accounting negative) sab handle karta hai."""
    s = series.fillna("").astype(str).str.strip()
    negative = s.str.match(r"^\(.*\)$")
    s = s.str.replace(r"[₹,\s()]", "", regex=True)
    num = pd.to_numeric(s, errors="coerce")
    return num.where(~negative, -num)


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

    issues["Rows with a budget value"] = int((df["Budget"] != 0).sum())
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
# HEADER + DATA SOURCE
# --------------------------------------------------------------------------- #
head_left, head_right = st.columns([0.74, 0.26], vertical_alignment="center")
title_slot = head_left.empty()

with head_right:
    b1, b2 = st.columns(2)
    with b1.popover("Data source"):
        source = st.radio("Source", ["Google Sheet", "Upload file"], horizontal=True)
        sheet_id, gid, upload = DEFAULT_SHEET_ID, DEFAULT_GID, None
        if source == "Google Sheet":
            sheet_id = st.text_input("Sheet ID", str(get_secret("SHEET_ID", DEFAULT_SHEET_ID)))
            gid = st.text_input("Tab gid", str(get_secret("SHEET_GID", DEFAULT_GID)))
        else:
            upload = st.file_uploader("xlsx ya csv", type=["xlsx", "csv"])
    if b2.button("Refresh data"):
        st.cache_data.clear()
        st.rerun()


def header(meta_items=()):
    meta = "".join(f"<span>{html.escape(m)}</span>" for m in meta_items)
    title_slot.markdown(
        f'<div class="hdr-title">{html.escape(TITLE)}</div><div class="hdr-meta">{meta}</div>',
        unsafe_allow_html=True,
    )


try:
    if source == "Google Sheet":
        data, issues, loaded_at = load_google_sheet(sheet_id.strip(), gid.strip())
    else:
        if upload is None:
            header()
            st.info("'Data source' se xlsx ya csv upload karo.")
            st.stop()
        raw_df = pd.read_csv(upload, dtype=str) if upload.name.lower().endswith(".csv") else pd.read_excel(upload, dtype=str)
        data, issues = clean_data(raw_df)
        loaded_at = datetime.now().strftime("%d %b %Y, %H:%M")
except Exception as exc:  # noqa: BLE001
    header()
    st.error(f"Data load nahi hua: {exc}")
    st.caption(
        "Check karo: Sheet 'Anyone with the link – Viewer' pe hai, Sheet ID / gid sahi hai, "
        "ya 'Data source' me 'Upload file' use karo."
    )
    st.stop()

if data.empty:
    header()
    st.warning("Sheet me koi valid row nahi mili.")
    st.stop()

# --------------------------------------------------------------------------- #
# FILTERS (top panel)
# --------------------------------------------------------------------------- #
def reset_filters():
    for _, _, key in FILTERS:
        st.session_state[key] = []


def keep_valid(key, options):
    st.session_state[key] = [v for v in st.session_state.get(key, []) if v in options]


sheet_totals = data.groupby("Sheet Name")["Amount"].sum().sort_values(ascending=False)
sheet_options = list(sheet_totals.index)
if "f_sheet" not in st.session_state:
    # Alag sheets ke amounts ka scale alag hota hai -> default me sabse bada sheet
    st.session_state["f_sheet"] = sheet_options[:1]
else:
    keep_valid("f_sheet", sheet_options)

with st.container(border=True):
    r1 = st.columns([2.6, 1, 1, 1.2, 0.7], vertical_alignment="bottom")
    sel_sheets = r1[0].multiselect("Sheet name", sheet_options, key="f_sheet")
    scope = data[data["Sheet Name"].isin(sel_sheets)] if sel_sheets else data.iloc[0:0]

    total_scope = scope["Amount"].sum()
    default_unit = 0 if total_scope >= 1e7 else 1 if total_scope >= 1e5 else 2

    r2 = st.columns(4)
    slots = [r1[1], r1[2], r2[0], r2[1], r2[2], r2[3]]
    for (label, col, key), slot in zip(FILTERS, slots):
        options = sorted(scope[col].unique())
        keep_valid(key, options)
        slot.multiselect(label, options, key=key, placeholder="All")

    unit_label = r1[3].selectbox("Amount unit", list(UNITS.keys()), index=default_unit)
    r1[4].button("Reset", on_click=reset_filters, help="Sheet aur unit chhodke baaki filters clear karta hai")
    if len(sheet_options) > 1:
        st.caption("Alag sheets ke amount ka scale alag hota hai, isliye default me sabse bada sheet select hai.")


def apply_filters(df):
    out = df
    for _, col, key in FILTERS:
        picked = st.session_state.get(key, [])
        if picked:
            out = out[out[col].isin(picked)]
    return out


divisor, unit_sym = UNITS[unit_label]
axis_title = f"Amount ({unit_sym})"


def add_units(df):
    df = df.copy()
    df["Amt"] = df["Amount"] / divisor
    df["Budget_u"] = df["Budget"] / divisor
    return df


def fmt(value):
    if unit_sym == "₹":
        return f"₹{value:,.0f}"
    return f"₹{value / divisor:,.2f} {unit_sym}"


if not sel_sheets:
    header()
    st.warning("Kam se kam ek sheet chuno.")
    st.stop()

f = add_units(apply_filters(scope))
if f.empty:
    header([f"Sheet: {', '.join(sel_sheets)}"])
    st.warning("In filters me koi data nahi hai. 'Reset' dabao ya filters badlo.")
    st.stop()

header([f"Sheet: {', '.join(sel_sheets)}", f"Rows in view: {len(f):,}", f"Data refreshed: {loaded_at}"])

# Top-N expense types + Others (charts ko readable rakhne ke liye)
type_rank = f.groupby("Expense Type")["Amount"].sum().sort_values(ascending=False)
top_types = list(type_rank.index[:TOP_EXPENSE_TYPES])
f["Type Group"] = f["Expense Type"].where(f["Expense Type"].isin(top_types), "Others")
type_order = top_types + (["Others"] if (f["Type Group"] == "Others").any() else [])
color_map = {t: PALETTE[i % len(PALETTE)] for i, t in enumerate(top_types)}
color_map["Others"] = OTHERS_COLOR

# --------------------------------------------------------------------------- #
# KPIs
# --------------------------------------------------------------------------- #
total = f["Amount"].sum()
proj = f.groupby("Project Code")["Amount"].sum().sort_values(ascending=False)
n_proj = int(proj.shape[0])
avg_proj = total / n_proj if n_proj else 0
top_code = proj.index[0]
top_val = proj.iloc[0]
top_share = (top_val / total * 100) if total > 0 else 0
top_row = f[f["Project Code"] == top_code].iloc[0]
zero_rows = int((f["Amount"] == 0).sum())

st.write("")
k1, k2, k3, k4, k5 = st.columns(5)
kpi(k1, "Total spend", fmt(total), [f"{len(f):,} rows"])
kpi(k2, "Projects", f"{n_proj:,}", ["unique project codes"])
kpi(k3, "Average per project", fmt(avg_proj), ["total / projects"])
kpi(
    k4, "Biggest project", fmt(top_val),
    [f"{top_share:.1f}% of total", f"{top_row['Client']}/{top_row['Location']} ({top_row['Entity']})"],
    tip=top_code,
)
kpi(k5, "Zero-amount rows", f"{zero_rows:,}", ["details: Data quality tab"])
st.write("")

tab_over, tab_ce, tab_proj, tab_heat, tab_budget, tab_data, tab_dq = st.tabs(
    ["Overview", "Client & entity", "Projects", "Heatmap", "Budget vs actual", "Data", "Data quality"]
)

# --------------------------------------------------------------------------- #
# TAB: OVERVIEW
# --------------------------------------------------------------------------- #
with tab_over:
    with st.container(border=True):
        view = st.radio("Trend view", ["Monthly", "Quarterly"], horizontal=True, label_visibility="collapsed")

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
                    title="Monthly spend by expense type",
                )
                fig.update_layout(hovermode="x unified")
                show(fig)
                peak = m.groupby("Period")["Amount"].sum().sort_values(ascending=False)
                if total > 0 and len(peak):
                    st.markdown(
                        f'<div class="insight">Peak month: {html.escape(peak.index[0])} with {fmt(peak.iloc[0])} '
                        f'({peak.iloc[0] / total * 100:.0f}% of total). Khaali mahine bhi axis par dikhte hain.</div>',
                        unsafe_allow_html=True,
                    )
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
            fig.update_layout(hovermode="x unified")
            show(fig)

    c1, c2 = st.columns(2)
    with c1, st.container(border=True):
        fq = f.groupby(["Financial Year", "Quarter"], as_index=False)["Amt"].sum()
        fq = fq[fq["Amt"] != 0]
        fy_order = sorted(fq["Financial Year"].unique(), key=lambda x: (x == "Unknown", x))
        fig = px.bar(
            fq, x="Financial Year", y="Amt", color="Quarter", barmode="group",
            text_auto=".2s",
            category_orders={"Quarter": ["Q1", "Q2", "Q3", "Q4", "Unknown"], "Financial Year": fy_order},
            color_discrete_sequence=PALETTE,
            labels={"Amt": axis_title, "Financial Year": ""},
            title="Financial year and quarter",
        )
        show(fig, 400, chars=55)
    with c2, st.container(border=True):
        mix = f.groupby("Type Group", as_index=False)["Amt"].sum()
        mix = mix[mix["Amt"] > 0]
        fig = px.pie(
            mix, names="Type Group", values="Amt", hole=0.58,
            color="Type Group", color_discrete_map=color_map,
            category_orders={"Type Group": type_order},
            title="Expense type share",
        )
        fig.update_traces(textinfo="percent", sort=False, marker=dict(line=dict(color=TOKENS["card"], width=2)))
        show(fig, 400, legend="right")

# --------------------------------------------------------------------------- #
# TAB: CLIENT & ENTITY
# --------------------------------------------------------------------------- #
with tab_ce:
    cl = f.groupby("Client", as_index=False)["Amt"].sum().sort_values("Amt")
    row_h = max(340, 34 * len(cl) + 130)
    c1, c2 = st.columns(2)
    with c1, st.container(border=True):
        fig = px.bar(
            cl, x="Amt", y="Client", orientation="h", text_auto=".2f",
            color_discrete_sequence=[PALETTE[0]],
            labels={"Amt": axis_title, "Client": ""},
            title="Spend by client / prefix",
        )
        show(fig, row_h, chars=55)
    with c2, st.container(border=True):
        en = f.groupby(["Entity", "Type Group"], as_index=False)["Amt"].sum()
        ent_order = list(f.groupby("Entity")["Amt"].sum().sort_values(ascending=False).index)
        fig = px.bar(
            en, x="Entity", y="Amt", color="Type Group",
            category_orders={"Entity": ent_order, "Type Group": type_order},
            color_discrete_map=color_map,
            labels={"Amt": axis_title, "Entity": ""},
            title="Spend by entity and expense type",
        )
        show(fig, row_h, chars=55)

    c3, c4 = st.columns([2, 1])
    with c3, st.container(border=True):
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
    with c4, st.container(border=True):
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
    st.markdown(f'<div class="insight">{n_80} projects total spend ka 80% bana rahe hain (out of {n_proj}).</div>', unsafe_allow_html=True)

    with st.container(border=True):
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
        fig.update_yaxes(
            title_text="Cumulative %", range=[0, 102], tickvals=[0, 25, 50, 75, 100],
            showgrid=False, secondary_y=True,
        )
        fig.add_hline(y=80, line_dash="dot", line_color=TOKENS["muted"], secondary_y=True)
        fig.update_xaxes(tickangle=-40)
        fig.update_layout(title=f"Top {top_n} projects (Pareto)")
        show(fig, 540)

    tl = f[f["Start Date"].notna()].groupby(["Project Code", "Start Date", "Client"], as_index=False)["Amt"].sum()
    tl = tl[tl["Amt"] > 0]
    if not tl.empty:
        with st.container(border=True):
            fig = px.scatter(
                tl, x="Start Date", y="Amt", size="Amt", color="Client",
                hover_name="Project Code", color_discrete_sequence=PALETTE,
                labels={"Amt": axis_title, "Start Date": ""},
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
        with st.container(border=True):
            fig = px.imshow(
                pv, text_auto=".1f", aspect="auto", color_continuous_scale=HEAT_SCALE,
                labels=dict(x=col_dim, y=row_dim, color=axis_title),
                title=f"{row_dim} x {col_dim}",
            )
            show(fig, max(380, 36 * len(pv) + 150))

# --------------------------------------------------------------------------- #
# TAB: BUDGET VS ACTUAL
# --------------------------------------------------------------------------- #
with tab_budget:
    has_budget = data["Budget"].abs() > 0
    if not has_budget.any():
        st.info(
            "Poore data me 'Budget' column 0 hai, isliye Budget vs Actual nahi ban sakta. "
            "Sheet me budget values aate hi ye tab apne aap chalne lagega."
        )
    else:
        # Budget ka scope sheet-filter se alag hai: default me wahi sheets jinme budget hai
        cov = (
            data.assign(HasBudget=has_budget)
            .groupby("Sheet Name")
            .agg(Rows=("Amount", "size"), **{"Rows with budget": ("HasBudget", "sum")},
                 Budget=("Budget", "sum"), Actual=("Amount", "sum"))
        )
        budget_sheets = list(cov.index[cov["Rows with budget"] > 0])

        opt1, opt2, opt3 = st.columns([2.2, 1.2, 1.2], vertical_alignment="bottom")
        b_sheets = opt1.multiselect("Sheets", list(cov.index), default=budget_sheets)
        by = opt2.selectbox("Group by", ["Project Code", "Client", "Entity", "Location", "Financial Year"])
        only_budget = opt3.checkbox("Sirf budget wali rows", value=True)
        st.caption("Upar wale Filters (year, client, entity...) yahan bhi lagte hain. Sheet selection yahan alag hai.")

        b = add_units(apply_filters(data[data["Sheet Name"].isin(b_sheets)]))
        if only_budget:
            b = b[b["Budget"].abs() > 0]

        if b.empty:
            st.warning("Is selection me budget wali koi row nahi hai.")
        else:
            bg = b.groupby(by, as_index=False).agg(Budget=("Budget_u", "sum"), Actual=("Amt", "sum"))
            bg["Variance"] = bg["Budget"] - bg["Actual"]
            bg["Utilisation %"] = np.where(bg["Budget"] > 0, bg["Actual"] / bg["Budget"] * 100, np.nan)
            bg = bg.sort_values("Actual", ascending=False)

            tb, ta = bg["Budget"].sum(), bg["Actual"].sum()
            over = int((bg["Utilisation %"] > 100).sum())
            q1, q2, q3, q4 = st.columns(4)
            kpi(q1, "Total budget", f"₹{tb:,.2f} {unit_sym}" if unit_sym != "₹" else f"₹{tb:,.0f}", [f"{len(bg)} groups by {by.lower()}"])
            kpi(q2, "Actual spend", f"₹{ta:,.2f} {unit_sym}" if unit_sym != "₹" else f"₹{ta:,.0f}", ["same rows"])
            kpi(q3, "Utilisation", f"{(ta / tb * 100) if tb else 0:,.1f}%", ["actual / budget"])
            kpi(q4, "Over budget", f"{over}", ["groups with actual > budget"])
            st.write("")

            with st.container(border=True):
                plot = bg.head(20).melt(id_vars=by, value_vars=["Budget", "Actual"], var_name="Type", value_name="Value")
                fig = px.bar(
                    plot, x=by, y="Value", color="Type", barmode="group",
                    color_discrete_map={"Budget": OTHERS_COLOR, "Actual": PALETTE[0]},
                    labels={"Value": axis_title, by: ""},
                    title=f"Budget vs actual by {by.lower()} (top 20 by actual)",
                )
                fig.update_xaxes(tickangle=-40)
                show(fig, 500)

            util = bg[bg["Budget"] > 0].sort_values("Utilisation %", ascending=False).head(20).sort_values("Utilisation %")
            if not util.empty:
                util = util.assign(Status=np.where(util["Utilisation %"] > 100, "Over budget", "Within budget"))
                with st.container(border=True):
                    fig = px.bar(
                        util, x="Utilisation %", y=by, orientation="h", color="Status", text_auto=".0f",
                        color_discrete_map={"Over budget": OVER_COLOR, "Within budget": PALETTE[2]},
                        labels={by: ""},
                        title="Budget utilisation % (100% = poora budget use)",
                    )
                    fig.add_vline(x=100, line_dash="dash", line_color=TOKENS["muted"])
                    show(fig, max(340, 30 * len(util) + 140))

            st.caption("Budget rows ka sum liya gaya hai. Agar ek project ke multiple rows me same budget repeat hota hai, to total double count hoga.")
            table = bg.round(2)
            try:
                show_df(
                    table,
                    column_config={
                        "Utilisation %": st.column_config.ProgressColumn(
                            "Utilisation %", format="%.0f%%", min_value=0, max_value=150
                        )
                    },
                )
            except Exception:  # noqa: BLE001
                show_df(table)

        with st.expander("Sheet-wise budget coverage"):
            show_df(cov.reset_index().round(2))

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
    st.caption(f"{len(table):,} rows (upar ke filters lage hue hain)")
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

    d1, d2, _ = st.columns([1, 1, 2])
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
    st.caption("Ye checks poori sheet par hain (filters se independent).")
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
