"""Synthetic Commons responses and owned staging only, never a real account."""
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit
from inline_media import (InlineMediaService, InlineMediaError, MAX_FILE_BYTES,
                          MAX_CACHE_BYTES, MAX_PREVIEW_BYTES, RESULT_TTL_SECONDS,
                          safe_url, canonical_media_url, valid_content, plain, _Redirects)
from urllib.request import Request

PNG = b"\x89PNG\r\n\x1a\n" + b"synthetic"
WEBM = b"\x1a\x45\xdf\xa3webm" + b"synthetic"
URL = "https://upload.wikimedia.org/wikipedia/commons/a/ab/Synthetic.png"
THUMB = "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/Synthetic.png/240px-Synthetic.png"
SOURCE = "https://commons.wikimedia.org/wiki/File:Synthetic.png"


def response(mode="pic"):
    video = mode == "vid"
    return {"query": {"pages": [{"title": "File:Synthetic.png", "imageinfo": [{
        "mime": "video/webm" if video else "image/png", "size": len(WEBM if video else PNG),
        "width": 320, "height": 240, "url": URL, "thumburl": THUMB,
        "descriptionurl": SOURCE, "extmetadata": {"Artist": {"value": "<b>Test author</b>"},
        "LicenseShortName": {"value": "CC BY 4.0"}}}]}]}}


