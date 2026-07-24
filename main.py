"""Minimal FastAPI app for gamely.

One endpoint at /gamely:
  GET  -> serves the HTML page (which pulls in its CSS and JS from /gamely/static)
  POST -> calls a helper function and returns JSON

Run with:  uvicorn main:app --reload
"""

from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import services

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI()

# Serve static assets (CSS/JS) under /gamely/static so links work behind the falkly.de/gamely reverse proxy.
app.mount("/gamely/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


class SearchRequest(BaseModel):
    title: str


@app.get("/gamely")
def index():
    """Serve the HTML page."""
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.post("/gamely")
async def search(payload: SearchRequest):
    """Take a game title and return price data as JSON."""
    return await services.search_game_prices(payload.title)
