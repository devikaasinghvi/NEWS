"""
Morning News Briefing
Collects stories from free RSS news feeds, asks Google Gemini (free tier) to write
a detailed briefing with jargon explained, and emails it to you through Gmail.
"""

import os
import re
import time
import html
import smtplib
import datetime
from zoneinfo import ZoneInfo
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import feedparser
import requests

# ---------- Settings (filled in from GitHub Secrets) ----------
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
GMAIL_ADDRESS = os.environ["GMAIL_ADDRESS"]
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"].replace(" ", "")
TO_EMAIL = os.environ.get("TO_EMAIL") or GMAIL_ADDRESS

# If Google renames this model in the future, change it here.
GEMINI_MODEL = os.environ.get("GEMINI_MODEL") or "gemini-2.5-flash"
STORIES_PER_SECTION = int(os.environ.get("STORIES_PER_SECTION") or 3)
TIMEZONE = os.environ.get("TIMEZONE") or "UTC"
CANDIDATES_PER_SECTION = 12

# ---------- News sources (free RSS feeds) ----------
# If a feed stops working, the script skips it and uses the others.
SECTIONS = {
    "Geopolitics and World Affairs": [
        "https://feeds.bbci.co.uk/news/world/rss.xml",
        "https://www.theguardian.com/world/rss",
        "https://www.aljazeera.com/xml/rss/all.xml",
    ],
    "Finance and Economy": [
        "https://feeds.bbci.co.uk/news/business/rss.xml",
        "https://www.theguardian.com/business/rss",
        "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    ],
    "Science and Technology": [
        "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml",
        "https://www.theguardian.com/science/rss",
        "https://www.sciencedaily.com/rss/all.xml",
    ],
    "Culture and Society": [
        "https://www.theguardian.com/culture/rss",
        "https://feeds.bbci.co.uk/news/entertainment_and_arts/rss.xml",
        "https://feeds.npr.org/1008/rss.xml",
    ],
    # Not news: thoughtful essays and long reads on ideas, art, history, and culture.
    "Ideas, Art and Long Reads": [
        "https://aeon.co/feed.rss",
        "https://psyche.co/feed.rss",
        "https://nautil.us/feed/",
        "https://publicdomainreview.org/rss.xml",
        "https://hyperallergic.com/feed/",
        "https://www.openculture.com/feed",
        "https://www.smithsonianmag.com/rss/latest_articles/",
    ],
}

LONG_READS_SECTION = "Ideas, Art and Long Reads"
LONG_READS_PICKS = int(os.environ.get("LONG_READS_PICKS") or 2)


def clean(text):
    """Remove HTML tags and extra spaces from feed text."""
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def fetch_section(feed_urls):
    """Read each feed and mix the stories together so no single outlet dominates."""
    per_feed = []
    for url in feed_urls:
        try:
            feed = feedparser.parse(url, request_headers={"User-Agent": "Mozilla/5.0 (morning-briefing)"})
            source = clean(feed.feed.get("title", "")) or url
            stories = []
            for entry in feed.entries[:8]:
                title = clean(entry.get("title", ""))
                if title:
                    stories.append({
                        "title": title,
                        "summary": clean(entry.get("summary", ""))[:700],
                        "link": entry.get("link", ""),
                        "source": source,
                    })
            print(f"  {len(stories)} stories from {source}")
            per_feed.append(stories)
        except Exception as error:
            print(f"  Could not read {url}: {error}")

    mixed, seen = [], set()
    for i in range(8):
        for stories in per_feed:
            if i < len(stories) and stories[i]["title"].lower() not in seen:
                seen.add(stories[i]["title"].lower())
                mixed.append(stories[i])
    return mixed[:CANDIDATES_PER_SECTION]


def build_prompt(section_name, stories):
    story_text = ""
    for n, s in enumerate(stories, 1):
        story_text += (
            f"{n}. Headline: {s['title']}\n"
            f"   Summary: {s['summary']}\n"
            f"   Source: {s['source']}\n"
            f"   Link: {s['link']}\n\n"
        )

    if section_name == LONG_READS_SECTION:
        return f"""You are writing the "Ideas, Art and Long Reads" section of a morning briefing for a curious reader who wants to become more thoughtful and well read.

Below are recent essays and articles from magazines about ideas, art, history, philosophy, psychology, and culture. Choose the {LONG_READS_PICKS} that are most intellectually stimulating, favouring variety (for example, one on art or history and one on philosophy or science of the mind).

For each article, output exactly this HTML structure:
<h3>The article's title or a clear title you write</h3>
<p>A paragraph of 120 to 180 words introducing the big idea or argument, the context behind it, and why it is worth thinking about.</p>
<p><strong>Key ideas and terms:</strong> Plain-English explanations of any thinkers, art movements, theories, or terms a newcomer might not know.</p>
<p><strong>Something to think about:</strong> One open question the reader can reflect on during the day.</p>
<p><a href="THE ARTICLE LINK">Read the full piece at THE SOURCE NAME</a></p>

Rules:
- Describe the article's argument ONLY from the summary provided. Do not invent claims, quotes, or details the summary does not contain. You may use general knowledge for background and definitions.
- Never attribute a quote to a real person.
- Write in full paragraphs. No bullet points and no lists.
- Never use em dashes. Use commas, periods, or parentheses instead.
- Output only the HTML. No markdown, no code fences, no introduction or sign-off.

Articles:
{story_text}"""

    return f"""You are writing one section of a morning news briefing for a curious reader who is new to following the news.

Section: {section_name}

Below are today's candidate stories. Choose the {STORIES_PER_SECTION} most important ones and write about each.

For each story, output exactly this HTML structure:
<h3>A clear headline you write</h3>
<p>A detailed paragraph of 120 to 180 words explaining what happened, the background a newcomer needs to understand it, and why it matters.</p>
<p><strong>Jargon explained:</strong> Plain-English explanations of any technical terms, acronyms, organisations, or concepts a beginner might not know. If there are none, explain one useful background idea instead.</p>
<p><a href="THE STORY LINK">Read more at THE SOURCE NAME</a></p>

Rules:
- Facts about the event itself must come ONLY from the summaries provided. Do not invent numbers, quotes, names, dates, or details. You may use general knowledge for background and definitions, but never for claims about what happened today.
- If a summary is too thin to say much, write a shorter paragraph rather than guessing.
- Write in full paragraphs. No bullet points and no lists.
- Never use em dashes. Use commas, periods, or parentheses instead.
- Output only the HTML. No markdown, no code fences, no introduction or sign-off.

Stories:
{story_text}"""


def ask_gemini(prompt):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
    body = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.4}}
    headers = {"x-goog-api-key": GEMINI_API_KEY, "Content-Type": "application/json"}

    for attempt in range(3):
        try:
            response = requests.post(url, headers=headers, json=body, timeout=180)
            if response.status_code == 200:
                parts = response.json()["candidates"][0]["content"]["parts"]
                return "".join(p.get("text", "") for p in parts)
            print(f"  Gemini error {response.status_code}: {response.text[:300]}")
        except Exception as error:
            print(f"  Gemini request failed: {error}")
        time.sleep(30 * (attempt + 1))
    return None


