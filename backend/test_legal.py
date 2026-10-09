import json
from fastapi import FastAPI
from fastapi.testclient import TestClient

from .legal import router, source_page


def test_source_and_license_downloads_are_public_and_versioned(tmp_path, monkeypatch):
    monkeypatch.setenv("BINGDU_LEGAL_DIR", str(tmp_path))
    (tmp_path / "release.json").write_text(json.dumps({"version": "test-release", "source_sha256": "test-hash", "license": "AGPL-3.0-or-later"}))
    (tmp_path / "LICENSE").write_text("GNU AFFERO GENERAL PUBLIC LICENSE", encoding="utf-8")
    (tmp_path / "THIRD-PARTY-NOTICES.txt").write_text("EbookLib AGPL", encoding="utf-8")
    (tmp_path / "corresponding-source.zip").write_bytes(b"matching-source")
    app = FastAPI()
    app.include_router(router)
    app.add_api_route("/", source_page)
    client = TestClient(app)
    assert client.get("/api/legal").json()["version"] == "test-release"
    assert client.get("/api/legal/license").text == "GNU AFFERO GENERAL PUBLIC LICENSE"
    assert "EbookLib" in client.get("/api/legal/notices").text
    download = client.get("/api/legal/source")
    assert download.status_code == 200 and download.content == b"matching-source"
    assert "attachment" in download.headers["content-disposition"]
    probe = client.head("/api/legal/source")
    assert probe.status_code == 200 and probe.content == b""
    assert probe.headers["content-length"] == str(len(download.content))
    assert '/api/legal/source' in client.get("/").text
    # Do not expose arbitrary files from the release folder.
    (tmp_path / "private.txt").write_text("private")
    assert client.get("/api/legal/private.txt").status_code == 404


def test_missing_source_bundle_returns_actionable_error(tmp_path, monkeypatch):
    monkeypatch.setenv("BINGDU_LEGAL_DIR", str(tmp_path))
    app = FastAPI()
    app.include_router(router)
    response = TestClient(app).get("/api/legal/source")
    assert response.status_code == 503
    assert "prepare_legal.py" in response.json()["detail"]
