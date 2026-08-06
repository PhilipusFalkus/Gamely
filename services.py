import asyncio
from typing import Awaitable
import gamedata
import suggestions


async def _safe_lookup(store: str, title: str, lookup: Awaitable[gamedata.StoreResult]) -> gamedata.StoreResult:
    """Run one store's lookup, turning a failure (timeout, bad response, ...) into
    a not-available result for just that store instead of failing the whole search.
    """
    try:
        return await lookup
    except Exception:
        return gamedata.StoreResult(store=store, query=title, is_game_existing=False, is_available=False)


async def search_game_prices(title: str) -> list[gamedata.StoreResult]:
    """Returns the requested game information from various game stores.

    The four stores are looked up concurrently instead of one after another,
    since each one is a slow, independent network round-trip.
    """
    return await asyncio.gather(
        _safe_lookup("steam", title, gamedata.get_steam_price(title)),
        _safe_lookup("epic", title, gamedata.get_epic_price(title)),
        _safe_lookup("gog", title, gamedata.get_gog_price(title)),
        _safe_lookup("instant_gaming", title, gamedata.get_instant_gaming_price(title)),
    )


async def search_name_suggestions(text: str) -> list[str]:
    """Returns videogame name suggestions for the entered text."""
    return await suggestions.get_title_suggestions(text)