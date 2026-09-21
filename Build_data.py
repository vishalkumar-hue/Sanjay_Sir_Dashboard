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
        "PO Amount": "Budget",   # <-- ADDED: PO Amount ko Budget maana ja raha hai
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


# ---------------------------------------------------------------- dashboard tweaks
# Dashboard HTML ko file me chhue bina, data daalte waqt ye chhote badlaav lagte hain:
#   1) "Data Quality" tab hata diya
#   2) har chart me values bina click/hover ke dikhti hain (data labels)
DL_JS = r"""
// ---- data labels: har chart me value bina click kiye dikhe ----
function dlThemeText(){ try { return getComputedStyle(document.documentElement).getPropertyValue('--text').trim() || '#e7ecf5'; } catch(e){ return '#e7ecf5'; } }
function dlOutsideColor(){ return dlThemeText(); }
function dlNum(v){ return Number(v).toLocaleString('en-IN', {maximumFractionDigits: (unitKey==='rs' ? 0 : 2)}); }
function dlType(ctx){ return ctx.dataset.type || ctx.chart.config.type; }
function dlStacked(ch){ const sc = ch.options.scales || {}; return !!((sc.x && sc.x.stacked) || (sc.y && sc.y.stacked)); }
function dlStackMax(ch){
  if (ch._dlStackMax !== undefined) return ch._dlStackMax;
  const bars = ch.data.datasets.filter(d => (d.type || ch.config.type) === 'bar');
  const n = (ch.data.labels || []).length;
  let m = 0;
  for (let i = 0; i < n; i++){
    let t = 0;
    bars.forEach(d => { const v = +d.data[i]; if (isFinite(v) && v > 0) t += v; });
    if (t > m) m = t;
  }
  ch._dlStackMax = m;
  return m;
}
function dlInside(ctx){
  const t = ctx.chart.config.type;
  if (t === 'doughnut' || t === 'pie') return true;
  return dlType(ctx) === 'bar' && dlStacked(ctx.chart);
}
function dlDisplay(ctx){
  const ch = ctx.chart, cfg = ch.config.type;
  if (cfg === 'bubble') return false;
  const v = ctx.dataset.data[ctx.dataIndex];
  if (typeof v !== 'number' || !isFinite(v) || v === 0) return false;
  if (cfg === 'doughnut' || cfg === 'pie') return true;
  if (dlType(ctx) === 'bar' && dlStacked(ch)) return v >= 0.05 * dlStackMax(ch);
  return true;
}
function totalLine(datasets, n){
  const data = [];
  for (let i = 0; i < n; i++){ data.push(datasets.reduce((a, d) => a + (+d.data[i] || 0), 0)); }
  return { type:'line', label:'Total', data, showLine:false, pointRadius:0, pointHoverRadius:0, borderWidth:0,
    backgroundColor:'transparent', borderColor:'transparent',
    datalabels:{ display: ctx => ctx.dataset.data[ctx.dataIndex] > 0, anchor:'end', align:'top', offset:2,
                 color: () => dlThemeText(), font:{ size:11, weight:'700' } } };
}
Chart.defaults.set('layout', { padding:{ top:22, right:30, left:4, bottom:4 } });
Chart.defaults.set('plugins.legend.labels', { filter: item => item.text !== 'Total' });
Chart.defaults.set('plugins.datalabels', {
  display: dlDisplay,
  clamp: true,
  clip: false,
  offset: 2,
  font: { size:10, weight:'600' },
  color: ctx => dlInside(ctx) ? '#fff' : dlThemeText(),
  anchor: ctx => dlInside(ctx) ? 'center' : 'end',
  align: ctx => dlInside(ctx) ? 'center' : (dlType(ctx) === 'line' ? 'top' : 'end'),
  rotation: ctx => {
    const ch = ctx.chart;
    if (dlType(ctx) !== 'bar' || dlStacked(ch) || ch.options.indexAxis === 'y') return 0;
    const bars = ch.data.datasets.filter(d => (d.type || ch.config.type) === 'bar').length;
    return (ch.data.labels.length * bars) > 16 ? -90 : 0;
  },
  formatter: (v, ctx) => {
    if (v === null || v === undefined || typeof v === 'object') return '';
    if (ctx.dataset.yAxisID === 'y1') return Math.round(v) + '%';
    return dlNum(v);
  }
});
"""


def _sub(html, name, pattern, repl, skipped):
    new, n = re.subn(pattern, (lambda m: repl) if isinstance(repl, str) else repl, html)
    if n == 0:
        skipped.append(name)
    return new


def apply_dashboard_tweaks(html):
    """(html, skipped) return karta hai; skipped = jo badlaav lag nahi paye."""
    skipped = []
    html = _sub(html, "data labels",
                r"Chart\.defaults\.set\('plugins\.datalabels',\s*\{\s*display:\s*false\s*\}\);",
                DL_JS, skipped)
    html = _sub(html, "label colours",
                r"color:'#fff',\s*anchor:'end',\s*align:'right'",
                "color:dlOutsideColor(), anchor:'end', align:'right'", skipped)
    html = _sub(html, "trend totals",
                r"const ctx = document\.getElementById\('trendChart'\);",
                "datasets.push(totalLine(datasets, labels.length));\n  const ctx = document.getElementById('trendChart');",
                skipped)
    html = _sub(html, "entity totals",
                r"charts\.entity = new Chart\(document\.getElementById\('entityChart'\), \{",
                "datasets.push(totalLine(datasets, entities.length));\n  charts.entity = new Chart(document.getElementById('entityChart'), {",
                skipped)
    html = _sub(html, "remove Data Quality tab",
                r"[ \t]*<button class=\"tabbtn\" data-tab=\"dq\">Data Quality</button>[ \t]*\n?",
                "", skipped)
    html = _sub(html, "zero-amount KPI text",
                r"details: Data quality tab", "rows with zero amount", skipped)
    return html, skipped


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
        html, skipped = apply_dashboard_tweaks(html)
    except Exception as e:
        st.error(f"Dashboard HTML me data daal nahi paya: {e}")
        return
    if skipped:
        st.warning("Ye badlaav lag nahi paye (HTML alag hai): " + ", ".join(skipped))

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
