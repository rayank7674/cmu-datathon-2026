"""The only place colours, type and spacing are defined. The rules are in DESIGN.md.

Flat surfaces, 1px hairlines, 4px radius, a 4/8 spacing scale, Geist for text and JetBrains Mono
(tabular) for every number. One accent, and red means emergency only. Colours are CSS variables
picked from the active Streamlit theme ([theme.light] / [theme.dark] in .streamlit/config.toml),
so native widgets, tables and maps switch together with the parts drawn here.
"""
import html as _html

import streamlit as st

TOKENS = {
    "light": {"bg": "#FFFFFF", "surface": "#F5F5F4", "border": "#E3E3E0", "ink": "#111213", "muted": "#62646A",
              "accent": "#0B6B5D", "red": "#B42318", "red-soft": "#FDF0EE", "warn": "#8A5A00"},
    "dark": {"bg": "#0C0D0E", "surface": "#161718", "border": "#2A2B2D", "ink": "#EDEDEC", "muted": "#9A9CA1",
             "accent": "#45C4AD", "red": "#FF6A5E", "red-soft": "#2A1412", "warn": "#E3B25A"},
}
# the one data series colour and the caveat colour, readable on both themes
SERIES, CAVEAT = "#1F8A78", "#C9941F"
INDIGO, AMBER = SERIES, CAVEAT          # old names, kept so existing callers keep working
# evidence groups are data: a fixed scale, shown as a square swatch or a bar fill
TIER_COLOUR = {1: "#2E8B62", 2: "#7DB89A", 3: "#C9941F", 4: "#C4652B", 5: "#8B8D93", None: SERIES}
TIER_SHORT = {1: "States everything needed", 2: "States an accepted alternative", 3: "States part of it",
              4: "Only a label or hint", 5: "States none of it", None: "Fits the likely cause"}


def mode() -> str:
    try:
        return "dark" if st.context.theme.type == "dark" else "light"
    except Exception:
        return "light"


def tok(name: str) -> str:
    return TOKENS[mode()][name]


def ink() -> str:
    return tok("ink")


def ramp() -> list:
    """Sequential ramp for heatmaps: low to high, in the accent hue."""
    return (["#141918", "#17403A", "#1F7566", "#45C4AD", "#C9F2E9"] if mode() == "dark"
            else ["#F2F6F5", "#BFDCD6", "#6FB3A6", "#1F7566", "#0A3B34"])


CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap');
:root { %(vars)s --sans: 'Geist', system-ui, -apple-system, 'Segoe UI', sans-serif; --mono: 'JetBrains Mono', ui-monospace, 'SF Mono', Menlo, monospace; }
html, body, .stApp, .stMarkdown, .stButton button, input, textarea, [data-testid="stWidgetLabel"],
[data-testid="stChatMessage"], [data-baseweb="select"], [data-testid="stCaptionContainer"], [data-testid="stPopover"] button,
[data-testid="stExpander"] summary { font-family: var(--sans); }
.stApp { background: var(--bg); }
.block-container { padding-top: 72px; padding-bottom: 48px; max-width: 1200px; }
h1, h2, h3 { color: var(--ink); font-weight: 600 !important; font-family: var(--sans) !important; }
h1 { font-size: 28px !important; line-height: 32px !important; letter-spacing: -0.02em; padding: 0 0 4px 0 !important; }
h2 { font-size: 20px !important; line-height: 28px !important; letter-spacing: -0.01em; padding: 32px 0 8px 0 !important; }
h3 { font-size: 16px !important; padding: 16px 0 4px 0 !important; }
.stMarkdown p, .stMarkdown li { font-size: 16px; line-height: 24px; }
[data-testid="stCaptionContainer"] p { font-size: 13px; line-height: 20px; color: var(--muted); }
.num, .vf-fact .v, .vf-fig .v, table.vf-ev td.n { font-family: var(--mono); font-variant-numeric: tabular-nums; }

/* top bar: text links, current view underlined */
header[data-testid="stHeader"] { background: var(--bg); border-bottom: 1px solid var(--border); height: 48px; }
header[data-testid="stHeader"] [data-testid="stTopNavLink"] { border-radius: 0; padding: 14px 0 12px 0; margin: 0 24px 0 0;
  background: transparent !important; border-bottom: 2px solid transparent; }
header[data-testid="stHeader"] [data-testid="stTopNavLink"] * { color: var(--muted) !important; font-size: 14px; font-weight: 500; }
header[data-testid="stHeader"] [data-testid="stTopNavLink"]:hover * { color: var(--ink) !important; }
header[data-testid="stHeader"] [data-testid="stTopNavLink"][aria-current="page"] { border-bottom-color: var(--ink); }
header[data-testid="stHeader"] [data-testid="stTopNavLink"][aria-current="page"] * { color: var(--ink) !important; }
.st-key-vf_theme { position: fixed; top: 8px; right: 16px; z-index: 1000001; width: auto !important; }
.st-key-vf_theme button { min-height: 32px; padding: 0 12px; background: transparent; border: 1px solid var(--border); border-radius: 4px; }
.st-key-vf_theme button p { font-size: 13px; color: var(--muted); }

