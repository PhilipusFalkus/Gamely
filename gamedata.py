"""Helper functions for looking up game prices by title, per edition.

Uses Steam's, Epic Games Store's, GOG's, and Instant Gaming's public store
APIs (no API key required). Each get_*_price function returns a StoreResult
model holding one Edition entry per edition (Standard, Deluxe, Ultimate, ...)
available on that store.

G2A and MMOGA are not covered here: G2A actively blocks scripted requests
and its real API requires an approved partner/OAuth credential, while MMOGA
has no public API and fronts scraping attempts with a reCAPTCHA wall -
neither gives a reliable anonymous title lookup.

"""

import asyncio
import re
import httpx
import truststore
from pydantic import BaseModel

truststore.inject_into_ssl()  # verify TLS certs via the OS trust store

STEAM_API = "https://store.steampowered.com/api"
STEAM_HTML_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    # bypasses the age-check wall that blocks mature titles' app pages
    "Cookie": "birthtime=568022401; lastagecheckage=1-January-1988; wants_mature_content=1",
}

EPIC_GRAPHQL_URL = "https://store.epicgames.com/graphql"
EPIC_HEADERS = {
    # A full browser-like header set, not just User-Agent, to look less like a
    # bot to Epic's Cloudflare bot protection in front of this endpoint.
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://store.epicgames.com",
    "Referer": "https://store.epicgames.com/",
}
EPIC_SEARCH_QUERY = """
query searchStoreQuery($keywords: String, $country: String!, $locale: String, $count: Int) {
  Catalog {
    searchStore(keywords: $keywords, country: $country, locale: $locale, count: $count) {
      elements {
        title
        namespace
        offerType
        prePurchase
        price(country: $country) {
          totalPrice {
            discountPrice
            originalPrice
            currencyCode
            fmtPrice(locale: $locale) { originalPrice discountPrice }
          }
        }
        catalogNs { mappings(pageType: "productHome") { pageSlug } }
      }
    }
  }
}
"""

GOG_CATALOG_URL = "https://catalog.gog.com/v1/catalog"
GOG_API_URL = "https://api.gog.com"

IG_ALGOLIA_URL = "https://qknhp8tc3y-dsn.algolia.net/1/indexes/produits_{lang}_spotlighted_desc/query"
IG_HEADERS = {
    "X-Algolia-Application-Id": "QKNHP8TC3Y",
    "X-Algolia-API-Key": "93946b91c013211f842ddf1819ea880b",  # public read-only key, shipped to every visitor's browser on instant-gaming.com -- safe to expose
    "Referer": "https://www.instant-gaming.com/",
}
IG_PC_PLATFORM_ID = "1"  # the platform id Instant Gaming uses for PC keys


# Pydantic models give FastAPI a fixed response shape, so the frontend always
# knows exactly which fields/types to expect instead of guessing at loose
# dicts, and FastAPI validates + serializes them to JSON automatically.
class Edition(BaseModel):
    """One purchasable edition (Standard, Deluxe, Ultimate, ...) of a game."""

    edition: str
    title: str
    store_url: str | None
    is_preorder: bool
    current_price: str | None
    on_sale: bool
    discount_percent: int | float | None = None
    original_price: str | None = None
    in_stock: bool = True  # False = sold out (only Instant Gaming reports this)
    store: str | None = None  # Instant Gaming only: which storefront the key activates on


class StoreResult(BaseModel):
    """All editions of a game found on one store."""

    store: str
    query: str
    is_game_existing: bool  # False if the store has no matching title at all
    is_available: bool  # True if at least one edition could be priced
    editions: list[Edition] = []


def _edition_label(full_name: str, base_title: str) -> str:
    """Derive a short edition label ('Ultimate Edition') from a full SKU name."""
    full_name = re.sub(r"[®™]", "", full_name).strip()
    base_title = re.sub(r"[®™]", "", base_title).strip()
    if full_name.lower().startswith(base_title.lower()):
        suffix = full_name[len(base_title):].strip(" :-")
    else:
        suffix = full_name
    return suffix or "Standard Edition"


