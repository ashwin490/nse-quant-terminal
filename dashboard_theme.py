"""Bright, accessible, graph-free dashboard presentation helpers."""
from __future__ import annotations
import html

THEME_CSS = r"""
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700;800&family=Outfit:wght@500;600;700;800&display=swap');
:root { --ink:#15233e; --sub:#60708c; --edge:#e1e7f2; --blue:#365af1; --mint:#1ebd91; --rose:#e15d78; }
html, body, .stApp { font-family:'DM Sans',sans-serif; }
.stApp { background:linear-gradient(155deg,#fbfcff 0%,#f2f6ff 45%,#f9fcff 100%); color:#1b2c48; }
.block-container { max-width:1320px; padding-top:1.5rem; padding-bottom:4rem; }
#MainMenu, footer { visibility:hidden; }
h1,h2,h3 { font-family:'Outfit','DM Sans',sans-serif; letter-spacing:-.02em; }
[data-testid='stSidebar'] { background:#fff; border-right:1px solid #e2e8f4; }
[data-testid='stSidebar'] * { color:#1b2c48; }
[data-testid='stVerticalBlockBorderWrapper'] > div { border-color:#e0e8f5; }
.stTabs [data-baseweb='tab-list'] { gap:6px; background:#fff; padding:6px; border-radius:16px; box-shadow:0 7px 28px rgba(33,60,120,.06); }
.stTabs [data-baseweb='tab'] { border-radius:11px; font-weight:700; padding:9px 14px; color:#60708c; }
.stTabs [aria-selected='true'] { background:#edf3ff !important; color:#3154e8 !important; }
.stButton > button[kind='primary'], .stDownloadButton > button[kind='primary'] { background:linear-gradient(110deg,#365af1,#6964f8); color:white; border:none; border-radius:12px; box-shadow:0 5px 18px rgba(65,88,241,.23); font-weight:750; }
.stButton > button:not([kind='primary']), .stDownloadButton > button { border:1px solid #d8e3f5; background:#fff; color:#28456c; border-radius:12px; }
.stButton > button:hover { border-color:#8499fc; }
[data-testid='stMetric'] { padding:4px 2px; }
[data-testid='stMetricLabel'] { color:#66748f; }
[data-testid='stMetricValue'] { color:#1d3561; }
[data-testid='stAlert'] { border-radius:13px; }
[data-testid='stExpander'] { border:1px solid #e2e9f5; border-radius:14px; background:rgba(255,255,255,.8); }
.hero-new { border:1px solid #dce5ff; padding:27px 30px 25px; border-radius:26px; margin:1px 0 19px; background:radial-gradient(circle at 88% 28%,rgba(131,125,255,.22),transparent 30%),linear-gradient(113deg,#eef3ff 2%,#fff 57%,#e9fff6 100%); box-shadow:0 15px 46px rgba(30,58,117,.07); }
.hero-new .eyebrow { font-size:12px; font-weight:800; text-transform:uppercase; letter-spacing:.13em; color:#4265e6; }
.hero-new h1 { font-size:clamp(27px,3.5vw,43px); color:#172e58; margin:8px 0 6px; line-height:1.15; }
.hero-new p { color:#61718e; font-size:14px; margin:8px 0 0; }
.hero-new .hero-pills { display:flex; flex-wrap:wrap; gap:8px; margin-top:16px; }
.hero-new .pill { background:rgba(255,255,255,.91); border:1px solid #dce7f4; padding:7px 11px; border-radius:100px; color:#385476; font-weight:700; font-size:12px; }
.kpis { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:14px; margin:0 0 19px; }
.kpi { background:#fff; padding:17px 18px 15px; border:1px solid #e0e8f6; border-radius:19px; box-shadow:0 8px 28px rgba(42,61,112,.06); transition:transform .18s, box-shadow .18s; }
.kpi:hover, .research:hover { transform:translateY(-2px); box-shadow:0 12px 30px rgba(42,61,112,.10); }
.kpi .ico { font-size:19px; margin-bottom:8px; }
.kpi .klabel { font-size:12px; font-weight:750; color:#74809b; }
.kpi .kval { font-size:clamp(19px,2vw,29px); line-height:1.18; font-family:Outfit,sans-serif; font-weight:750; color:#243864; margin-top:5px; }
.kpi .ksub { font-size:11px; margin-top:7px; color:#8291a8; }
.kpi.teal { background:linear-gradient(135deg,#f1fffa,#fff); border-color:#c8eddf; }
.kpi.lav { background:linear-gradient(135deg,#f4f3ff,#fff); border-color:#dedcff; }
.intro-row { display:flex; gap:10px; align-items:center; justify-content:space-between; margin:18px 0 12px; }
.intro-row h3 { margin:0; font-size:22px; color:#203558; }
.intro-row .small-note { font-size:12px; color:#7b89a2; }
.research { background:#fff; border:1px solid #e1e8f6; border-radius:19px; padding:18px 20px; margin:9px 0; box-shadow:0 6px 21px rgba(28,53,108,.05); transition:transform .18s,box-shadow .18s; }
.research .top { display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
.research .name { font-family:Outfit,sans-serif; font-size:19px; font-weight:800; color:#23385f; }
.research .meta { color:#8996af; font-size:12px; margin-left:auto; }
.tag { border-radius:999px; font-size:10px; font-weight:850; letter-spacing:.06em; padding:6px 10px; }
.tag.consider { background:#dffaf0; color:#047e56; }
.tag.watch { background:#fff2d9; color:#a16102; }
.tag.avoid { background:#ffe7ed; color:#b83358; }
.mini-grid { display:grid; grid-template-columns:repeat(3,1fr); gap:10px; margin-top:14px; }
.mini-grid div { background:#f6f8fe; border-radius:11px; padding:11px 12px; }
.mini-grid b { display:block; color:#263f6a; font-size:15px; margin-top:4px; }
.mini-grid small { color:#7584a0; font-size:10px; }
.research .reason { font-size:13px; line-height:1.5; color:#556887; margin-top:13px; }
.panel { background:#fff; border:1px solid #e0e8f5; border-radius:19px; padding:19px 22px; margin:12px 0; box-shadow:0 7px 23px rgba(45,60,117,.05); }
.panel h3 { margin:0 0 10px; color:#273d65; }
.panel p { color:#687994; font-size:13px; }
.subtle { color:#70819d; font-size:12px; }
@media(max-width:850px) {.kpis{grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.hero-new{padding:19px 20px}.mini-grid{grid-template-columns:repeat(3,1fr)}.kpi{padding:13px}.research{padding:14px}.research .meta{margin-left:0}}
@media(max-width:520px){.kpis{grid-template-columns:repeat(2,minmax(0,1fr))}.mini-grid{grid-template-columns:repeat(2,1fr)}.research .name{font-size:17px}}
"""


