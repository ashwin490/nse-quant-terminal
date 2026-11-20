"""Accessible colourful, graph-free dashboard presentation.

Only HTML/CSS presentation helpers live here. No trading, portfolio or model logic.
All user-controlled content is escaped before it is included in HTML.
"""
from __future__ import annotations
import hashlib
import html

THEMES = ("Aurora", "Sunrise", "Ocean")

# Palette entries deliberately use CSS literals from this fixed table, not user input.
PALETTES = {
    "Aurora": {"accent": "#5b46d8", "accent2": "#008e98", "warm": "#f06a78", "back": "#f6f4ff", "hero1": "#4029a9", "hero2": "#7348df", "hero3": "#068b9b", "ink": "#192a4b"},
    "Sunrise": {"accent": "#c34561", "accent2": "#b15a13", "warm": "#da4388", "back": "#fff9f4", "hero1": "#852a74", "hero2": "#dd5264", "hero3": "#f2994a", "ink": "#3f2540"},
    "Ocean": {"accent": "#1673c8", "accent2": "#078888", "warm": "#df657a", "back": "#f0fbff", "hero1": "#154b91", "hero2": "#127ac2", "hero3": "#0aa38e", "ink": "#153553"},
}

THEME_CSS = r"""
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700;800&family=Outfit:wght@500;600;700;800&display=swap');
:root { --accent:#5b46d8; --accent2:#008e98; --warm:#f06a78; --back:#f6f4ff; --hero1:#4029a9; --hero2:#7348df; --hero3:#068b9b; --ink:#192a4b; }
html,body,.stApp { font-family:'DM Sans',sans-serif; }
.stApp {background:radial-gradient(circle at 93% 9%,rgba(124,107,246,.12),transparent 25%),radial-gradient(circle at 3% 43%,rgba(18,189,174,.10),transparent 33%),var(--back);color:var(--ink)}
.block-container {max-width:1320px;padding-top:1.3rem;padding-bottom:4rem}
#MainMenu,footer {visibility:hidden}
h1,h2,h3 {font-family:'Outfit','DM Sans',sans-serif;letter-spacing:-.025em}
[data-testid='stSidebar'] {background:linear-gradient(168deg,#fdfcff,#f7f6ff 48%,#effbf9);border-right:1px solid #e0e3f1}
[data-testid='stSidebar'] [data-testid='stMarkdownContainer'] {color:var(--ink)}
.stTabs [data-baseweb='tab-list'] {gap:6px;background:rgba(255,255,255,.91);padding:7px;border:1px solid #e5e6f1;border-radius:17px;box-shadow:0 7px 30px rgba(44,42,112,.07)}
.stTabs [data-baseweb='tab'] {border-radius:12px;font-weight:750;padding:10px 15px;color:#5c668a;transition:.2s}
.stTabs [aria-selected='true'] {background:linear-gradient(112deg,var(--accent),var(--accent2))!important;color:white!important;box-shadow:0 7px 16px rgba(41,54,142,.18)}
.stButton > button[kind='primary'] {background:linear-gradient(110deg,var(--accent),var(--accent2));color:white;border:none;border-radius:13px;box-shadow:0 6px 17px rgba(49,77,152,.19);font-weight:750}
.stButton > button:not([kind='primary']), .stDownloadButton > button {border:1px solid #d7dcef;background:#fff;color:var(--ink);border-radius:13px;font-weight:700}
.stButton > button:hover,.stDownloadButton > button:hover {border-color:var(--accent)!important;transform:translateY(-1px)}
[data-testid='stAlert'] {border-radius:15px}
[data-testid='stExpander'] {border:1px solid #e1e4f2;border-radius:14px;background:#fff}
[data-testid='stDataFrame'] {border:1px solid #dfe5f2;border-radius:14px;overflow:hidden}
.hero-new {position:relative;overflow:hidden;padding:29px 33px 27px;border-radius:26px;margin:1px 0 20px;background:linear-gradient(110deg,var(--hero1),var(--hero2) 55%,var(--hero3));box-shadow:0 15px 35px rgba(66,49,140,.21)}
.hero-new:after {content:'';pointer-events:none;position:absolute;width:390px;height:390px;border:1px solid rgba(255,255,255,.20);border-radius:50%;right:-75px;top:-195px;box-shadow:0 0 0 55px rgba(255,255,255,.05),0 0 0 120px rgba(255,255,255,.035)}
.hero-new .eyebrow {position:relative;z-index:1;color:#e1eaff;font-size:11px;font-weight:800;text-transform:uppercase;letter-spacing:.14em}
.hero-new h1 {position:relative;z-index:1;font-size:clamp(27px,3.7vw,46px);color:#fff;margin:8px 0 5px;line-height:1.14}
.hero-new h1 .dot {color:#ffcf79}
.hero-new p {position:relative;z-index:1;color:#f0f1ff;font-size:14px;margin:8px 0 0;max-width:700px}
.hero-new .hero-pills {display:flex;flex-wrap:wrap;gap:8px;margin-top:17px;position:relative;z-index:1}
.hero-new .pill {background:rgba(255,255,255,.18);border:1px solid rgba(255,255,255,.34);padding:7px 11px;border-radius:100px;color:#fff;font-weight:750;font-size:12px;backdrop-filter:blur(4px)}
.kpis {display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:13px;margin:0 0 21px}
.kpi {background:#fff;padding:16px 17px;border:1px solid #dfe6f5;border-radius:19px;box-shadow:0 8px 28px rgba(42,51,110,.08);transition:transform .18s,box-shadow .18s;min-width:0;position:relative;overflow:hidden}
.kpi:before {content:'';position:absolute;left:0;right:0;top:0;height:4px;background:linear-gradient(90deg,var(--accent),var(--accent2))}
.kpi .ico {display:inline-flex;background:rgba(255,255,255,.83);border-radius:12px;padding:8px;margin-bottom:7px;font-size:21px}
.kpi .klabel {font-size:12px;font-weight:750;color:#505f80}
.kpi .kval {font-size:clamp(20px,2.1vw,30px);line-height:1.16;font-family:Outfit,sans-serif;font-weight:800;color:var(--ink);margin-top:5px;overflow-wrap:anywhere}
.kpi .ksub {font-size:11px;margin-top:7px;color:#667594}
.kpi:hover,.research:hover {transform:translateY(-3px);box-shadow:0 14px 31px rgba(50,55,122,.14)}
.kpi.lav {background:linear-gradient(155deg,#efebff,#fcfbff);border-color:#d8cffd}
.kpi.lav:before {background:#775bdf}
.kpi.teal {background:linear-gradient(155deg,#dffbf0,#fbfffd);border-color:#b9ecdc}
.kpi.teal:before {background:#12a786}
.kpi.peach {background:linear-gradient(155deg,#fff1df,#fffdfb);border-color:#f6dbb3}
.kpi.peach:before {background:#ed9c3f}
.kpi.sky {background:linear-gradient(155deg,#e6f3ff,#fbfdff);border-color:#c6e1ff}
.kpi.sky:before {background:#398bd9}
.intro-row {display:flex;flex-wrap:wrap;gap:9px;align-items:center;justify-content:space-between;margin:19px 0 12px}
.intro-row h3 {margin:0;font-size:21px;color:var(--ink)}
.intro-row h3:before {content:'';display:inline-block;width:5px;height:20px;border-radius:5px;margin-right:9px;vertical-align:-3px;background:linear-gradient(var(--accent),var(--accent2))}
.intro-row .small-note {font-size:12px;color:#687b98}
.research {background:#fff;border:1px solid #e1e5f3;border-left:5px solid #8c74e6;border-radius:18px;padding:15px 17px;margin:9px 0;box-shadow:0 5px 18px rgba(36,60,119,.06);transition:transform .18s,box-shadow .18s;overflow:hidden}
.research.tone0{background:linear-gradient(116deg,#f7f2ff,#fff 63%);border-left-color:#8163d9}
.research.tone1{background:linear-gradient(116deg,#edfaf7,#fff 63%);border-left-color:#10a888}
.research.tone2{background:linear-gradient(116deg,#fff6ed,#fff 63%);border-left-color:#f39a4c}
.research.tone3{background:linear-gradient(116deg,#eef6ff,#fff 63%);border-left-color:#418ee8}
.research .top {display:flex;gap:9px;align-items:center;flex-wrap:wrap}
.research .name {font-family:Outfit,sans-serif;font-size:18px;font-weight:800;color:var(--ink)}
.research .meta {color:#687b99;font-size:12px;margin-left:auto}
.tag {border-radius:999px;font-size:10px;font-weight:800;letter-spacing:.07em;padding:6px 10px}
.tag.consider {background:#d8f9ea;color:#076446}
.tag.watch {background:#fff0cd;color:#895400}
.tag.avoid {background:#ffe2e8;color:#a9294b}
.mini-grid {display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;margin-top:12px}
.mini-grid div {background:rgba(246,248,255,.86);border:1px solid #edf0f9;border-radius:11px;padding:10px}
.mini-grid b {display:block;color:#263e69;font-size:14px;margin-top:4px;overflow-wrap:anywhere}
.mini-grid small {color:#687991;font-size:10px}
.research .reason {font-size:12px;line-height:1.55;color:#546581;margin-top:12px}
.panel {background:#fff;border:1px solid #e0e4f3;border-radius:18px;padding:17px 19px;margin:11px 0;box-shadow:0 7px 21px rgba(45,52,117,.06)}
.panel h3 {margin:0 0 9px;color:var(--ink);font-size:18px}
.panel p {color:#526783;font-size:13px;line-height:1.6}
.panel.learning {background:linear-gradient(132deg,#f0ebff,#fff 62%);border-color:#ddd3ff}
.panel.guide {background:linear-gradient(132deg,#edfaf6,#fff 60%);border-color:#cdeee3}
.panel.recent {background:linear-gradient(132deg,#fff2e5,#fff 63%);border-color:#f7dcbf}
.status-strip {padding:12px 16px;background:linear-gradient(112deg,#fff2d7,#fffaf0);border:1px solid #f3dbad;border-radius:14px;color:#644b18;font-size:13px;font-weight:650;margin:10px 0 14px}
.status-strip.ok {background:linear-gradient(112deg,#e8faf0,#f8fffc);border-color:#b8ead4;color:#1c694a}
@media(max-width:900px){.kpis{grid-template-columns:repeat(2,minmax(0,1fr))}.hero-new{padding:23px}.kpi{padding:13px}}
@media(max-width:530px){.kpis{gap:9px}.mini-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.research .meta{margin-left:0}.research{padding:13px}.hero-new{padding:19px}.hero-new .hero-pills{gap:5px}}
"""