def _normalize_title(title: str) -> str:
    """Lowercase and strip trademark symbols/edge whitespace for exact-title comparisons."""
    return re.sub(r"[®™]", "", title).strip().lower()


def _title_matches(candidate: str, query: str) -> bool:
    """Check that every word in `query` also appears (whole-word) in `candidate`.

    Some fuzzy search APIs (like GOG's) fall back to unrelated "closest"
    results instead of an empty list when there's no real match for the
    query -- this filters those out.
    """
    normalize = lambda s: set(re.sub(r"[^\w]+", " ", s.lower()).split())
    query_words = normalize(query)
    return bool(query_words) and query_words.issubset(normalize(candidate))


def _fmt_cents(cents: int, currency: str) -> str:
    return f"{cents / 100:.2f} {currency}"


async def _steam_bundle_editions(
    client: httpx.AsyncClient, appid: int, base_title: str, is_preorder: bool, cc: str, lang: str,
) -> list[Edition]:
    """Find same-game "edition" bundles (e.g. base game + DLC sold together).

    Some Steam editions (like Cyberpunk 2077: Ultimate Edition) aren't a
    purchase option on the base app itself -- they're a separate bundle that
    combines the base game with its DLC. Bundles aren't exposed by
    appdetails/packagedetails, so this scrapes the bundle widgets off the
    app page's HTML and skips any bundle that mixes in an unrelated game.
    """
    resp = await client.get(
        f"https://store.steampowered.com/app/{appid}/", params={"cc": cc, "l": lang},
        headers=STEAM_HTML_HEADERS, timeout=10,
    )
    html = resp.text

    editions = []
    for label_match in re.finditer(r'aria-labelledby="bundle_purchase_label_(\d+)"', html):
        bundle_id = label_match.group(1)
        window = html[label_match.start(): label_match.start() + 4000]

        title_match = re.search(rf'id="bundle_purchase_label_{bundle_id}"[^>]*>\s*Buy ([^<]+?)\s*(?:<span|</h2>)', window)
        contents_match = re.search(r'package_contents_collapsed">(.*?)</p>', window, re.S)
        price_match = re.search(r'aria-label="(\d+)% off\. ([^"]+?) normally, discounted to ([^"]+?)"', window)
        if not (title_match and contents_match and price_match):
            continue

        item_names = [n for n in re.findall(r'>([^<]+)</a>', contents_match.group(1)) if n not in ("Show less", "Show more")]
        if not item_names or not all(n.lower().startswith(base_title.lower()) for n in item_names):
            continue  # cross-promotional bundle with an unrelated game, not an edition

        discount_percent, original_price, current_price = price_match.groups()
        discount_percent = int(discount_percent)
        on_sale = discount_percent > 0

        editions.append(Edition(
            edition=_edition_label(title_match.group(1).strip(), base_title),
            title=base_title,
            store_url=f"https://store.steampowered.com/app/{appid}/",
            is_preorder=is_preorder,
            current_price=current_price,
            on_sale=on_sale,
            discount_percent=discount_percent if on_sale else None,
            original_price=original_price if on_sale else None,
        ))

    return editions