class Fake:
    def __init__(self):
        self.body = response()
        self.calls = []
        self.download = PNG
        self.mime = "image/png"
    def __call__(self, url, limit, kind):
        self.calls.append((url, limit, kind))
        if kind == "api":
            return json.dumps(self.body).encode(), "application/json"
        if url == THUMB:
            return PNG, "image/png"
        return self.download, self.mime


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fake = Fake()
        self.now = 10.
        self.service = InlineMediaService(Path(self.tmp.name), transport=self.fake, clock=lambda: self.now)
    def search(self, mode="pic"):
        self.now += 3
        return self.service.search({"mode": mode, "query": "synthetic test"})
    def test_constructor_does_not_query_and_requires_explicit_valid_search(self):
        self.assertEqual([], self.fake.calls)
        for payload in ({}, {"mode": "pic"}, {"mode": "pic", "query": "a"},
                        {"mode": "pic", "query": "a\nprivate"}, {"mode": "bad", "query": "hello"},
                        {"mode": "pic", "query": "x"*161}, {"mode": "pic", "query": "test", "url": URL},
                        [], {"mode": [], "query": "test"}):
            with self.subTest(payload=payload), self.assertRaises(InlineMediaError):
                self.service.search(payload)
        self.assertEqual([], self.fake.calls)
    def test_search_sanitizes_metadata_does_not_expose_original_media_url(self):
        result = self.search()
        row = result["results"][0]
        self.assertEqual("Wikimedia Commons", result["provider"])
        self.assertEqual("Test author", row["author"])
        self.assertEqual(SOURCE, row["source"])
        self.assertNotIn(URL, json.dumps(result))
        self.assertNotIn("synthetic test", json.dumps(result))
        query = parse_qs(urlsplit(self.fake.calls[0][0]).query)
        self.assertEqual(['filetype:bitmap "synthetic test"'], query["gsrsearch"])
    def test_rate_limit_and_query_is_literal_not_search_operator(self):
        self.service.search({"mode": "pic", "query": 'cat" filetype:video'})
        with self.assertRaises(InlineMediaError) as cm:
            self.service.search({"mode": "pic", "query": "test"})
        self.assertEqual("search_too_fast", cm.exception.code)
        query = parse_qs(urlsplit(self.fake.calls[0][0]).query)["gsrsearch"][0]
        self.assertEqual('filetype:bitmap "cat  filetype:video"', query)
    def test_select_stages_only_chosen_file_with_attribution(self):
        row = self.search()["results"][0]
        self.assertEqual(1, len(self.fake.calls))
        result = self.service.select({"id": row["id"]})
        files = list(Path(self.tmp.name).iterdir())
        self.assertEqual(1, len(files))
        self.assertEqual(PNG, files[0].read_bytes())
        self.assertEqual(files[0].as_uri(), result["url"])
        self.assertIn("Test author · CC BY 4.0", result["caption"])
        self.assertIn(SOURCE, result["caption"])
        self.assertEqual(2, len(self.fake.calls))
        self.assertEqual("media", self.fake.calls[-1][2])
    def test_video_uses_same_manual_draft_contract(self):
        self.fake.body = response("vid")
        self.fake.mime, self.fake.download = "video/webm", WEBM
        row = self.search("vid")["results"][0]
        result = self.service.select({"id": row["id"]})
        self.assertTrue(result["url"].endswith(".webm"))
        self.assertEqual("video/webm", result["mime"])
    def test_no_url_or_file_path_can_be_supplied_to_select(self):
        for payload in ({"id": URL}, {"id": "../file"}, {"id": "0"*32, "url": URL},
                        {"path": "D:/file"}, {"id": "0"*32}, None):
            with self.subTest(payload=payload), self.assertRaises(InlineMediaError):
                self.service.select(payload)
        self.assertEqual([], self.fake.calls)
    def test_expiry_prevents_download(self):
        row = self.search()["results"][0]
        self.now += RESULT_TTL_SECONDS + 1
        with self.assertRaises(InlineMediaError) as cm:
            self.service.select({"id": row["id"]})
        self.assertEqual("result_expired", cm.exception.code)
        self.assertEqual(1, len(self.fake.calls))
    def test_preview_is_image_only_bounded_and_cached(self):
        row = self.search()["results"][0]
        self.assertEqual((PNG, "image/png"), self.service.preview(row["id"]))
        self.assertEqual((PNG, "image/png"), self.service.preview(row["id"]))
        self.assertEqual(2, len(self.fake.calls))
        self.assertEqual(MAX_PREVIEW_BYTES, self.fake.calls[-1][1])
        self.assertEqual([], list(Path(self.tmp.name).iterdir()))
    def test_untrusted_response_rows_fail_closed(self):
        alterations = [("url", "https://127.0.0.1/private"), ("descriptionurl", "https://evil.example/a"),
                       ("thumburl", "http://upload.wikimedia.org/a"), ("mime", "text/html"),
                       ("mime", []), ("size", MAX_FILE_BYTES+1), ("size", True),
                       ("width", 999999), ("height", 0), ("extmetadata", {})]
        for field, value in alterations:
            with self.subTest(field=field, value=value):
                self.fake.body = response()
                self.fake.body["query"]["pages"][0]["imageinfo"][0][field] = value
                self.assertEqual([], self.search()["results"])
    def test_changed_download_mime_magic_or_length_does_not_write_files(self):
        row = self.search()["results"][0]
        for data, mime in ((b"<html>attack</html>", "image/png"), (PNG, "text/html"),
                           (PNG+b"extra", "image/png")):
            self.fake.download, self.fake.mime = data, mime
            with self.subTest(mime=mime), self.assertRaises(InlineMediaError):
                self.service.select({"id": row["id"]})
            self.assertEqual([], list(Path(self.tmp.name).iterdir()))
    def test_concurrent_selected_download_is_rejected(self):
        row = self.search()["results"][0]
        self.service._download_lock.acquire()
        try:
            with self.assertRaises(InlineMediaError) as cm:
                self.service.select({"id": row["id"]})
            self.assertEqual("download_busy", cm.exception.code)
        finally:
            self.service._download_lock.release()
    def test_cache_quota_prevents_download(self):
        row = self.search()["results"][0]
        file = Path(self.tmp.name)/('valmedia-'+'f'*32+'.png')
        with file.open('wb') as stream:
            # Quota uses logical length. Do not physically allocate 128 MiB
            # just to exercise the rejection path on an almost full volume.
            if os.name == "nt":
                import ctypes
                from ctypes import wintypes
                import msvcrt
                control = ctypes.WinDLL("kernel32", use_last_error=True).DeviceIoControl
                control.argtypes = (wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                                    ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p)
                control.restype = wintypes.BOOL
                returned = wintypes.DWORD()
                self.assertTrue(control(msvcrt.get_osfhandle(stream.fileno()), 0x900C4,
                                        None, 0, None, 0, ctypes.byref(returned), None),
                                "Cannot mark owned media quota fixture sparse")
            stream.truncate(MAX_CACHE_BYTES)
        with self.assertRaises(InlineMediaError) as cm:
            self.service.select({"id": row["id"]})
        self.assertEqual("cache_full", cm.exception.code)
        self.assertEqual(1, len(self.fake.calls))
    def test_only_own_staging_names_are_cleaned(self):
        other = Path(self.tmp.name)/"keep-me.txt"
        other.write_text("synthetic unrelated file")
        row = self.search()["results"][0]
        self.service.select({"id": row["id"]})
        self.assertTrue(other.is_file())
    def test_expired_owned_staging_is_removed_before_quota_check(self):
        old=Path(self.tmp.name)/('valmedia-'+'b'*32+'.png')
        old.write_bytes(PNG)
        os.utime(old,(0,0))
        row=self.search()['results'][0]
        self.service.select({'id':row['id']})
        self.assertFalse(old.exists())
    def test_result_inventory_is_bounded(self):
        for _ in range(60):
            self.search()
        self.assertEqual(48, len(self.service._entries))
    def test_cache_parent_is_checked_before_creating_children(self):
        parent=Path(self.tmp.name)/'link-parent'
        target=Path(self.tmp.name)/'outside'
        target.mkdir()
        try:
            parent.symlink_to(target,target_is_directory=True)
        except OSError:
            self.skipTest('Synthetic symlink creation unavailable')
        with self.assertRaises(ValueError):
            InlineMediaService(parent/'must-not-be-created',transport=self.fake)
        self.assertFalse((target/'must-not-be-created').exists())
    def test_hardlinked_cache_file_is_rejected_and_left_intact(self):
        source=Path(self.tmp.name)/'unrelated-source'
        source.write_bytes(PNG)
        alias=Path(self.tmp.name)/('valmedia-'+'a'*32+'.png')
        os.link(source,alias)
        row=self.search()['results'][0]
        with self.assertRaises(InlineMediaError) as cm:
            self.service.select({'id':row['id']})
        self.assertEqual('storage_unavailable',cm.exception.code)
        self.assertEqual(PNG,source.read_bytes())
        self.assertTrue(alias.exists())
        self.assertEqual(1,len(self.fake.calls))
    def test_bad_api_schema_does_not_expose_provider_response(self):
        for body in ([], {"error": {"info": "private-text-must-not-leak"}}, {"query": []},
                     {"query": {"pages": [1]*7}}):
            self.fake.body = body
            with self.subTest(body=body), self.assertRaises(InlineMediaError) as cm:
                self.search()
            self.assertEqual("provider_unavailable", str(cm.exception))


