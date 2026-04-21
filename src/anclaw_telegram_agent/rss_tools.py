import feedparser


def make_rss_fetch_tool(feed_url: str, feed_name: str):
    safe_name = feed_name.lower().replace(" ", "_").replace("-", "_")

    def fetch_feed() -> str:
        try:
            feed = feedparser.parse(feed_url)
            entries = feed.entries[:5]
            if not entries:
                return f"Nessun articolo trovato nel feed {feed_name}."
            parts = []
            for e in entries:
                title = (e.get("title") or "").strip()
                url = e.get("link", "")
                summary = (e.get("summary") or "").strip()[:300]
                parts.append(f"Titolo: {title}\nURL: {url}\nSommario: {summary}")
            return f"[{feed_name}]\n\n" + "\n\n---\n\n".join(parts)
        except Exception as ex:
            return f"Errore nel leggere il feed {feed_name}: {ex}"

    fetch_feed.__name__ = f"fetch_{safe_name}"
    fetch_feed.__doc__ = f"Legge gli ultimi 5 articoli dal feed RSS '{feed_name}'."
    return fetch_feed