async def get_steam_price(title: str, cc: str = "de", lang: str = "en") -> StoreResult:
    """Look up `title` on Steam and return price/sale/preorder info per edition."""
    async with httpx.AsyncClient() as client:
        search = (await client.get(
            f"{STEAM_API}/storesearch/", params={"term": title, "cc": cc, "l": lang}, timeout=10,
        )).json()
        # Steam's search falls back to "closest" results (spin-offs, sequels,
        # ...) instead of an empty list when the exact title isn't sold on
        # Steam at all -- e.g. "minecraft" returns "Minecraft Dungeons" as its
        # top hit. Only accept an exact (case/trademark-insensitive) title
        # match so an unrelated game is never silently shown as the query.
        match = next(
            (item for item in search.get("items", [])
             if item.get("type") == "app" and _normalize_title(item["name"]) == _normalize_title(title)),
            None,
        )
        if match is None:
            return StoreResult(store="steam", query=title, is_game_existing=False, is_available=False)

        appid = match["id"]
        details = (await client.get(
            f"{STEAM_API}/appdetails", params={"appids": appid, "cc": cc, "l": lang}, timeout=10,
        )).json()
        data = details.get(str(appid), {}).get("data")
        if not data:
            return StoreResult(store="steam", query=title, is_game_existing=True, is_available=False)

        base_title = data.get("name", title)
        store_url = f"https://store.steampowered.com/app/{appid}/"

        # Only the "default" group holds the game's actual editions (Standard,
        # Deluxe, ...) -- other groups are DLC, season passes, or in-game
        # currency bundles (e.g. "5000 BFC for Battlefield 6 and REDSEC").
        package_ids = list(dict.fromkeys(
            sub["packageid"]
            for group in data.get("package_groups", [])
            if group.get("name") == "default"
            for sub in group.get("subs", [])
        ))
        if not package_ids:
            price = data.get("price_overview")
            on_sale = bool(price and price["discount_percent"] > 0)
            edition = Edition(
                edition="Standard Edition",
                title=base_title,
                store_url=store_url,
                is_preorder=bool(data.get("release_date", {}).get("coming_soon")),
                current_price=price["final_formatted"] if price else ("Free" if data.get("is_free") else None),
                on_sale=on_sale,
                discount_percent=price["discount_percent"] if on_sale else None,
                original_price=price["initial_formatted"] if on_sale else None,
            )
            return StoreResult(store="steam", query=title, is_game_existing=True, is_available=True, editions=[edition])

        editions = []
        for package_id in package_ids:
            pkg = (await client.get(
                f"{STEAM_API}/packagedetails", params={"packageids": package_id, "cc": cc, "l": lang}, timeout=10,
            )).json()
            pkg_data = pkg.get(str(package_id), {}).get("data")
            if not pkg_data:
                continue

            price = pkg_data.get("price")
            on_sale = bool(price and price["discount_percent"] > 0)
            editions.append(Edition(
                edition=_edition_label(pkg_data.get("name", base_title), base_title),
                title=base_title,
                store_url=store_url,
                is_preorder=bool(pkg_data.get("release_date", {}).get("coming_soon")),
                current_price=_fmt_cents(price["final"], price["currency"]) if price else None,
                on_sale=on_sale,
                discount_percent=price["discount_percent"] if on_sale else None,
                original_price=_fmt_cents(price["initial"], price["currency"]) if on_sale else None,
            ))

        is_preorder = bool(data.get("release_date", {}).get("coming_soon"))
        known_labels = {e.edition for e in editions}
        for bundle_edition in await _steam_bundle_editions(client, appid, base_title, is_preorder, cc, lang):
            if bundle_edition.edition not in known_labels:
                editions.append(bundle_edition)
                known_labels.add(bundle_edition.edition)

        return StoreResult(store="steam", query=title, is_game_existing=True, is_available=bool(editions), editions=editions)


