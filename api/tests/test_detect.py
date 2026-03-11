"""
Tests for POST /api/detect — YOLO object detection endpoint.

detect_from_bytes() is patched in conftest.py to return a canned payload
(2 detections: person + bicycle) so no YOLO model or GPU is required.
"""

import io
import pytest

# Minimal valid 1×1 white JPEG — avoids PIL dependency in tests
_TINY_JPEG = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t"
    b"\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a"
    b"\x1f\x1e\x1d\x1a\x1c\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=82<.342\x1e"
    b"\x1e\r\r!!22!!22222222222222222222222222222222222222222222222222"
    b"\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00"
    b"\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b"
    b"\xff\xc4\x00\xb5\x10\x00\x02\x01\x03\x03\x02\x04\x03\x05\x05\x04"
    b"\x04\x00\x00\x01}\x01\x02\x03\x00\x04\x11\x05\x12!1A\x06\x13Qa"
    b"\x07\"q\x142\x81\x91\xa1\x08#B\xb1\xc1\x15R\xd1\xf0$3br\x82\t\n"
    b"\x16\x17\x18\x19\x1a%&'()*456789:CDEFGHIJSTUVWXYZ"
    b"cdefghijstuvwxyz\x83\x84\x85\x86\x87\x88\x89\x8a\x92\x93\x94"
    b"\x95\x96\x97\x98\x99\x9a\xa2\xa3\xa4\xa5\xa6\xa7\xa8\xa9\xaa"
    b"\xb2\xb3\xb4\xb5\xb6\xb7\xb8\xb9\xba\xc2\xc3\xc4\xc5\xc6\xc7"
    b"\xc8\xc9\xca\xd2\xd3\xd4\xd5\xd6\xd7\xd8\xd9\xda\xe1\xe2\xe3"
    b"\xe4\xe5\xe6\xe7\xe8\xe9\xea\xf1\xf2\xf3\xf4\xf5\xf6\xf7\xf8"
    b"\xf9\xfa\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xfb\xd8\xff\xd9"
)


class TestDetectEndpoint:
    # ------------------------------------------------------------------
    # Happy path
    # ------------------------------------------------------------------

    def test_detect_jpeg_returns_200(self, client):
        resp = client.post(
            "/api/detect",
            files={"file": ("test.jpg", io.BytesIO(_TINY_JPEG), "image/jpeg")},
        )
        assert resp.status_code == 200

    def test_detect_response_schema(self, client):
        resp = client.post(
            "/api/detect",
            files={"file": ("test.jpg", io.BytesIO(_TINY_JPEG), "image/jpeg")},
        )
        body = resp.json()
        assert "labels" in body
        assert "detections" in body
        assert "object_count" in body
        assert "model" in body
        assert "conf_threshold" in body
        assert "execution_time_ms" in body

    def test_detect_detections_have_required_fields(self, client):
        resp = client.post(
            "/api/detect",
            files={"file": ("test.jpg", io.BytesIO(_TINY_JPEG), "image/jpeg")},
        )
        det = resp.json()["detections"]
        assert len(det) == 2
        for d in det:
            assert "label" in d
            assert "confidence" in d
            assert "bbox" in d
            assert len(d["bbox"]) == 4

    def test_detect_labels_match_detections(self, client):
        resp = client.post(
            "/api/detect",
            files={"file": ("test.jpg", io.BytesIO(_TINY_JPEG), "image/jpeg")},
        )
        body = resp.json()
        detection_labels = {d["label"] for d in body["detections"]}
        assert set(body["labels"]) == detection_labels

    def test_detect_object_count_correct(self, client):
        resp = client.post(
            "/api/detect",
            files={"file": ("test.jpg", io.BytesIO(_TINY_JPEG), "image/jpeg")},
        )
        body = resp.json()
        assert body["object_count"] == len(body["detections"])

    def test_detect_model_name_present(self, client):
        resp = client.post(
            "/api/detect",
            files={"file": ("test.jpg", io.BytesIO(_TINY_JPEG), "image/jpeg")},
        )
        assert resp.json()["model"] == "yolov8n"

    def test_detect_execution_time_positive(self, client):
        resp = client.post(
            "/api/detect",
            files={"file": ("test.jpg", io.BytesIO(_TINY_JPEG), "image/jpeg")},
        )
        assert resp.json()["execution_time_ms"] >= 0

    def test_detect_png_accepted(self, client):
        """PNG content-type should be accepted (415 guard passes)."""
        resp = client.post(
            "/api/detect",
            files={"file": ("test.png", io.BytesIO(_TINY_JPEG), "image/png")},
        )
        assert resp.status_code == 200

    def test_detect_webp_accepted(self, client):
        resp = client.post(
            "/api/detect",
            files={"file": ("test.webp", io.BytesIO(_TINY_JPEG), "image/webp")},
        )
        assert resp.status_code == 200

    # ------------------------------------------------------------------
    # Error handling
    # ------------------------------------------------------------------

    def test_detect_empty_file_returns_400(self, client):
        resp = client.post(
            "/api/detect",
            files={"file": ("empty.jpg", io.BytesIO(b""), "image/jpeg")},
        )
        assert resp.status_code == 400

    def test_detect_wrong_mime_returns_415(self, client):
        resp = client.post(
            "/api/detect",
            files={"file": ("doc.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
        )
        assert resp.status_code == 415

    def test_detect_text_mime_returns_415(self, client):
        resp = client.post(
            "/api/detect",
            files={"file": ("data.csv", io.BytesIO(b"a,b,c"), "text/csv")},
        )
        assert resp.status_code == 415

    def test_detect_missing_file_returns_422(self, client):
        """FastAPI returns 422 Unprocessable Entity when required field is absent."""
        resp = client.post("/api/detect", data={})
        assert resp.status_code == 422

    def test_detect_503_when_yolo_unavailable(self, client, monkeypatch):
        """
        When detect_from_bytes raises RuntimeError (ultralytics not installed),
        the endpoint should return 503 rather than 500.
        """
        import routers.detect as detect_mod
        monkeypatch.setattr(
            detect_mod, "detect_from_bytes",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("ultralytics not installed")),
        )
        resp = client.post(
            "/api/detect",
            files={"file": ("test.jpg", io.BytesIO(_TINY_JPEG), "image/jpeg")},
        )
        assert resp.status_code == 503

    def test_detect_conf_param_passed(self, client, monkeypatch):
        """
        The optional ?conf= query param should be forwarded to detect_from_bytes.
        """
        captured = {}

        def _capture(image_bytes, conf=None, **kw):
            captured["conf"] = conf
            return {
                "yolo_labels": [],
                "yolo_detections": [],
                "yolo_object_count": 0,
                "yolo_model": "yolov8n",
                "yolo_conf_threshold": conf or 0.25,
            }

        import routers.detect as detect_mod
        monkeypatch.setattr(detect_mod, "detect_from_bytes", _capture)
        client.post(
            "/api/detect?conf=0.5",
            files={"file": ("test.jpg", io.BytesIO(_TINY_JPEG), "image/jpeg")},
        )
        assert captured["conf"] == 0.5
