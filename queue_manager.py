"""Fila persistente de downloads de áudio do YouTube."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from queue import Queue
from threading import Lock, Thread

from yt_dlp import YoutubeDL
from cloud_storage import CloudStorage
from youtube_config import configure as configure_youtube


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DOWNLOAD_DIR = ROOT / "downloads"
MANIFEST = DATA_DIR / "queue.json"


class DownloadQueue:
    def __init__(self) -> None:
        DATA_DIR.mkdir(exist_ok=True)
        DOWNLOAD_DIR.mkdir(exist_ok=True)
        self.lock = Lock()
        self.pending: Queue[str] = Queue()
        self.jobs: dict[str, dict] = {}
        self.cloud = CloudStorage()
        self._load()
        Thread(target=self._worker, daemon=True, name="audio-download-queue").start()

    def _load(self) -> None:
        if not MANIFEST.exists() and self.cloud.firestore:
            for job in self.cloud.load_jobs():
                if isinstance(job, dict) and job.get("id"):
                    self.jobs[job["id"]] = job
            if self.jobs:
                for video_id in self.jobs:
                    if self.jobs[video_id].get("status") not in {"ready", "error"}:
                        self.pending.put(video_id)
                return
        if not MANIFEST.exists():
            return
        try:
            stored = json.loads(MANIFEST.read_text(encoding="utf-8"))
            for job in stored:
                video_id = job.get("id", "")
                if len(video_id) != 11 or not all(char.isalnum() or char in "_-" for char in video_id):
                    continue
                filename = job.get("file")
                storage = job.get("storage") or {}
                source_ready = bool(filename and (DOWNLOAD_DIR / Path(filename).name).is_file()) or bool(storage.get("original") and self.cloud.exists(storage["original"]))
                stems = job.get("stems")
                expected = {name: f"{video_id}/{name}.flac" for name in ("instrumental", "lead_vocals", "backing_vocals")}
                stems_ready = isinstance(stems, dict) and all(
                    stems.get(name) == relative and (ROOT / "stems" / relative).is_file()
                    for name, relative in expected.items()
                ) or all(storage.get(name) and self.cloud.exists(storage[name]) for name in expected)
                instruments = job.get("instruments") or {}
                instruments_ready = all(
                    instruments.get(name) == f"{video_id}/{name}.flac" and (ROOT / "stems" / video_id / f"{name}.flac").is_file()
                    for name in ("drums", "bass", "guitar", "piano", "other")
                ) or all(storage.get(name) and self.cloud.exists(storage[name]) for name in ("drums", "bass", "guitar", "piano", "other"))
                karaoke_ready = (job.get("karaoke_audio") == f"{video_id}/karaoke.m4a" and (ROOT / "stems" / video_id / "karaoke.m4a").is_file()) or bool(storage.get("karaoke") and self.cloud.exists(storage["karaoke"]))
                if job.get("status") == "ready" and source_ready and stems_ready and instruments_ready and "key" in job and karaoke_ready and job.get("metadata_checked"):
                    self.jobs[video_id] = job
                    continue
                if job.get("status") == "error":
                    self.jobs[video_id] = job
                    continue
                job.update(status="queued", progress=100 if source_ready else 0, error=None)
                self.jobs[video_id] = job
                self.pending.put(video_id)
        except (OSError, ValueError, TypeError) as error:
            print(f"Não foi possível restaurar a fila: {error}")

    def _save(self) -> None:
        temporary = MANIFEST.with_suffix(".tmp")
        temporary.write_text(json.dumps(list(self.jobs.values()), ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, MANIFEST)
        for job in self.jobs.values():
            self.cloud.sync_job(job)

    def list_jobs(self) -> list[dict]:
        with self.lock:
            return [job.copy() for job in self.jobs.values()]

    def add(self, video_id: str, title: str, channel: str) -> tuple[dict, bool]:
        with self.lock:
            existing = self.jobs.get(video_id)
            if existing and existing["status"] != "error":
                return existing.copy(), False
            if existing:
                job = existing
                job.update(title=title, channel=channel, status="queued", error=None)
            else:
                job = {
                    "id": video_id,
                    "title": title,
                    "channel": channel,
                    "status": "queued",
                    "progress": 0,
                    "file": None,
                    "stems": None,
                    "key": None,
                    "karaoke_audio": None,
                    "lyrics": None,
                    "artwork_url": None,
                    "genre": "Outros",
                    "favorite": False,
                    "metadata_checked": False,
                    "error": None,
                }
            self.jobs[video_id] = job
            self._save()
            self.pending.put(video_id)
            return job.copy(), True

    def update(self, video_id: str, **changes: object) -> dict | None:
        with self.lock:
            job = self.jobs.get(video_id)
            if not job:
                return None
            allowed = {"genre", "favorite", "player_name"}
            for key, value in changes.items():
                if key in allowed:
                    job[key] = value
            self._save()
            return job.copy()

    def _set(self, video_id: str, **changes: object) -> None:
        with self.lock:
            self.jobs[video_id].update(changes)
            if "status" in changes or "storage" in changes:
                self._save()

    def _worker(self) -> None:
        while True:
            video_id = self.pending.get()
            try:
                with self.lock:
                    filename = self.jobs[video_id].get("file")
                source = DOWNLOAD_DIR / Path(filename).name if filename else None
                if not source or not source.is_file():
                    with self.lock:
                        remote = (self.jobs[video_id].get("storage") or {}).get("original")
                    if remote and self.cloud.download(remote, DOWNLOAD_DIR / f"{video_id}.m4a"):
                        filename = f"{video_id}.m4a"
                        source = DOWNLOAD_DIR / filename
                    else:
                        self._set(video_id, status="downloading", progress=0, error=None)
                        filename = self._download(video_id)
                        source = DOWNLOAD_DIR / filename
                        self._set(video_id, status="separating_instrumental", progress=100, file=filename)

                from audio_processing import prepare_karaoke_audio, separate_and_analyze

                with self.lock:
                    previous = self.jobs[video_id].copy()
                stems = previous.get("stems") or {}
                stems_ready = all(
                    stems.get(name) == f"{video_id}/{name}.flac" and (ROOT / "stems" / video_id / f"{name}.flac").is_file()
                    for name in ("instrumental", "lead_vocals", "backing_vocals")
                )
                instruments = previous.get("instruments") or {}
                instruments_ready = all(
                    instruments.get(name) == f"{video_id}/{name}.flac" and (ROOT / "stems" / video_id / f"{name}.flac").is_file()
                    for name in ("drums", "bass", "guitar", "piano", "other")
                )
                if not stems_ready or not instruments_ready or "key" not in previous:
                    results = separate_and_analyze(source, video_id, lambda stage: self._set(video_id, status=stage))
                    self._set(video_id, **results)

                self._set(video_id, status="preparing_karaoke")
                karaoke_audio = prepare_karaoke_audio(video_id)
                self._set(video_id, karaoke_audio=karaoke_audio)

                if not previous.get("metadata_checked"):
                    from music_metadata import fetch_metadata

                    self._set(video_id, status="fetching_metadata")
                    duration = self._duration(source)
                    metadata = fetch_metadata(previous["title"], previous["channel"], duration)
                    self._set(video_id, **metadata)
                with self.lock:
                    current = self.jobs[video_id].copy()
                self._upload_assets(video_id, source, current)
                self._set(video_id, status="ready", progress=100, error=None)
            except Exception as error:
                print(f"Processamento de {video_id} falhou: {error}")
                with self.lock:
                    filename = self.jobs[video_id].get("file")
                has_audio = bool(filename and (DOWNLOAD_DIR / Path(filename).name).is_file())
                message = "Falha no processamento. Confirme novamente para tentar de novo." if has_audio else "Falha no download. Confirme novamente para tentar de novo."
                self._set(video_id, status="error", error=message)
            finally:
                self.pending.task_done()

    def _upload_assets(self, video_id: str, source: Path, job: dict) -> None:
        if not self.cloud.enabled:
            return
        assets = {"original": source}
        for name in ("instrumental", "lead_vocals", "backing_vocals"):
            assets[name] = ROOT / "stems" / video_id / f"{name}.flac"
        for name in ("drums", "bass", "guitar", "piano", "other"):
            assets[name] = ROOT / "stems" / video_id / f"{name}.flac"
        assets["karaoke"] = ROOT / "stems" / video_id / "karaoke.m4a"
        uploaded = {}
        for name, path in assets.items():
            suffix = path.suffix.lower()
            content_type = "audio/mp4" if suffix in {".m4a", ".mp4"} else "audio/flac"
            key = f"songs/{video_id}/{path.name}"
            if self.cloud.upload(path, key, content_type):
                uploaded[name] = key
        if uploaded:
            self._set(video_id, storage=uploaded)
            if os.environ.get("CLOUD_CLEANUP_LOCAL", "").lower() in {"1", "true", "yes"}:
                for path in assets.values():
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        pass

    def ensure_karaoke_local(self, video_id: str) -> Path | None:
        path = ROOT / "stems" / video_id / "karaoke.m4a"
        if path.is_file():
            return path
        with self.lock:
            key = (self.jobs.get(video_id, {}).get("storage") or {}).get("karaoke")
        if key and self.cloud.download(key, path):
            return path
        return None

    @staticmethod
    def _duration(source: Path) -> float | None:
        try:
            process = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(source)],
                capture_output=True, text=True, timeout=20, check=False,
            )
            return float(process.stdout.strip()) if process.returncode == 0 else None
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return None

    def _download(self, video_id: str) -> str:
        def reject_live(info: dict, incomplete: bool = False) -> str | None:
            if info.get("is_live") or info.get("live_status") in {"is_live", "is_upcoming"}:
                return "Transmissões ao vivo não podem entrar na fila de músicas."
            return None

        def progress_hook(progress: dict) -> None:
            if progress.get("status") != "downloading":
                return
            total = progress.get("total_bytes") or progress.get("total_bytes_estimate")
            if not total:
                return
            percent = min(99, int(100 * progress.get("downloaded_bytes", 0) / total))
            with self.lock:
                self.jobs[video_id]["progress"] = percent

        options = {
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "noplaylist": True,
            "format": "bestaudio[ext=m4a]/bestaudio",
            "paths": {"home": str(DOWNLOAD_DIR)},
            "outtmpl": {"default": "%(id)s.%(ext)s"},
            "socket_timeout": 20,
            "retries": 3,
            "continuedl": True,
            "match_filter": reject_live,
            "progress_hooks": [progress_hook],
        }
        with YoutubeDL(configure_youtube(options)) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=True)
            filename = Path(ydl.prepare_filename(info))
        if not filename.is_file() or filename.stat().st_size == 0:
            raise RuntimeError("O download terminou sem gerar um arquivo de áudio.")
        return filename.name
