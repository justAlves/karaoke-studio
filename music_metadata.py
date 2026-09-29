"""Busca capa no iTunes e letra sincronizada na LRCLIB."""

from __future__ import annotations

import json
import re
import time
import unicodedata
from difflib import SequenceMatcher
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


USER_AGENT = "KaraokeStudio/0.2 (http://127.0.0.1:8000/)"
LRC_LINE = re.compile(r"\[(\d{1,2}):(\d{2})(?:[.:](\d{1,3}))?\]")
TITLE_TAGS = re.compile(r"\s*[\[(][^\])]*(?:official|video|audio|letra|lyrics|clipe|visualizer|hd|4k)[^\])]*[\])]", re.I)


def normalized(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(char for char in value if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def clean_identity(title: str, channel: str) -> tuple[str, str]:
    artist = re.sub(r"\s*(?:- Topic|VEVO)$", "", channel, flags=re.I).strip()
    track = TITLE_TAGS.sub("", title).strip()
    if " - " in track:
        first, rest = track.split(" - ", 1)
        if artist and (normalized(first) == normalized(artist) or normalized(first) in normalized(artist) or similarity(first, artist) > 0.7):
            track = rest
    track = TITLE_TAGS.sub("", track).strip()
    return track, artist


def request_json(url: str) -> object:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    for attempt in range(3):
        try:
            with urlopen(request, timeout=12) as response:
                return json.load(response)
        except HTTPError as error:
            if error.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise
            try:
                retry_after = float(error.headers.get("Retry-After", "1"))
            except ValueError:
                retry_after = 1
            delay = min(5, max(0.2, retry_after)) if error.code == 429 else 0.6 * (attempt + 1)
            time.sleep(delay)
        except (OSError, URLError):
            if attempt == 2:
                raise
            time.sleep(0.6 * (attempt + 1))
    raise RuntimeError("A consulta não retornou uma resposta.")


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, normalized(a), normalized(b)).ratio()


def title_score(wanted: str, candidate: str) -> float:
    first = similarity(wanted, candidate)
    # As versões ao vivo e remasterizadas podem ter sufixos no catálogo.
    if normalized(wanted) in normalized(candidate):
        first = max(first, 0.82)
    return first


def find_itunes(track: str, artist: str, duration: float | None) -> dict | None:
    query = urlencode({"term": f"{artist} {track}", "media": "music", "entity": "song", "country": "BR", "limit": 20})
    response = request_json(f"https://itunes.apple.com/search?{query}")
    candidates = response.get("results", []) if isinstance(response, dict) else []
    scored = []
    for item in candidates:
        if not isinstance(item, dict) or not item.get("artworkUrl100"):
            continue
        title_match = title_score(track, item.get("trackName", ""))
        artist_match = similarity(artist, item.get("artistName", ""))
        if title_match < 0.65 or artist_match < 0.65:
            continue
        difference = abs(duration - item.get("trackTimeMillis", 0) / 1000) if duration else 0
        version_mismatch = ("ao vivo" in normalized(track)) != ("ao vivo" in normalized(item.get("trackName", "")))
        score = title_match * 0.5 + artist_match * 0.35 + max(0, 1 - difference / 40) * 0.15 - (0.12 if version_mismatch else 0)
        scored.append((score, item))
    if not scored:
        return None
    item = max(scored, key=lambda pair: pair[0])[1]
    artwork = item["artworkUrl100"].replace("/100x100bb.", "/600x600bb.")
    return {"track": item.get("trackName") or track, "artist": item.get("artistName") or artist,
            "album": item.get("collectionName"), "artwork_url": artwork,
            "genre": item.get("primaryGenreName") or "Outros"}


def parse_lrc(value: str) -> list[dict]:
    lines = []
    for line in value.splitlines():
        stamps = list(LRC_LINE.finditer(line))
        if not stamps:
            continue
        text = LRC_LINE.sub("", line).strip()
        for stamp in stamps:
            fraction = stamp.group(3) or "0"
            seconds = int(stamp.group(1)) * 60 + int(stamp.group(2)) + int(fraction) / 10 ** len(fraction)
            lines.append({"time": seconds, "text": text})
    return sorted(lines, key=lambda item: item["time"])


def find_lyrics(track: str, artist: str, duration: float | None, album: str | None = None) -> dict | None:
    query = urlencode({"track_name": track, "artist_name": artist})
    response = request_json(f"https://lrclib.net/api/search?{query}")
    if not isinstance(response, list):
        raise RuntimeError("Resposta inválida da LRCLIB.")
    scored = []
    for item in response:
        if not isinstance(item, dict) or item.get("instrumental"):
            continue
        title_match = title_score(track, item.get("trackName", ""))
        artist_match = similarity(artist, item.get("artistName", ""))
        if title_match < 0.7 or artist_match < 0.65:
            continue
        difference = abs(duration - (item.get("duration") or 0)) if duration else 0
        if duration and difference > 18:
            continue
        synced = parse_lrc(item.get("syncedLyrics") or "")
        plain = item.get("plainLyrics") or ""
        if not synced and not plain:
            continue
        score = title_match * 0.5 + artist_match * 0.3 + max(0, 1 - difference / 20) * 0.2 + (0.25 if synced else 0)
        scored.append((score, item, synced, plain))
    if not scored:
        return None
    _, item, synced, plain = max(scored, key=lambda result: result[0])
    other_version = bool(album and item.get("albumName") and similarity(album, item["albumName"]) < 0.7)
    return {"synced": bool(synced), "lines": synced if synced else [{"time": None, "text": line.strip()} for line in plain.splitlines() if line.strip()],
            "track": item.get("trackName"), "artist": item.get("artistName"), "album": item.get("albumName"),
            "source": "LRCLIB", "approximate": other_version}


def fetch_metadata(title: str, channel: str, duration: float | None) -> dict:
    track, artist = clean_identity(title, channel)
    result = {"track": track, "artist": artist, "album": None, "artwork_url": None, "genre": "Outros", "lyrics": None}
    errors = []
    try:
        catalog = find_itunes(track, artist, duration)
        if catalog:
            result.update(catalog)
    except Exception as error:
        errors.append(f"iTunes: {error}")
    try:
        result["lyrics"] = find_lyrics(result["track"], result["artist"], duration, result["album"])
    except Exception as error:
        errors.append(f"LRCLIB: {error}")
    result["metadata_checked"] = not errors
    result["metadata_error"] = "; ".join(errors) if errors else None
    return result
