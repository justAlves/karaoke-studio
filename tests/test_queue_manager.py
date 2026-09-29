import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import queue_manager
import audio_processing
import music_metadata


class DownloadQueueTest(unittest.TestCase):
    def test_download_is_persisted_and_not_queued_twice(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            data = base / "data"
            downloads = base / "downloads"
            manifest = data / "queue.json"

            def fake_download(_queue, video_id):
                filename = f"{video_id}.m4a"
                (downloads / filename).write_bytes(b"audio")
                return filename

            def fake_processing(source, video_id, on_stage):
                self.assertTrue(source.is_file())
                stem_dir = base / "stems" / video_id
                stem_dir.mkdir(parents=True)
                stems = {}
                for name in ("instrumental", "lead_vocals", "backing_vocals"):
                    (stem_dir / f"{name}.flac").write_bytes(b"audio")
                    stems[name] = f"{video_id}/{name}.flac"
                on_stage("separating_backing")
                on_stage("analyzing_key")
                return {"stems": stems, "key": {"label": "Dó maior"}}

            def fake_mix(video_id):
                (base / "stems" / video_id / "karaoke.m4a").write_bytes(b"karaoke")
                return f"{video_id}/karaoke.m4a"

            def fake_metadata(_title, _channel, _duration):
                return {"track": "Evidências", "artist": "Chitãozinho & Xororó", "album": "Ao Vivo",
                        "artwork_url": "https://example.com/cover.jpg", "lyrics": {"synced": True, "lines": [{"time": 1.2, "text": "Linha"}]},
                        "metadata_checked": True, "metadata_error": None}

            with patch.multiple(queue_manager, ROOT=base, DATA_DIR=data, DOWNLOAD_DIR=downloads, MANIFEST=manifest), patch.object(queue_manager.DownloadQueue, "_download", fake_download), patch.object(audio_processing, "separate_and_analyze", fake_processing), patch.object(audio_processing, "prepare_karaoke_audio", fake_mix), patch.object(music_metadata, "fetch_metadata", fake_metadata):
                download_queue = queue_manager.DownloadQueue()
                job, added = download_queue.add("ePjtnSPFWK8", "Evidências", "Chitãozinho & Xororó")
                self.assertTrue(added)
                self.assertEqual(job["status"], "queued")
                download_queue.pending.join()
                ready = download_queue.list_jobs()[0]
                self.assertEqual(ready["status"], "ready")
                self.assertEqual(ready["file"], "ePjtnSPFWK8.m4a")
                self.assertEqual(ready["key"]["label"], "Dó maior")
                self.assertEqual(len(ready["stems"]), 3)
                self.assertEqual(ready["karaoke_audio"], "ePjtnSPFWK8/karaoke.m4a")
                self.assertTrue(ready["lyrics"]["synced"])
                updated = download_queue.update("ePjtnSPFWK8", genre="Rock", favorite=True)
                self.assertEqual(updated["genre"], "Rock")
                self.assertTrue(updated["favorite"])
                self.assertEqual(json.loads(manifest.read_text())[0]["status"], "ready")

                restored = queue_manager.DownloadQueue()
                self.assertEqual(restored.list_jobs()[0]["status"], "ready")
                _, added_again = restored.add("ePjtnSPFWK8", "Evidências", "Chitãozinho & Xororó")
                self.assertFalse(added_again)


if __name__ == "__main__":
    unittest.main()