def esc(v):
    return html.escape(str(v), quote=True)


def theme_css(name: str = "Aurora") -> str:
    """Render a closed-set palette; no user-provided CSS interpolation."""
    p = PALETTES.get(name, PALETTES["Aurora"])
    vars_css = ";".join(f"--{key}:{value}" for key, value in p.items())
    return THEME_CSS + f"\n:root {{{vars_css}}}\n"


def hero(now_text: str, market_open: bool) -> str:
    market = "🟢 NSE market open" if market_open else "🌙 NSE market closed"
    return (f'<div class="hero-new"><div class="eyebrow">🇮🇳 NSE • YOUR AI RESEARCH WORKSPACE</div>'
            '<h1>Find the signal. <span class="dot">Skip the noise.</span></h1>'
            '<p>Stocks, bullish calls and bearish puts — clear ideas, clear risks, and no confusing graphs.</p>'
            '<div class="hero-pills">'
            f'<span class="pill">{esc(market)}</span><span class="pill">🕘 {esc(now_text)} IST</span>'
            '<span class="pill">🧪 Paper trading</span><span class="pill">✨ Simple view</span>'
            '</div></div>')


def kpis(stats: list[tuple[str,str,str,str,str]]) -> str:
    cards=[]
    for label, value, sub, icon, theme in stats:
        tone = theme if theme in ('teal','lav','peach','sky') else ''
        cards.append(f'<div class="kpi {tone}"><div class="ico">{esc(icon)}</div>'
                     f'<div class="klabel">{esc(label)}</div><div class="kval">{esc(value)}</div>'
                     f'<div class="ksub">{esc(sub)}</div></div>')
    return '<div class="kpis">'+''.join(cards)+'</div>'


def item_card(name: str, decision: str, details: str, values: list[tuple[str,str]], reason: str) -> str:
    kind=decision.lower() if decision in ('CONSIDER','WATCH','AVOID') else 'watch'
    tone = int(hashlib.sha256(name.encode('utf-8')).hexdigest()[:3], 16) % 4
    return (f'<div class="research tone{tone}"><div class="top">'
            f'<span class="name">{esc(name)}</span><span class="tag {kind}">{esc(decision)}</span>'
            f'<span class="meta">{esc(details)}</span></div>'
            '<div class="mini-grid">' + ''.join(f'<div><small>{esc(k)}</small><b>{esc(v)}</b></div>' for k,v in values) +
            '</div><div class="reason"><strong>Why:</strong> '+esc(reason)+'</div></div>')


def intro(title: str, note: str="") -> str:
    return f'<div class="intro-row"><h3>{esc(title)}</h3><span class="small-note">{esc(note)}</span></div>'


def status_strip(text: str, good: bool = False) -> str:
    return f'<div class="status-strip {"ok" if good else ""}">{esc(text)}</div>'