/* emergency line: first on every view */
.vf-emerg { background: var(--red-soft); border: 1px solid var(--red); border-radius: 4px; padding: 8px 12px; display: flex;
  flex-wrap: wrap; align-items: center; gap: 8px 16px; margin: 0 0 32px 0; font-size: 14px; line-height: 20px; color: var(--ink); }
.vf-emerg b { font-weight: 600; color: var(--red); }
.vf-emerg a { color: var(--ink) !important; font-weight: 500; text-decoration: underline; text-underline-offset: 3px; white-space: nowrap; }
.vf-emerg a .num { font-weight: 500; }
.vf-emerg span.small { margin-left: auto; color: var(--muted); font-size: 13px; }
.vf-redcard { border-left: 2px solid var(--red); padding: 4px 0 4px 12px; margin: 0 0 16px 0; color: var(--ink); font-size: 16px; line-height: 24px; }
.vf-redcard .t { color: var(--red); font-weight: 600; font-size: 20px; line-height: 28px; }
.vf-redcard a { color: var(--red); font-weight: 600; }

/* patient start screen */
.vf-hero h1 { font-size: 40px !important; line-height: 44px !important; letter-spacing: -0.03em; margin: 32px 0 8px 0; }
.vf-hero p { color: var(--muted); font-size: 16px; line-height: 24px; margin: 0 0 24px 0; }
.vf-lede { color: var(--muted); font-size: 16px; line-height: 24px; max-width: 68ch; margin: 0 0 24px 0; }
.vf-fine { color: var(--muted); font-size: 13px; line-height: 20px; }

/* one ruled block, not a card */
.vf-card { border: 1px solid var(--border); border-radius: 4px; padding: 16px; margin-bottom: 24px; color: var(--ink); background: var(--bg); }
.vf-card .name { font-size: 20px; font-weight: 600; line-height: 28px; letter-spacing: -0.01em; }
.vf-card .where { color: var(--muted); font-size: 14px; line-height: 20px; }
.vf-card blockquote { border-left: 1px solid var(--border); margin: 12px 0 0 0; padding: 0 0 0 12px; font-size: 14px; line-height: 20px; color: var(--ink); }
.vf-card a.call { display: inline-block; margin-top: 16px; background: var(--ink); color: var(--bg) !important; font-weight: 500;
  text-decoration: none; padding: 8px 12px; border-radius: 4px; font-size: 14px; }
.vf-facts { display: flex; flex-wrap: wrap; gap: 8px 32px; margin: 16px 0 4px 0; padding-top: 12px; border-top: 1px solid var(--border); }
.vf-fact .v { font-size: 20px; font-weight: 500; line-height: 28px; color: var(--ink); }
.vf-fact .l { font-size: 12px; line-height: 16px; color: var(--muted); }
.vf-tag { display: inline-flex; align-items: center; gap: 8px; font-size: 14px; line-height: 20px; color: var(--ink); }
.vf-sw { display: inline-block; width: 8px; height: 8px; flex: none; }
.vf-note { color: var(--muted); font-size: 13px; line-height: 20px; }
.vf-caveat { border-left: 2px solid var(--warn); padding: 4px 0 4px 12px; margin: 0 0 16px 0; font-size: 14px; line-height: 20px; color: var(--ink); }
.vf-caveat b { color: var(--warn); font-weight: 600; }
.vf-empty { color: var(--muted); font-size: 14px; line-height: 20px; padding: 16px 0; border-bottom: 1px solid var(--border); }
.vf-panel-title { font-size: 14px; font-weight: 600; line-height: 20px; color: var(--ink); margin: 8px 0; }

table.vf-ev { width: 100%%; border-collapse: collapse; font-size: 14px; line-height: 20px; color: var(--ink); }
table.vf-ev th { text-align: left; color: var(--muted); padding: 8px 12px 8px 0; font-weight: 400; font-size: 13px; border-bottom: 1px solid var(--border); }
table.vf-ev td { padding: 8px 12px 8px 0; border-bottom: 1px solid var(--border); vertical-align: top; }

/* figures: one ruled row, no tiles */
.vf-figs { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px 32px; margin: 0 0 32px 0;
  padding: 16px 0; border-top: 1px solid var(--border); border-bottom: 1px solid var(--border); }
.vf-fig .v { font-size: 28px; font-weight: 500; line-height: 32px; color: var(--ink); letter-spacing: -0.02em; }
.vf-fig .l { font-size: 14px; line-height: 20px; color: var(--ink); margin-top: 4px; }
.vf-fig .s { font-size: 12px; line-height: 16px; color: var(--muted); margin-top: 4px; }
.vf-fig.warn .v { color: var(--warn); }

