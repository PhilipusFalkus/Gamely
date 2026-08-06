"""Videogame title suggestions for the search bar's autocomplete dropdown.

Reuses Steam's public storesearch endpoint (no API key required) since it
already does fuzzy prefix/substring matching on game titles.
"""

import httpx
import truststore

truststore.inject_into_ssl()  # verify TLS certs via the OS trust store

STEAM_SEARCH_URL = "https://store.steampowered.com/api/storesearch/"


async def get_title_suggestions(text: str, cc: str = "de", lang: str = "en", limit: int = 8) -> list[str]:
    """Return up to `limit` Steam game titles matching the partially-typed `text`.

    Only "app" hits are kept (not "sub"/bundle hits) so suggestions are base
    game titles, not edition/bundle names.
    """
    if not text.strip():
        return []

    async with httpx.AsyncClient() as client:
        search = (await client.get(
            STEAM_SEARCH_URL, params={"term": text, "cc": cc, "l": lang}, timeout=10,
        )).json()

    titles = []
    for item in search.get("items", []):
        if item.get("type") != "app":
            continue
        name = item["name"]
        if name not in titles:
            titles.append(name)
        if len(titles) >= limit:
            break
    return titles
