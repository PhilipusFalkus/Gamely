"use strict";
// Fixed left-to-right row order, independent of the order the backend
// returns results in.
const STORE_ORDER = ["steam", "epic", "gog", "instant_gaming"];
const STORE_NAMES = {
    steam: "Steam",
    epic: "Epic Games",
    gog: "GOG",
    instant_gaming: "Instant Gaming",
};
const titleInput = document.getElementById("title");
const statusText = document.getElementById("status");
const resultsSection = document.getElementById("results");
// Some stores name the base game's edition differently (e.g. GOG calls it
// "Base Edition" instead of "Standard Edition"). Map known synonyms to one
// canonical label so cheapest-price comparison groups them together.
const EDITION_LABEL_ALIASES = {
    "base edition": "standard edition",
    "base game": "standard edition",
};
function normalizeEditionLabel(label) {
    const key = label.trim().toLowerCase();
    return EDITION_LABEL_ALIASES[key] ?? key;
}
// Pulls a comparable number out of formatted price strings like "59.99 EUR",
// "82,78€" or "€17.99", since stores each format prices differently.
function parsePrice(price) {
    if (!price)
        return null;
    if (price.trim().toLowerCase() === "free")
        return 0;
    const decimalMatch = price.match(/(\d+)[.,](\d{1,2})(?!\d)/);
    if (decimalMatch) {
        return parseFloat(`${decimalMatch[1]}.${decimalMatch[2]}`);
    }
    const wholeMatch = price.match(/\d+/);
    return wholeMatch ? parseFloat(wholeMatch[0]) : null;
}
function buildEditionRow(edition, cheapestByEdition) {
    const row = document.createElement("div");
    row.className = "edition-row";
    const name = document.createElement(edition.store_url ? "a" : "span");
    name.className = "edition-name";
    name.textContent = edition.edition;
    if (edition.store_url) {
        const link = name;
        link.href = edition.store_url;
        link.target = "_blank";
        link.rel = "noopener";
    }
    row.appendChild(name);
    if (edition.store) {
        const storeTag = document.createElement("span");
        storeTag.className = "edition-substore";
        storeTag.textContent = edition.store;
        row.appendChild(storeTag);
    }
    if (edition.current_price) {
        const price = document.createElement("span");
        price.className = "price-current";
        const parsedPrice = parsePrice(edition.current_price);
        const key = normalizeEditionLabel(edition.edition);
        if (parsedPrice !== null && cheapestByEdition.get(key) === parsedPrice) {
            price.classList.add("price-cheapest");
        }
        price.textContent = edition.current_price;
        row.appendChild(price);
    }
    if (edition.on_sale && edition.original_price) {
        const original = document.createElement("span");
        original.className = "price-original";
        original.textContent = edition.original_price;
        row.appendChild(original);
    }
    if (edition.on_sale && edition.discount_percent != null) {
        const badge = document.createElement("span");
        badge.className = "badge badge-sale";
        badge.textContent = `-${edition.discount_percent}%`;
        row.appendChild(badge);
    }
    if (edition.is_preorder) {
        const badge = document.createElement("span");
        badge.className = "badge badge-preorder";
        badge.textContent = "Preorder";
        row.appendChild(badge);
    }
    if (!edition.in_stock) {
        const badge = document.createElement("span");
        badge.className = "badge badge-soldout";
        badge.textContent = "Sold out";
        row.appendChild(badge);
    }
    return row;
}
function buildStoreRow(store, result, cheapestByEdition) {
    const row = document.createElement("div");
    row.className = "store-row";
    const logo = document.createElement("div");
    logo.className = "store-logo";
    logo.textContent = STORE_NAMES[store] ?? store;
    row.appendChild(logo);
    const editionsBox = document.createElement("div");
    editionsBox.className = "store-editions";
    const editions = result?.editions ?? [];
    if (!result || !result.is_game_existing || !result.is_available || editions.length === 0) {
        const notAvailable = document.createElement("div");
        notAvailable.className = "not-available";
        notAvailable.textContent = "Not available";
        editionsBox.appendChild(notAvailable);
    }
    else {
        for (const edition of editions) {
            editionsBox.appendChild(buildEditionRow(edition, cheapestByEdition));
        }
    }
    row.appendChild(editionsBox);
    return row;
}
// The cheapest current_price seen for each edition label, across all stores,
// so that price can be highlighted wherever it's shown.
function findCheapestByEdition(results) {
    const cheapest = new Map();
    for (const result of results) {
        for (const edition of result.editions) {
            const price = parsePrice(edition.current_price);
            if (price === null)
                continue;
            const key = normalizeEditionLabel(edition.edition);
            const current = cheapest.get(key);
            if (current === undefined || price < current) {
                cheapest.set(key, price);
            }
        }
    }
    return cheapest;
}
function renderResults(results) {
    resultsSection.innerHTML = "";
    const byStore = new Map(results.map((result) => [result.store, result]));
    const cheapestByEdition = findCheapestByEdition(results);
    for (const store of STORE_ORDER) {
        resultsSection.appendChild(buildStoreRow(store, byStore.get(store), cheapestByEdition));
    }
    resultsSection.classList.remove("hidden");
}
async function runSearch() {
    const title = titleInput.value.trim();
    if (!title) {
        statusText.textContent = "Enter a game title first.";
        return;
    }
    statusText.textContent = "Searching…";
    resultsSection.classList.add("hidden");
    try {
        const response = await fetch("/gamely", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ title }),
        });
        const data = await response.json();
        statusText.textContent = "";
        renderResults(data);
    }
    catch (err) {
        statusText.textContent = "Request failed.";
    }
}
document.getElementById("search").addEventListener("click", runSearch);
titleInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
        runSearch();
    }
});
