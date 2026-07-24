# Gamely

Enter a game title and see its current price (per edition) on Steam, Epic Games Store, GOG, and Instant Gaming, side by side.

This is a small module meant to be embedded on [falkly.de](https://falkly.de) (hence the "Back" link and contact info in the frontend) — it also works fine standalone.

## How it works

- `main.py` — FastAPI app, one route (`/gamely`) that serves the page and handles search requests.
- `services.py` — thin orchestration layer, queries all four stores concurrently.
- `gamedata.py` — the actual store lookups (Steam, Epic, GOG, Instant Gaming public APIs/endpoints).
- `src/main.ts` — frontend logic, compiled to `static/main.js`.

## Running it

Install dependencies:

```
pip install -r requirements.txt
```

Compile the frontend TypeScript once, or watch for changes while developing:

```
npm install
npm run build   # or: npm run watch
```

Start the server:

```
uvicorn main:app --reload
```

Then open `http://127.0.0.1:8000/gamely`.
