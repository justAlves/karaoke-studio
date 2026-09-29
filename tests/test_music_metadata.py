import unittest
from unittest.mock import patch

import music_metadata


class MusicMetadataTest(unittest.TestCase):
    def test_title_cleanup_and_lrc_timing(self):
        self.assertEqual(music_metadata.clean_identity("Zimbra - Missão Apollo (Official Video)", "Zimbra"), ("Missão Apollo", "Zimbra"))
        self.assertEqual(music_metadata.parse_lrc("[00:01.40] Primeira\n[00:03.375] Segunda"),
                         [{"time": 1.4, "text": "Primeira"}, {"time": 3.375, "text": "Segunda"}])

    def test_matches_synced_lyrics_and_cover_by_artist_title_duration(self):
        def fake_request(url):
            if "itunes.apple.com" in url:
                return {"results": [
                    {"trackName": "Outra", "artistName": "Zimbra", "trackTimeMillis": 183000, "artworkUrl100": "https://example.com/wrong/100x100bb.jpg"},
                    {"trackName": "Missão Apollo", "artistName": "Zimbra", "collectionName": "O Tudo, o Nada e o Mundo", "trackTimeMillis": 183561, "artworkUrl100": "https://example.com/right/100x100bb.jpg"},
                ]}
            return [
                {"trackName": "Missão Apollo", "artistName": "Outro Artista", "duration": 184, "syncedLyrics": "[00:01.00] Errada", "plainLyrics": None},
                {"trackName": "Missão Apollo", "artistName": "Zimbra", "duration": 184, "syncedLyrics": "[00:01.40] Certa", "plainLyrics": None},
            ]

        with patch.object(music_metadata, "request_json", fake_request):
            result = music_metadata.fetch_metadata("Missão Apollo", "Zimbra", 183.56)
        self.assertEqual(result["album"], "O Tudo, o Nada e o Mundo")
        self.assertIn("/right/600x600bb.jpg", result["artwork_url"])
        self.assertEqual(result["lyrics"]["lines"][0]["text"], "Certa")
        self.assertTrue(result["metadata_checked"])


if __name__ == "__main__":
    unittest.main()