def tidy(text):
    text = re.sub(r"```(?:html)?", "", text).strip()
    return text.replace(" \u2014 ", ", ").replace("\u2014", ", ")


def fallback_html(stories):
    """Used only if Gemini fails: a plain list of headlines with links."""
    out = "<p><em>The AI summary was unavailable today, so here are the top headlines.</em></p>"
    for s in stories[:STORIES_PER_SECTION + 2]:
        out += f'<p><strong>{html.escape(s["title"])}</strong><br>{html.escape(s["summary"])}<br><a href="{s["link"]}">Read more at {html.escape(s["source"])}</a></p>'
    return out


def send_email(subject, html_body):
    message = MIMEMultipart("alternative")
    message["Subject"] = subject
    message["From"] = GMAIL_ADDRESS
    message["To"] = TO_EMAIL
    plain = clean(re.sub(r"</(p|h2|h3)>", "\n\n", html_body))
    message.attach(MIMEText(plain, "plain"))
    message.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        server.send_message(message)


def main():
    today = datetime.datetime.now(ZoneInfo(TIMEZONE)).strftime("%A, %d %B %Y")
    body = ""

    for section_name, feeds in SECTIONS.items():
        print(f"Working on: {section_name}")
        stories = fetch_section(feeds)
        if not stories:
            body += f"<h2>{section_name}</h2><p>No stories could be loaded for this section today.</p>"
            continue
        written = ask_gemini(build_prompt(section_name, stories))
        section_html = tidy(written) if written else fallback_html(stories)
        body += f'<h2 style="border-bottom:2px solid #ddd;padding-bottom:6px;margin-top:36px;">{section_name}</h2>{section_html}'
        time.sleep(5)  # small pause to stay well within free-tier limits

    email_html = f"""<html><body style="font-family:Georgia,serif;max-width:680px;margin:auto;line-height:1.6;color:#222;">
<h1 style="margin-bottom:0;">Your Morning Briefing</h1>
<p style="color:#666;margin-top:4px;">{today}</p>
{body}
<p style="color:#888;font-size:13px;margin-top:40px;">Written by AI from news feed summaries. AI can make mistakes, so check the original article before relying on anything important.</p>
</body></html>"""

    send_email(f"Your Morning Briefing: {today}", email_html)
    print("Briefing sent.")


if __name__ == "__main__":
    main()
