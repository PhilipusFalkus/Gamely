import asyncio
import gamedata

"""Business logic for the gamely app.

The endpoint stays thin and calls the helper(s) here. Plain Python -- no FastAPI
imports -- so these are easy to test and reuse.
"""

async def search_game_prices(title: str) -> list[gamedata.StoreResult]:
    """Returns the requested game information from various game stores.

    The four stores are looked up concurrently instead of one after another,
    since each one is a slow, independent network round-trip.
    """
    return await asyncio.gather(
        gamedata.get_steam_price(title),
        gamedata.get_epic_price(title),
        gamedata.get_gog_price(title),
        gamedata.get_instant_gaming_price(title),
    )


def search_name_suggestions(text: str):
    """Returns videogame name suggestions for the entered text."""