class URLTests(unittest.TestCase):
    def test_errors_have_finite_codes_and_http_statuses(self):
        for code,status in [('private payload',503),('invalid_input',200),('invalid_input',True),([],400)]:
            with self.subTest(status=status),self.assertRaises(ValueError):
                InlineMediaError(code,status)
        self.assertEqual('invalid_response',str(InlineMediaError('invalid_response',503)))
    def test_known_utm_fields_are_removed_but_unknown_query_is_rejected(self):
        self.assertEqual(URL,canonical_media_url(URL+'?utm_source=api&utm_campaign=stats&utm_content=image'))
        thumb=THUMB.replace('upload.wikimedia.org','thumb.wikimedia.org')
        self.assertEqual(thumb,canonical_media_url(thumb+'?utm_source=api'))
        with self.assertRaises(InlineMediaError):
            canonical_media_url(URL+'?utm_source=api&auth=private')
    def test_host_scheme_port_path_redirect_credentials_are_pinned(self):
        for url in ("http://upload.wikimedia.org/wikipedia/commons/a/x.png", "https://127.0.0.1/a", "https://upload.wikimedia.org.evil.example/wikipedia/commons/a/x.png",
                    "https://upload.wikimedia.org:444/wikipedia/commons/a/x.png", "https://u:p@upload.wikimedia.org/wikipedia/commons/a/x.png",
                    "https://upload.wikimedia.org/wikipedia/commons/../x.png", "https://upload.wikimedia.org/wikipedia/commons/%2e%2e/x.png",
                    URL+"?token=secret", URL+"#fragment", "https://upload.wikimedia.org/private.png", URL+"\r\nX: value"):
            with self.subTest(url=url), self.assertRaises(InlineMediaError):
                safe_url(url, "media")
        self.assertEqual(URL, safe_url(URL, "media"))
        self.assertEqual(SOURCE, safe_url(SOURCE, "source"))
    def test_redirect_to_non_wikimedia_url_is_denied_before_request(self):
        with self.assertRaises(InlineMediaError):
            _Redirects().redirect_request(Request(URL), None, 302, "", {}, "https://localhost/private")
    def test_magic_and_plain_metadata(self):
        self.assertTrue(valid_content(PNG, "image/png"))
        self.assertTrue(valid_content(WEBM, "video/webm"))
        self.assertFalse(valid_content(b"MZexe", "video/mp4"))
        self.assertFalse(valid_content(b"<html>", "image/png"))
        self.assertEqual("Name & author", plain('<a href="https://evil">Name &amp; author</a>'))
        self.assertEqual("abcd", plain("ab\u202ecd"))


if __name__ == "__main__":
    unittest.main()