/* chat: no avatars, the person's words on the right, answers as plain text */
[data-testid="stChatMessage"] { background: transparent; border: 0; padding: 4px 0; gap: 0; }
[data-testid="stChatMessage"] > div:first-child:not([data-testid="stChatMessageContent"]) { display: none; }
[data-testid="stChatMessage"]:has(.vf-me) { justify-content: flex-end; }
[data-testid="stChatMessage"]:has(.vf-me) [data-testid="stChatMessageContent"] { flex: 0 1 auto; max-width: 85%%; margin-left: auto;
  background: var(--surface); border-radius: 4px; padding: 8px 12px; }
.vf-me { display: none; }
[data-testid="stChatInput"], [data-testid="stChatInput"] > div { border-radius: 4px !important; }
[data-testid="stChatInput"] textarea { font-size: 16px; }

/* native controls: square them off, no shadows */
.stButton button, [data-testid="stPopover"] button, .stButtonGroup button, [data-testid="stExpander"] details,
[data-baseweb="select"] > div, [data-baseweb="input"], [data-baseweb="textarea"], .stDownloadButton button { border-radius: 4px !important; box-shadow: none !important; }
.stButton button[kind="primary"], .stButton button[data-testid="stBaseButton-primary"] { background: var(--ink); border-color: var(--ink); color: var(--bg); }
.stButton button[kind="primary"] p, .stButton button[data-testid="stBaseButton-primary"] p { color: var(--bg); }
.stButton button p, [data-testid="stPopover"] button p, .stButtonGroup button p { font-size: 14px; }
.stButtonGroup > div { flex-wrap: wrap !important; overflow: visible !important; gap: 8px; }
[data-testid="stExpander"] details { border: 1px solid var(--border); }
[data-testid="stAlert"] { border-radius: 4px; }
button:focus-visible, a:focus-visible { outline: 2px solid var(--accent) !important; outline-offset: 2px; }
hr { border-color: var(--border) !important; margin: 32px 0 !important; }
@media (prefers-reduced-motion: reduce) { * { transition: none !important; animation: none !important; } }
</style>
"""


def inject():
    t = TOKENS[mode()]
    st.markdown(CSS % {"vars": " ".join(f"--{k}: {v};" for k, v in t.items())}, unsafe_allow_html=True)
    theme_toggle()


def theme_toggle():
    """Light / dark switch. It sets Streamlit's own saved theme choice and reloads, so every widget follows."""
    dark = mode() == "dark"
    if st.button("Light" if dark else "Dark", key="vf_theme", help="Switch between light and dark"):
        want = "Light" if dark else "Dark"
        st.html("<script>try{['/','/emr','/policy',window.location.pathname].forEach(function(p){"
                f"window.localStorage.setItem('stActiveTheme-'+p+'-v2', JSON.stringify('{want}'));" "});"
                "window.location.reload();}catch(e){}</script>", unsafe_allow_javascript=True)


def esc(v) -> str:
    return _html.escape("" if v is None else str(v))


def emergency_strip():
    st.markdown(
        "<div class='vf-emerg' role='alert'><b>Emergency? Call now.</b>"
        "<a href='tel:108'><span class='num'>108</span> ambulance</a>"
        "<a href='tel:112'><span class='num'>112</span> any emergency</a>"
        "<a href='tel:102'><span class='num'>102</span> pregnancy or newborn</a>"
        "<span class='small'>Do not wait for this app.</span></div>", unsafe_allow_html=True)


def tag(text, colour) -> str:
    """A square swatch and plain text. Used for evidence groups; never a pill."""
    return f"<span class='vf-tag'><span class='vf-sw' style='background:{colour}'></span>{esc(text)}</span>"


def chip(text, colour=None, soft=False) -> str:      # old name
    return tag(text, colour or SERIES)


def tier_chip(tier) -> str:
    return tag(TIER_SHORT.get(tier, TIER_SHORT[None]), TIER_COLOUR.get(tier, TIER_COLOUR[None]))


def kpis(items):
    """A ruled row of figures. items: (value, label, small note, caveat?)"""
    cells = "".join(
        f"<div class='vf-fig{' warn' if (len(i) > 3 and i[3]) else ''}'><div class='v'>{esc(i[0])}</div>"
        f"<div class='l'>{esc(i[1])}</div>" + (f"<div class='s'>{esc(i[2])}</div>" if len(i) > 2 and i[2] else "") + "</div>"
        for i in items)
    st.markdown(f"<div class='vf-figs'>{cells}</div>", unsafe_allow_html=True)


def signed_in_user():
    """Email the Databricks Apps proxy forwards for the signed-in viewer, or None (e.g. run locally)."""
    try:
        h = st.context.headers
        return h.get("x-forwarded-email") or h.get("x-forwarded-user") or None
    except Exception:
        return None
