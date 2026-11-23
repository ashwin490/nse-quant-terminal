"""Streamlit presentation primitives for the three-page, no-chart dark terminal.

No orders, model decisions, database mutations, or broker logic live here.
All displayed market/user values are HTML escaped.
"""
from __future__ import annotations
import html
from typing import Iterable

THEMES = ("Midnight", "Nebula", "Deep Ocean")
PALETTES = {
    "Midnight": {"accent": "#a78bfa", "accent2": "#34d399", "glow": "#8564fa", "surface": "#121827", "hero": "#191e37"},
    "Nebula": {"accent": "#fb91b1", "accent2": "#fbbf79", "glow": "#f570b1", "surface": "#201629", "hero": "#321a40"},
    "Deep Ocean": {"accent": "#4fafff", "accent2": "#55e0d5", "glow": "#268bea", "surface": "#0d1d29", "hero": "#0d293c"},
}

STYLES = r"""
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700;800&family=Outfit:wght@500;600;700;800&display=swap');
html, body, .stApp, [data-testid="stAppViewContainer"] {font-family:'DM Sans',sans-serif;}
.stApp {background:radial-gradient(ellipse 66% 35% at 92% 1%,color-mix(in srgb,var(--glow),transparent 86%),transparent 80%),radial-gradient(ellipse 54% 26% at 1% 25%,rgba(16,185,129,.05),transparent),#0b0f1a;color:#f0f3fc;}
.block-container {max-width:1180px;padding:1.6rem 2rem 4rem;}
h1,h2,h3 {font-family:'Outfit','DM Sans',sans-serif;letter-spacing:-.025em;color:#f4f6ff;}
[data-testid='stSidebar'] {background:#0d1321;border-right:1px solid #242b40;}
[data-testid='stSidebar'] [data-testid='stMarkdownContainer'] {color:#dbe4f5;}
[data-testid='stSidebar'] h3 {color:#f6f5ff;}
.stTabs [data-baseweb='tab-list'] {gap:6px;background:#151b2b;padding:5px;border:1px solid #283248;border-radius:15px;}
.stTabs [data-baseweb='tab'] {background:transparent;border-radius:11px;font-weight:700;padding:10px 15px;color:#97a8c2;}
.stTabs [aria-selected='true'] {background:linear-gradient(125deg,color-mix(in srgb,var(--accent),#161d30 77%),#242742)!important;color:#f8f8ff!important;box-shadow:inset 0 0 0 1px color-mix(in srgb,var(--accent),transparent 60%);}
.stButton>button[kind='primary'],.stDownloadButton>button[kind='primary'] {background:linear-gradient(112deg,var(--accent),var(--accent2));color:#09111d;border:0;border-radius:11px;font-weight:800;box-shadow:0 8px 25px color-mix(in srgb,var(--accent),transparent 87%);}
.stButton>button:not([kind='primary']),.stDownloadButton>button:not([kind='primary']) {border:1px solid #33425b;background:#1a2234;color:#edf4ff;border-radius:11px;font-weight:650;}
.stButton>button:hover {filter:brightness(1.12);border-color:var(--accent);}
[data-testid='stExpander'] {background:#131b2a;border:1px solid #2b374d;border-radius:14px;}
[data-testid='stAlert'] {border-radius:12px;background:#172238;}
[data-testid='stDataFrame'] {border:1px solid #2a364b;border-radius:13px;overflow:hidden;}
[data-testid='stMetricValue'] {color:#f3f5fe;}
[data-testid='stMetricLabel'] {color:#9eafcb;}
[data-testid='stWidgetLabel'] label {color:#cdd9f1;}
[data-baseweb='input'] {background:#141d2e;border-radius:10px;}
.dash-hero {padding:24px 27px;border:1px solid #303551;background:radial-gradient(circle at 84% 23%,color-mix(in srgb,var(--glow),transparent 78%),transparent 34%),linear-gradient(115deg,var(--hero),#101b2b 84%);border-radius:21px;margin:5px 0 17px;box-shadow:0 20px 65px rgba(0,0,0,.18);}
.dash-eyebrow {font-size:11px;font-weight:800;color:var(--accent);letter-spacing:.15em;text-transform:uppercase;}
.dash-hero h1 {font-size:clamp(27px,3.5vw,39px);line-height:1.13;margin:7px 0 8px;color:#fff;}
.dash-hero h1 span {color:var(--accent2);}
.dash-hero p {font-size:13px;color:#afbed8;max-width:700px;margin:0 0 14px;}
.dash-chips {display:flex;flex-wrap:wrap;gap:7px;align-items:center;}
.dash-chip {border:1px solid #35405c;background:#1a2437;color:#c8d6f0;font-size:11px;padding:6px 11px;border-radius:100px;}
.dash-stats {display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin:0 0 16px;}
.dash-stat {background:var(--surface);border:1px solid #2b364c;border-radius:16px;padding:15px;min-width:0;position:relative;overflow:hidden;}
.dash-stat:before {content:'';position:absolute;top:0;left:0;height:3px;width:100%;background:linear-gradient(90deg,var(--accent),var(--accent2));opacity:.75;}
.dash-stat .ds-label {color:#9db0ce;font-size:11px;font-weight:700;}
.dash-stat .ds-val {color:#fcfcff;font:700 clamp(18px,2.4vw,28px) 'Outfit',sans-serif;line-height:1.3;margin:6px 0 4px;overflow-wrap:anywhere;}
.dash-stat .ds-sub {color:#8195b5;font-size:11px;line-height:1.35;}
.dash-header {display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap;margin:23px 0 10px;}
.dash-header h3 {font-size:19px;font-weight:750;margin:0;color:#f4f6ff;}
.dash-header p {font-size:11px;color:#889bbc;margin:0;}
.signal-card {padding:18px 19px;border:1px solid #303b51;background:linear-gradient(140deg,#182031,#111927 82%);border-radius:17px;margin:9px 0 13px;position:relative;overflow:hidden;transition:border-color .18s,transform .18s;}
.signal-card:before {position:absolute;content:'';width:4px;top:0;bottom:0;left:0;background:#8d9bb3;}
.signal-card.consider:before {background:#34d399;}.signal-card.watch:before {background:#ffc773;}.signal-card.avoid:before {background:#fb7185;}
.signal-card:hover {border-color:#4b5974;transform:translateY(-2px);}
.sig-heading {display:flex;align-items:center;gap:9px;flex-wrap:wrap;}
.sig-name {color:#f8f9ff;font:700 20px 'Outfit',sans-serif;}
.sig-meta {color:#8194b6;font-size:11px;margin-left:auto;}
.sig-pill {font-size:10px;font-weight:850;letter-spacing:.06em;padding:5px 9px;border-radius:999px;color:#041b13;background:#60edbf;}
.sig-pill.watch {background:#ffd18b;color:#33230d;}.sig-pill.avoid {background:#ff9cad;color:#3b101a;}
.sig-items {display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:9px;margin-top:15px;}
.sig-cell {border:1px solid #2d384a;background:#121b29;padding:10px 11px;border-radius:10px;min-width:0;}
.sig-cell small {display:block;font-size:10px;color:#8d9fbd;margin-bottom:5px;}
.sig-cell strong {font-size:15px;color:#f0f5fe;display:block;overflow-wrap:anywhere;}
.sig-timeline {margin-top:12px;border:1px solid #31415a;border-radius:10px;background:#1b2841;padding:11px 12px;color:#d5e5ff;font-size:12px;}
.sig-timeline b {color:var(--accent2);}
.sig-reason {font-size:12px;color:#9aacca;line-height:1.6;margin-top:12px;}
.pane-note {border:1px solid #2e3d56;background:#172339;color:#cad8ec;padding:12px 14px;border-radius:11px;font-size:12px;margin:9px 0;}
.pane-note.warn {border-color:#755b39;background:#2c251d;color:#ffd5a0;}
.learning-event {background:#131d2b;border:1px solid #2a354a;border-radius:11px;padding:12px 15px;margin:8px 0;}
.learning-event b {color:#f1f5ff}.learning-event p {color:#a3b5ce;line-height:1.6;font-size:12px;}
.ledger-caption {color:#91a2bd;font-size:12px;}
@media(max-width:960px) {.dash-stats{grid-template-columns:repeat(2,minmax(0,1fr))}.sig-items{grid-template-columns:repeat(2,minmax(0,1fr))}.block-container{padding:1.1rem 1rem 3rem;}}
@media(max-width:520px){.dash-hero{padding:19px}.sig-heading{gap:7px}.sig-meta{width:100%;margin-left:0}.sig-items{gap:7px}.dash-stat{padding:11px}}
"""


