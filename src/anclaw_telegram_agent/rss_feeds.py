# Lista statica dei feed RSS disponibili per il Search Team.
# Ogni feed ha: url, name (identificativo unico senza spazi), description (usata dall'LLM per la selezione).
# Aggiungere/rimuovere feed qui; il Search Team selezionerà i più rilevanti in base alla query.

RSS_FEEDS: list[dict] = [
    # ── ADNKronos ─────────────────────────────────────────────────────────────
    {
        "url": "https://www.adnkronos.com/RSS_Ultimora.xml",
        "name": "ADNKronos_UltimaOra",
        "description": "Notizie dell'ultima ora e aggiornamenti in tempo reale (ADNKronos)",
    },
    {
        "url": "https://www.adnkronos.com/RSS_Politica.xml",
        "name": "ADNKronos_Politica",
        "description": "Aggiornamenti sulla scena politica nazionale e internazionale (ADNKronos)",
    },
    {
        "url": "https://www.adnkronos.com/RSS_Esteri.xml",
        "name": "ADNKronos_Esteri",
        "description": "Notizie dal mondo, politica estera e cronaca internazionale (ADNKronos)",
    },
    {
        "url": "https://www.adnkronos.com/RSS_Economia.xml",
        "name": "ADNKronos_Economia",
        "description": "Approfondimenti su mercati finanziari, imprese e scenari economici (ADNKronos)",
    },
    {
        "url": "https://www.adnkronos.com/RSS_Sport.xml",
        "name": "ADNKronos_Sport",
        "description": "Principali eventi sportivi, risultati e news dal mondo dello sport (ADNKronos)",
    },
    # ── ANSA ──────────────────────────────────────────────────────────────────
    {
        "url": "https://www.ansa.it/sito/notizie/topnews/topnews_rss.xml",
        "name": "ANSA_UltimeNotizie",
        "description": "Le notizie dell'ultima ora e i principali aggiornamenti in tempo reale (ANSA)",
    },
    {
        "url": "https://www.ansa.it/sito/notizie/politica/politica_rss.xml",
        "name": "ANSA_Politica",
        "description": "Notizie e aggiornamenti sulle istituzioni e la politica italiana (ANSA)",
    },
    {
        "url": "https://www.ansa.it/sito/notizie/mondo/mondo_rss.xml",
        "name": "ANSA_Esteri",
        "description": "Cronaca internazionale e notizie provenienti da tutto il mondo (ANSA)",
    },
    {
        "url": "https://www.ansa.it/sito/notizie/economia/economia_rss.xml",
        "name": "ANSA_Economia",
        "description": "News su mercati, finanza, lavoro e scenario economico (ANSA)",
    },
    {
        "url": "https://www.ansa.it/sito/notizie/sport/sport_rss.xml",
        "name": "ANSA_Sport",
        "description": "Notizie su tutte le discipline sportive e grandi eventi (ANSA)",
    },
    {
        "url": "https://www.ansa.it/sito/notizie/sport/calcio/calcio_rss.xml",
        "name": "ANSA_Calcio",
        "description": "Approfondimenti, risultati e news sul mondo del calcio (ANSA)",
    },
]