async def get_epic_price(title: str, country: str = "DE", locale: str = "en-US") -> StoreResult:
    """Look up `title` on the Epic Games Store and return price/sale/preorder info per edition."""
    payload = {
        "query": EPIC_SEARCH_QUERY,
        "variables": {"keywords": title, "country": country, "locale": locale, "count": 20},
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(EPIC_GRAPHQL_URL, headers=EPIC_HEADERS, json=payload, timeout=10)
    if resp.status_code != 200:
        # Epic's Cloudflare bot protection occasionally blocks this endpoint
        # with a challenge page instead of JSON -- treat that the same as
        # "not found" instead of crashing the whole concurrent lookup.
        return StoreResult(store="epic", query=title, is_game_existing=False, is_available=False)
    elements = resp.json()["data"]["Catalog"]["searchStore"]["elements"]
    if not elements:
        return StoreResult(store="epic", query=title, is_game_existing=False, is_available=False)

    base = next((e for e in elements if e["offerType"] == "BASE_GAME"), elements[0])
    base_title = base["title"]
    editions_data = [
        e for e in elements
        if e["namespace"] == base["namespace"] and e["offerType"] in ("BASE_GAME", "EDITION")
    ]

    editions = []
    for game in editions_data:
        mappings = game["catalogNs"]["mappings"]
        slug = mappings[0]["pageSlug"] if mappings else None
        price = game["price"]["totalPrice"]
        discount = price["originalPrice"] - price["discountPrice"]
        on_sale = discount > 0

        editions.append(Edition(
            edition="Standard Edition" if game["offerType"] == "BASE_GAME" else _edition_label(game["title"], base_title),
            title=game["title"],
            store_url=f"https://store.epicgames.com/{locale}/p/{slug}" if slug else None,
            is_preorder=bool(game["prePurchase"]),
            current_price=price["fmtPrice"]["discountPrice"] if price["originalPrice"] else "Free",
            on_sale=on_sale,
            discount_percent=round(discount / price["originalPrice"] * 100) if on_sale else None,
            original_price=price["fmtPrice"]["originalPrice"] if on_sale else None,
        ))

    return StoreResult(store="epic", query=title, is_game_existing=True, is_available=bool(editions), editions=editions)


def _gog_final_price(product: dict) -> float:
    try:
        return float(product["price"]["finalMoney"]["amount"])
    except (KeyError, TypeError, ValueError):
        return 0.0


async def _gog_edition_price(
    client: httpx.AsyncClient, edition: dict, base_title: str, country: str, currency: str,
) -> Edition | None:
    """Fetch price/detail info for one GOG edition, or None if it's not sold in this country/currency."""
    edition_id = edition["id"]
    product_resp, prices_resp = await asyncio.gather(
        client.get(f"{GOG_API_URL}/products/{edition_id}", timeout=10),
        client.get(
            f"{GOG_API_URL}/products/{edition_id}/prices",
            params={"countryCode": country, "currency": currency}, timeout=10,
        ),
    )
    product = product_resp.json()
    price_entries = prices_resp.json().get("_embedded", {}).get("prices", [])
    if not price_entries:
        return None  # not sold in this country/currency

    price = price_entries[0]
    base_cents = int(price["basePrice"].split()[0])
    final_cents = int(price["finalPrice"].split()[0])
    price_currency = price["currency"]["code"]
    edition_title = product.get("title", base_title)
    on_sale = final_cents < base_cents

    return Edition(
        edition=edition.get("name") or _edition_label(edition_title, base_title),
        title=edition_title,
        store_url=product.get("links", {}).get("product_card"),
        is_preorder=bool(product.get("is_pre_order")),
        current_price=_fmt_cents(final_cents, price_currency),
        on_sale=on_sale,
        discount_percent=round((base_cents - final_cents) / base_cents * 100) if on_sale else None,
        original_price=_fmt_cents(base_cents, price_currency) if on_sale else None,
    )


async def get_gog_price(title: str, country: str = "DE", currency: str = "EUR") -> StoreResult:
    """Look up `title` on GOG.com and return price/sale/preorder info per edition."""
    async with httpx.AsyncClient() as client:
        search = (await client.get(
            GOG_CATALOG_URL, params={"limit": 10, "order": "desc:score", "query": f"like:{title}"}, timeout=10,
        )).json()
        products = search.get("products") or []
        matches = [p for p in products if _title_matches(p["title"], title)]
        # Prefer a paid match over a free one (e.g. a "REDkit" modding toolkit
        # ranking above the actual paid game it's named after) -- GOG's own
        # relevance ranking doesn't otherwise distinguish these.
        base = next((p for p in matches if _gog_final_price(p) > 0), None) or (matches[0] if matches else None)
        if base is None:
            return StoreResult(store="gog", query=title, is_game_existing=False, is_available=False)

        base_title = base["title"]
        editions = base.get("editions") or [{"id": base["id"], "name": "Standard Edition"}]

        # Editions are independent lookups (each its own product+prices call
        # pair), so fetch them all concurrently instead of one after another.
        edition_results = await asyncio.gather(
            *(_gog_edition_price(client, edition, base_title, country, currency) for edition in editions)
        )
        results = [e for e in edition_results if e is not None]

        return StoreResult(store="gog", query=title, is_game_existing=True, is_available=bool(results), editions=results)


async def get_instant_gaming_price(title: str, country: str = "DE", lang: str = "en") -> StoreResult:
    """Look up `title` on Instant Gaming and return each PC-store edition's key info.

    Instant Gaming sells the same game as keys for several storefronts (Steam,
    GOG, Epic, Uplay, ...) and for non-PC platforms (PlayStation, Xbox,
    Switch). This only considers full-game keys whose `platforms` list
    includes the PC platform id, and reports which storefront each key
    activates on.
    """
    payload = {
        "query": title,
        "hitsPerPage": 40,
        "filters": (
            f'(country_whitelist:"{country}" OR country_whitelist:"worldwide" OR country_whitelist:"WW") '
            f'AND (NOT country_blacklist:"{country}")'
        ),
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(IG_ALGOLIA_URL.format(lang=lang), headers=IG_HEADERS, json=payload, timeout=10)
    hits = resp.json().get("hits", [])
    if not hits:
        return StoreResult(store="instant_gaming", query=title, is_game_existing=False, is_available=False)

    pc_hits = [h for h in hits if IG_PC_PLATFORM_ID in h["platforms"]]
    if not pc_hits:
        return StoreResult(store="instant_gaming", query=title, is_game_existing=True, is_available=False)

    non_dlc_pc_hits = [h for h in pc_hits if not h.get("is_dlc")] or pc_hits
    anchor = non_dlc_pc_hits[0]
    related_ids = {anchor["prod_id"]} | {int(x) for x in anchor["similar_prod_id"].split(",") if x}
    editions_hits = [h for h in non_dlc_pc_hits if h["prod_id"] in related_ids]
    base_name = min((h["name"] for h in editions_hits), key=len)

    # Instant Gaming's search index sometimes lists the same edition for the
    # same store twice under different product ids (e.g. a duplicate/legacy
    # listing) -- keep only the cheapest hit per (edition label, store) pair.
    cheapest_hit_by_edition_store = {}
    for hit in editions_hits:
        key = (_edition_label(hit["name"], base_name), hit["type"])
        cheapest = cheapest_hit_by_edition_store.get(key)
        if cheapest is None or (hit.get("price") or float("inf")) < (cheapest.get("price") or float("inf")):
            cheapest_hit_by_edition_store[key] = hit
    editions_hits = list(cheapest_hit_by_edition_store.values())

    editions = []
    for game in editions_hits:
        store_slug = game["type"].lower().replace(".", "-").replace(" ", "-")
        action = "download" if game.get("is_free_to_play") else "buy"
        on_sale = bool(game["discount"])

        editions.append(Edition(
            edition=_edition_label(game["name"], base_name),
            title=game["name"],
            store=game["type"],
            store_url=f"https://www.instant-gaming.com/{lang}/{game['prod_id']}-{action}-{store_slug}-{game['seo_name']}/",
            in_stock=bool(game["has_stock"]),
            is_preorder=bool(game["preorder"]),
            current_price=game["price_formatted"].replace("&nbsp;", " ") if game.get("price") is not None else None,
            on_sale=on_sale,
            discount_percent=game["discount"] if on_sale else None,
            original_price=f"{game['retail']} {game['retail_currency']}" if on_sale else None,
        ))

    return StoreResult(
        store="instant_gaming", query=title, is_game_existing=True, is_available=bool(editions), editions=editions,
    )