def escape(value) -> str:
    return html.escape(str(value), quote=True)


def theme_css(name: str = 'Midnight') -> str:
    palette = PALETTES.get(name, PALETTES['Midnight'])
    return STYLES + '\n:root{' + ';'.join(f'--{k}:{v}' for k,v in palette.items()) + '}\n'


def hero(stamp: str, market_open: bool) -> str:
    market = '🟢 NSE open' if market_open else '🌙 NSE closed'
    return (f'<div class="dash-hero"><div class="dash-eyebrow">🇮🇳 NSE / AI RESEARCH TERMINAL</div>'
            '<h1>Sharper signals. <span>Less noise.</span></h1>'
            '<p>Stocks, calls and puts in one place. Clear entry, target, stop-loss and evidence-based timing. No charts.</p>'
            '<div class="dash-chips">'
            f'<span class="dash-chip">{escape(market)}</span>'
            f'<span class="dash-chip">🕘 {escape(stamp)} IST</span>'
            '<span class="dash-chip">🧪 Paper trading only</span>'
            '<span class="dash-chip">🔒 No broker orders</span>'
            '</div></div>')


def stats(items: Iterable[tuple[str,str,str]]) -> str:
    return '<div class="dash-stats">' + ''.join(
        f'<div class="dash-stat"><div class="ds-label">{escape(k)}</div><div class="ds-val">{escape(v)}</div><div class="ds-sub">{escape(note)}</div></div>'
        for k,v,note in items) + '</div>'


def header(title: str, note: str='') -> str:
    return f'<div class="dash-header"><h3>{escape(title)}</h3><p>{escape(note)}</p></div>'


def note(message: str, warning: bool=False) -> str:
    return f'<div class="pane-note {"warn" if warning else ""}">{escape(message)}</div>'


def signal_card(name: str, decision: str, meta: str, fields: list[tuple[str,str]],
                reason: str, timing: str = '', timing_detail: str = '') -> str:
    tone = decision.lower() if decision in ('CONSIDER','WATCH','AVOID') else 'watch'
    cells = ''.join(f'<div class="sig-cell"><small>{escape(k)}</small><strong>{escape(v)}</strong></div>' for k,v in fields)
    timing_html = (f'<div class="sig-timeline"><b>⏱ {escape(timing)}</b> · {escape(timing_detail)}</div>' if timing else '')
    return (f'<div class="signal-card {tone}"><div class="sig-heading">'
            f'<span class="sig-name">{escape(name)}</span><span class="sig-pill {tone}">{escape(decision)}</span>'
            f'<span class="sig-meta">{escape(meta)}</span></div><div class="sig-items">{cells}</div>'
            + timing_html + f'<div class="sig-reason">{escape(reason)}</div></div>')
