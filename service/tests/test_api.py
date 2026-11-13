import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
import app


class ProcessingApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app.app)

    def test_health_shape(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "ok")
        self.assertIn("ocr", body)
        self.assertIn("office", body)

    @patch("app.recognize_image")
    def test_image_ocr_endpoint(self, mocked_ocr):
        mocked_ocr.return_value = {
            "text": "עוגת תפוחים\nמצרכים: תפוחים",
            "title": "עוגת תפוחים",
            "confidence": 91.5,
            "lines": [{"text": "עוגת תפוחים"}],
        }
        buffer = io.BytesIO()
        Image.new("RGB", (180, 100), "white").save(buffer, format="PNG")
        response = self.client.post(
            "/api/v1/ocr/image",
            files={"file": ("scan.png", buffer.getvalue(), "image/png")},
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["title"], "עוגת תפוחים")
        self.assertIn("תפוחים", body["text"])
        mocked_ocr.assert_called_once()

    def test_bad_image_is_rejected(self):
        response = self.client.post(
            "/api/v1/ocr/image",
            files={"file": ("bad.png", b"not an image", "image/png")},
        )
        self.assertEqual(response.status_code, 400)

    def test_office_extension_is_validated(self):
        response = self.client.post(
            "/api/v1/office/convert",
            files={"file": ("notes.txt", b"hello", "text/plain")},
        )
        self.assertEqual(response.status_code, 415)


if __name__ == "__main__":
    unittest.main()