def esc(v):
    return html.escape(str(v), quote=True)


def hero(now_text: str, market_open: bool) -> str:
    market = "🟢 NSE market open" if market_open else "🌙 NSE market closed"
    return (f'<div class="hero-new"><div class="eyebrow">🇮🇳 NSE • Your AI research workspace</div>'
            f'<h1>Market research, made simple<span style="color:#596df5">.</span></h1>'
            '<p>Clear stock ideas, bullish calls and bearish puts — explained in everyday language.</p>'
            '<div class="hero-pills">'
            f'<span class="pill">{esc(market)}</span><span class="pill">🕘 {esc(now_text)} IST</span>'
            '<span class="pill">🧪 Research and paper trades only</span><span class="pill">📊 No charts</span>'
            '</div></div>')


def kpis(stats: list[tuple[str,str,str,str,str]]) -> str:
    cards=[]
    for label, value, sub, icon, theme in stats:
        color = theme if theme in ('teal','lav') else ''
        cards.append(f'<div class="kpi {color}"><div class="ico">{esc(icon)}</div>'
                     f'<div class="klabel">{esc(label)}</div><div class="kval">{esc(value)}</div>'
                     f'<div class="ksub">{esc(sub)}</div></div>')
    return '<div class="kpis">'+''.join(cards)+'</div>'


def item_card(name: str, decision: str, details: str, values: list[tuple[str,str]], reason: str) -> str:
    kind=decision.lower() if decision in ('CONSIDER','WATCH','AVOID') else 'watch'
    return ('<div class="research"><div class="top">'
            f'<span class="name">{esc(name)}</span><span class="tag {kind}">{esc(decision)}</span>'
            f'<span class="meta">{esc(details)}</span></div>'
            '<div class="mini-grid">' + ''.join(f'<div><small>{esc(k)}</small><b>{esc(v)}</b></div>' for k,v in values) +
            '</div><div class="reason"><strong>Why:</strong> '+esc(reason)+'</div></div>')


def intro(title: str, note: str="") -> str:
    return f'<div class="intro-row"><h3>{esc(title)}</h3><span class="small-note">{esc(note)}</span></div>'
