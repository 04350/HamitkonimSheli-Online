import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import cloud_client


class CloudClientTests(unittest.TestCase):
    def tearDown(self):
        cloud_client.set_api_base_url(cloud_client.DEFAULT_CLOUD_API_URL)

    def test_rejects_plain_http_except_localhost(self):
        with self.assertRaises(ValueError):
            cloud_client.set_api_base_url("http://example.com")
        self.assertEqual(
            cloud_client.set_api_base_url("http://127.0.0.1:8000"),
            "http://127.0.0.1:8000",
        )

    def test_empty_url_disables_online_service(self):
        self.assertEqual(cloud_client.set_api_base_url(""), "")
        with self.assertRaises(cloud_client.CloudServiceError):
            cloud_client.cloud_health_check()

    @patch("cloud_client.urlopen")
    def test_health_check(self, mocked_urlopen):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, size=-1):
                return json.dumps({"status": "ok", "ocr": True, "office": True}).encode()
        cloud_client.set_api_base_url("https://example.test")
        mocked_urlopen.return_value = Response()
        result = cloud_client.cloud_health_check()
        self.assertTrue(result["ocr"])
        mocked_urlopen.assert_called_once()


    def test_large_response_is_rejected_before_full_download(self):
        class Response:
            headers = {}
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, size=-1): return b"x" * size
        cloud_client.set_api_base_url("https://example.test")
        with patch.object(cloud_client, "MAX_RESPONSE_BYTES", 16), patch(
            "cloud_client.urlopen", return_value=Response()
        ):
            with self.assertRaisesRegex(cloud_client.CloudServiceError, "100MB"):
                cloud_client._post_file("/test", b"small", "a.txt", "text/plain")

    def test_oversized_office_file_is_rejected_before_reading(self):
        cloud_client.set_api_base_url("https://example.test")
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "large.docx"
            source.write_bytes(b"1234")
            with patch.object(cloud_client, "MAX_UPLOAD_BYTES", 3), patch.object(
                Path, "read_bytes", side_effect=AssertionError("file must not be read")
            ):
                with self.assertRaisesRegex(cloud_client.CloudServiceError, "גדול מדי"):
                    cloud_client.cloud_convert_office(source)

    @patch("cloud_client.urlopen")
    def test_health_check_rejects_non_object_json(self, mocked_urlopen):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, size=-1): return b"[]"
        cloud_client.set_api_base_url("https://example.test")
        mocked_urlopen.return_value = Response()
        with self.assertRaisesRegex(cloud_client.CloudServiceError, "אינו מוכן"):
            cloud_client.cloud_health_check()

if __name__ == "__main__":
    unittest.main()
