"""Configuração compartilhada do yt-dlp para servidores/VPS."""
from __future__ import annotations

import base64
import os
from pathlib import Path

COOKIE_FILE = Path("/tmp/karaoke-youtube-cookies.txt")


def configure(options: dict) -> dict:
    """Aplica clientes alternativos e cookies opcionais sem expor segredos no código."""
    options.setdefault("extractor_args", {}).setdefault("youtube", {})["player_client"] = ["android", "web_safari"]
    encoded = os.environ.get("YOUTUBE_COOKIES_B64", "").strip()
    if encoded:
        try:
            COOKIE_FILE.write_bytes(base64.b64decode(encoded, validate=True))
            options["cookiefile"] = str(COOKIE_FILE)
        except (ValueError, OSError) as error:
            print(f"Cookies do YouTube inválidos: {error}")
    return options
