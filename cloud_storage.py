"""Backends opcionais para Cloudflare R2 e Firestore.

Sem as variáveis de ambiente correspondentes, a aplicação continua local.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path


class CloudStorage:
    def __init__(self) -> None:
        self.r2 = None
        self.bucket = os.environ.get("R2_BUCKET_NAME", "").strip()
        endpoint = os.environ.get("R2_ENDPOINT_URL", "").strip()
        access = os.environ.get("R2_ACCESS_KEY_ID", "").strip()
        secret = os.environ.get("R2_SECRET_ACCESS_KEY", "").strip()
        if endpoint and access and secret and self.bucket:
            try:
                import boto3
                self.r2 = boto3.client("s3", endpoint_url=endpoint, aws_access_key_id=access, aws_secret_access_key=secret, region_name="auto")
            except ImportError:
                print("R2 configurado, mas boto3 não está instalado.")
        self.firestore = None
        encoded = os.environ.get("FIREBASE_SERVICE_ACCOUNT_B64", "").strip()
        if encoded:
            try:
                import firebase_admin
                from firebase_admin import credentials, firestore
                if not firebase_admin._apps:
                    raw = base64.b64decode(encoded).decode("utf-8")
                    firebase_admin.initialize_app(credentials.Certificate(json.loads(raw)))
                self.firestore = firestore.client()
            except Exception as error:
                print(f"Firestore indisponível: {error}")

    @property
    def enabled(self) -> bool:
        return self.r2 is not None

    def upload(self, path: Path, key: str, content_type: str = "application/octet-stream") -> bool:
        if not self.r2 or not path.is_file():
            return False
        try:
            self.r2.upload_file(str(path), self.bucket, key, ExtraArgs={"ContentType": content_type})
            return True
        except Exception as error:
            print(f"Falha no upload para R2 ({key}): {error}")
            return False

    def download(self, key: str, path: Path) -> bool:
        if not self.r2:
            return False

    def exists(self, key: str) -> bool:
        if not self.r2:
            return False
        try:
            self.r2.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception:
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.r2.download_file(self.bucket, key, str(path))
            return path.is_file()
        except Exception as error:
            print(f"Falha no download do R2 ({key}): {error}")
            return False

    def sync_job(self, job: dict) -> None:
        if not self.firestore:
            return
        try:
            self.firestore.collection("karaoke_jobs").document(job["id"]).set(job)
        except Exception as error:
            print(f"Falha ao sincronizar Firestore: {error}")

    def load_jobs(self) -> list[dict]:
        if not self.firestore:
            return []
        try:
            return [item.to_dict() for item in self.firestore.collection("karaoke_jobs").stream()]
        except Exception as error:
            print(f"Falha ao carregar Firestore: {error}")
            return []
