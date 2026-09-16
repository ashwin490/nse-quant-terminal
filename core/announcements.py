import feedparser
import re
from datetime import datetime

# High-impact negative and material regulatory event triggers
RISK_KEYWORDS = [
    r"\bsebi\b", r"\bpenalty\b", r"\bnotice\b", r"\binvestigation\b",
    r"\bfraud\b", r"\braid\b", r"\bresignation\b", r"\bdefault\b",
    r"\binsolvency\b", r"\bdemerger\b", r"\bdelisting\b", r"\bimpairment\b",
    r"\bshow cause\b", r"\barrest\b", r"\blitigation\b", r"\bdispute\b"
]

# Positive/neutral event tags to distinguish benign announcements
BENIGN_KEYWORDS = [
    r"\bdividend\b", r"\bbuyback\b", r"\bpatent\b", r"\border win\b",
    r"\bbonus\b", r"\bexpansion\b", r"\bcredit rating upgrade\b"
]

def check_corporate_announcements(symbol: str) -> dict:
    """
    Fetches real-time corporate announcements and regulatory filings.
    Evaluates headline sentiment for risk disqualifications.
    """
    clean_sym = symbol.replace(".NS", "").upper()
    
    # Live Indian financial news & regulatory RSS endpoints
    feed_url = f"https://news.google.com/rss/search?q={clean_sym}+NSE+OR+BSE+corporate+announcement+when:7d&hl=en-IN&gl=IN&ceid=IN:en"
    
    try:
        feed = feedparser.parse(feed_url)
        recent_entries = feed.entries[:8]
        
        detected_risks = []
        positive_events = []
        latest_headline = "No recent material disclosures."

        if recent_entries:
            latest_headline = recent_entries[0].title

        for entry in recent_entries:
            title = entry.title.lower()
            
            # Match negative regulatory / distress keywords
            for kw in RISK_KEYWORDS:
                if re.search(kw, title):
                    detected_risks.append(entry.title)
                    break
                    
            # Match value-accretive corporate catalysts
            for kw in BENIGN_KEYWORDS:
                if re.search(kw, title):
                    positive_events.append(entry.title)
                    break

        if detected_risks:
            return {
                "status": "DISQUALIFIED",
                "risk_penalty": 0.5,  # Slashes score by 50%
                "headline": detected_risks[0],
                "badge_color": "red"
            }
        elif positive_events:
            return {
                "status": "CATALYST",
                "risk_penalty": 1.05,  # 5% boost for clean corporate momentum
                "headline": positive_events[0],
                "badge_color": "green"
            }
        else:
            return {
                "status": "NEUTRAL",
                "risk_penalty": 1.0,
                "headline": latest_headline,
                "badge_color": "gray"
            }

    except Exception:
        return {
            "status": "NEUTRAL",
            "risk_penalty": 1.0,
            "headline": "Exchange feed unavailable.",
            "badge_color": "gray"
        }

if __name__ == "__main__":
    print("Testing Corporate Announcement Scanner on RELIANCE...")
    res = check_corporate_announcements("RELIANCE")
    print(f"Status: {res['status']} | Penalty: {res['risk_penalty']}x")
    print(f"Latest Headline: {res['headline']}")