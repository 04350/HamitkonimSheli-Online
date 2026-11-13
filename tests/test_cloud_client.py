import json
import unittest
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


if __name__ == "__main__":
    unittest.main()
