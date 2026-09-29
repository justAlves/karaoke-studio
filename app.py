"""Servidor local para pesquisar músicas no YouTube."""

from __future__ import annotations

import json
import hashlib
import hmac
import os
import re
import shutil
import socket
import subprocess
from io import BytesIO
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from queue_manager import DownloadQueue


ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be", "www.youtu.be"}
DOWNLOAD_QUEUE = DownloadQueue()


def session_url() -> str:
    public_url = os.environ.get("KARAOKE_PUBLIC_URL", "").strip().rstrip("/")
    if public_url:
        return public_url + "/"
    host = os.environ.get("KARAOKE_HOST", "").strip()
    if not host:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("8.8.8.8", 80))
            host = probe.getsockname()[0]
        except OSError:
            host = "127.0.0.1"
        finally:
            probe.close()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"http://{host}:8000/"


def video_url_from_input(value: str) -> str | None:
    """Retorna uma URL canônica para links de vídeo; rejeita outras URLs."""
    if value.lower().startswith(("youtube.com/", "www.youtube.com/", "m.youtube.com/", "music.youtube.com/", "youtu.be/", "www.youtu.be/")):
        value = "https://" + value
    if not value.lower().startswith(("http://", "https://")):
        return None

    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in YOUTUBE_HOSTS:
        raise ValueError("Use um link do YouTube ou digite o título da música.")

    parts = parsed.path.strip("/").split("/")
    if parsed.hostname in {"youtu.be", "www.youtu.be"}:
        video_id = parts[0]
    elif parts[0] == "watch":
        video_id = parse_qs(parsed.query).get("v", [""])[0]
    elif parts[0] in {"shorts", "live", "embed"} and len(parts) > 1:
        video_id = parts[1]
    else:
        video_id = ""

    if not VIDEO_ID.fullmatch(video_id):
        raise ValueError("Não encontrei um vídeo válido nesse link do YouTube.")
    return f"https://www.youtube.com/watch?v={video_id}"


def format_video(entry: dict) -> dict | None:
    video_id = entry.get("id")
    if not isinstance(video_id, str) or not VIDEO_ID.fullmatch(video_id):
        return None

    thumbnails = entry.get("thumbnails") or []
    thumbnail = next((item.get("url") for item in reversed(thumbnails) if item.get("url")), None)
    return {
        "id": video_id,
        "title": entry.get("title") or "Sem título",
        "channel": entry.get("channel") or entry.get("uploader") or "Canal desconhecido",
        "duration": entry.get("duration") if isinstance(entry.get("duration"), (int, float)) else None,
        "thumbnail": thumbnail or f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
        "url": f"https://www.youtube.com/watch?v={video_id}",
    }


def search_youtube(query: str) -> list[dict]:
    from yt_dlp import YoutubeDL

    direct_url = video_url_from_input(query)
    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "extract_flat": not bool(direct_url),
        "socket_timeout": 12,
        "retries": 1,
    }
    with YoutubeDL(options) as ydl:
        info = ydl.extract_info(direct_url or f"ytsearch5:{query}", download=False)

    entries = [info] if direct_url else (info or {}).get("entries") or []
    return [video for entry in entries[:5] if entry and (video := format_video(entry))]


@lru_cache(maxsize=20)
def preview_audio(video_id: str) -> bytes:
    """Extrai até 25 segundos de áudio do centro do vídeo."""
    from yt_dlp import YoutubeDL

    if not VIDEO_ID.fullmatch(video_id):
        raise ValueError("Vídeo inválido.")
    if not shutil.which("ffmpeg"):
        raise RuntimeError("FFmpeg não está instalado.")

    with YoutubeDL({
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "format": "bestaudio[ext=m4a]/bestaudio/best",
        "socket_timeout": 12,
        "retries": 1,
    }) as ydl:
        info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)

    duration = info.get("duration")
    if isinstance(duration, (int, float)) and duration > 0:
        clip_length = min(25, int(duration))
        start = max(0, int((duration - clip_length) / 2))
    else:
        clip_length, start = 25, 0

    command = ["ffmpeg", "-hide_banner", "-loglevel", "error"]
    user_agent = (info.get("http_headers") or {}).get("User-Agent")
    if user_agent:
        command.extend(["-user_agent", user_agent])
    command.extend([
        "-ss", str(start), "-i", info["url"], "-t", str(clip_length),
        "-vn", "-ac", "2", "-c:a", "libmp3lame", "-b:a", "96k",
        "-f", "mp3", "pipe:1",
    ])
    process = subprocess.run(command, capture_output=True, timeout=90, check=False)
    if process.returncode or not process.stdout:
        raise RuntimeError(process.stderr.decode("utf-8", errors="replace")[-500:])
    return process.stdout


class Handler(BaseHTTPRequestHandler):
    def _auth_enabled(self) -> bool:
        return bool(os.environ.get("KARAOKE_PASSWORD", "").strip())

    def _auth_token(self) -> str:
        password = os.environ.get("KARAOKE_PASSWORD", "")
        secret = os.environ.get("KARAOKE_AUTH_SECRET", password)
        return hmac.new(secret.encode(), b"karaoke-session", hashlib.sha256).hexdigest()

    def _authorized(self) -> bool:
        if not self._auth_enabled():
            return True
        cookies = self.headers.get("Cookie", "")
        expected = self._auth_token()
        return any(part.strip() == f"karaoke_auth={expected}" for part in cookies.split(";"))

    def _login_page(self) -> None:
        body = """<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Karaoke Studio — acesso</title><style>
        :root{color-scheme:dark}*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0c0d0f;color:#f4efe7;font:16px system-ui,sans-serif}main{width:min(390px,calc(100% - 32px));padding:32px;border:1px solid #34332f;border-radius:18px;background:#151614;box-shadow:0 24px 80px #0008}h1{margin:0 0 8px;font-size:28px}p{margin:0 0 24px;color:#aaa79f;line-height:1.5}label{display:block;margin-bottom:8px;color:#cbc5b9;font-size:13px}input{width:100%;padding:13px 14px;border:1px solid #494740;border-radius:10px;background:#0e0f0e;color:#fff;font:inherit}button{width:100%;margin-top:14px;padding:13px;border:0;border-radius:10px;background:#e7a84f;color:#17130d;font-weight:700;font:inherit;cursor:pointer}#error{min-height:22px;margin-top:12px;color:#ed8b79;font-size:14px}</style></head><body><main><h1>♫ Karaoke Studio</h1><p>Informe a senha da sessão para entrar.</p><form id="login"><label for="password">Senha da sessão</label><input id="password" type="password" autocomplete="current-password" required autofocus><button>Entrar</button><div id="error" role="alert"></div></form></main><script>document.getElementById('login').addEventListener('submit',async e=>{e.preventDefault();const error=document.getElementById('error');error.textContent='';try{const r=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:document.getElementById('password').value})});if(!r.ok)throw new Error('Senha incorreta.');location.href='/'}catch(err){error.textContent=err.message}})</script></body></html>"""
        self.send_bytes(body.encode("utf-8"), 200, "text/html; charset=utf-8")

    def _require_auth(self, route: str) -> bool:
        if route in {"/api/health", "/api/login", "/login"} or self._authorized():
            return True
        if route == "/":
            self._login_page()
        else:
            self.send_json({"error": "Autenticação necessária."}, 401)
        return False

    def send_bytes(self, body: bytes, status: int, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, payload: dict, status: int = 200) -> None:
        self.send_bytes(json.dumps(payload, ensure_ascii=False).encode("utf-8"), status, "application/json; charset=utf-8")

    def send_audio(self, path: Path) -> None:
        size = path.stat().st_size
        start, end = 0, size - 1
        range_header = self.headers.get("Range")
        if range_header:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header)
            if not match or (not match.group(1) and not match.group(2)):
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            if match.group(1):
                start = int(match.group(1))
                end = min(int(match.group(2)), size - 1) if match.group(2) else size - 1
            else:
                start = max(0, size - int(match.group(2)))
            if start >= size or end < start:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
        self.send_response(206 if range_header else 200)
        self.send_header("Content-Type", "audio/mp4")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        if range_header:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with path.open("rb") as source:
            source.seek(start)
            remaining = end - start + 1
            while remaining:
                chunk = source.read(min(64 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def do_GET(self) -> None:
        files = {
            "/": (STATIC / "index.html", "text/html; charset=utf-8"),
            "/styles.css": (STATIC / "styles.css", "text/css; charset=utf-8"),
            "/app.js": (STATIC / "app.js", "text/javascript; charset=utf-8"),
        }
        parsed = urlparse(self.path)
        route = parsed.path
        if not self._require_auth(route):
            return
        if route == "/login":
            if self._authorized():
                self.send_response(302)
                self.send_header("Location", "/")
                self.end_headers()
            else:
                self._login_page()
            return
        if route == "/api/health":
            self.send_json({"status": "ok"})
            return
        if route == "/api/queue":
            self.send_json({"jobs": DOWNLOAD_QUEUE.list_jobs()})
            return
        if route == "/api/session":
            self.send_json({"url": session_url()})
            return
        if route == "/api/session/qr":
            try:
                import qrcode
                from qrcode.image.svg import SvgPathImage

                image = qrcode.make(session_url(), image_factory=SvgPathImage)
                body = BytesIO()
                image.save(body)
                self.send_bytes(body.getvalue(), 200, "image/svg+xml")
            except ImportError:
                self.send_json({"error": "Dependência de QR Code ausente."}, 503)
            return
        if route == "/api/karaoke":
            parameters = parse_qs(parsed.query)
            video_id = parameters.get("id", [""])[0]
            if not VIDEO_ID.fullmatch(video_id):
                self.send_json({"error": "Música inválida."}, 400)
                return
            job = next((item for item in DOWNLOAD_QUEUE.list_jobs() if item["id"] == video_id), None)
            path = DOWNLOAD_QUEUE.ensure_karaoke_local(video_id)
            if not job or job["status"] != "ready" or job.get("karaoke_audio") != f"{video_id}/karaoke.m4a" or not path or not path.is_file():
                self.send_json({"error": "Karaokê ainda não disponível."}, 404)
                return
            try:
                self.send_audio(path)
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        if route == "/api/preview":
            video_id = parse_qs(parsed.query).get("id", [""])[0]
            if not VIDEO_ID.fullmatch(video_id):
                self.send_json({"error": "Vídeo inválido."}, 400)
                return
            try:
                self.send_bytes(preview_audio(video_id), 200, "audio/mpeg")
            except ImportError:
                self.send_json({"error": "Dependência ausente: instale yt-dlp conforme o README."}, 503)
            except Exception as error:
                print(f"Falha na prévia: {error}")
                self.send_json({"error": "Não foi possível carregar a prévia de áudio."}, 502)
            return
        if route not in files:
            self.send_json({"error": "Página não encontrada."}, 404)
            return
        path, content_type = files[route]
        self.send_bytes(path.read_bytes(), 200, content_type)

    def do_POST(self) -> None:
        if self.path == "/api/login":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 1 or length > 4096:
                    raise ValueError("Dados inválidos.")
                payload = json.loads(self.rfile.read(length))
                password = payload.get("password", "") if isinstance(payload, dict) else ""
                expected = os.environ.get("KARAOKE_PASSWORD", "")
                if not expected or not isinstance(password, str) or not hmac.compare_digest(password, expected):
                    self.send_json({"error": "Senha incorreta."}, 401)
                    return
                self.send_response(204)
                secure = "; Secure" if os.environ.get("KARAOKE_COOKIE_SECURE", "").lower() in {"1", "true", "yes"} else ""
                self.send_header("Set-Cookie", f"karaoke_auth={self._auth_token()}; Path=/; Max-Age=2592000; HttpOnly; SameSite=Lax{secure}")
                self.end_headers()
            except (ValueError, json.JSONDecodeError):
                self.send_json({"error": "Dados inválidos."}, 400)
            return
        if not self._require_auth(urlparse(self.path).path):
            return
        if self.path not in {"/api/search", "/api/queue"}:
            self.send_json({"error": "Página não encontrada."}, 404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > 4096:
                raise ValueError("Dados inválidos.")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("Dados inválidos.")
            if self.path == "/api/queue":
                action = payload.get("action")
                if action == "update":
                    video_id = payload.get("id", "")
                    if not isinstance(video_id, str) or not VIDEO_ID.fullmatch(video_id):
                        raise ValueError("Música inválida.")
                    changes = {}
                    if isinstance(payload.get("genre"), str) and len(payload["genre"]) <= 60:
                        changes["genre"] = payload["genre"].strip() or "Outros"
                    if isinstance(payload.get("favorite"), bool):
                        changes["favorite"] = payload["favorite"]
                    if isinstance(payload.get("player_name"), str) and len(payload["player_name"]) <= 60:
                        changes["player_name"] = payload["player_name"].strip()
                    job = DOWNLOAD_QUEUE.update(video_id, **changes)
                    if not job:
                        raise ValueError("Música não encontrada na fila.")
                    self.send_json({"job": job})
                    return
                video_id = payload.get("id", "")
                title = payload.get("title", "")
                channel = payload.get("channel", "")
                if not isinstance(video_id, str) or not VIDEO_ID.fullmatch(video_id):
                    raise ValueError("Vídeo inválido.")
                if not isinstance(title, str) or not title.strip() or len(title) > 300:
                    raise ValueError("Título inválido.")
                if not isinstance(channel, str) or len(channel) > 200:
                    raise ValueError("Canal inválido.")
                job, added = DOWNLOAD_QUEUE.add(video_id, title.strip(), channel.strip())
                self.send_json({"job": job, "added": added}, 202 if added else 200)
                return
            query = payload.get("query", "")
            if not isinstance(query, str) or not query.strip():
                raise ValueError("Informe um título ou URL do YouTube.")
            query = query.strip()
            if len(query) > 300:
                raise ValueError("A busca deve ter até 300 caracteres.")
            results = search_youtube(query)
            self.send_json({"results": results, "source": "url" if video_url_from_input(query) else "search"})
        except (ValueError, json.JSONDecodeError) as error:
            self.send_json({"error": str(error)}, 400)
        except ImportError:
            self.send_json({"error": "Dependência ausente: instale yt-dlp conforme o README."}, 503)
        except Exception as error:
            print(f"Falha na operação: {error}")
            self.send_json({"error": "Não foi possível concluir a operação. Tente novamente."}, 502)


def main() -> None:
    port = int(os.environ.get("KARAOKE_PORT", "8000"))
    address = (os.environ.get("KARAOKE_BIND", "0.0.0.0"), port)
    server = ThreadingHTTPServer(address, Handler)
    print(f"Karaoke Studio em {session_url()}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServidor encerrado.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
