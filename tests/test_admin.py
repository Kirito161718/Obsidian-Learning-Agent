from app.routers import admin


def test_admin_dashboard_returns_html(client):
    response = client.get("/admin")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "RAG 知识库本地调试后台" in response.text
    assert 'id="retrieveForm"' in response.text
    assert 'id="documentChunksBox"' in response.text
    assert 'id="historyTableBody"' in response.text
    assert "历史问答" in response.text


def test_admin_static_assets_are_served(client):
    css_response = client.get("/static/admin.css")
    js_response = client.get("/static/admin.js")

    assert css_response.status_code == 200
    assert js_response.status_code == 200
    assert "page-shell" in css_response.text
    assert "chunk-card" in css_response.text
    assert "checkHealth" in js_response.text
    assert "retrieveRag" in js_response.text
    assert "loadDocumentChunks" in js_response.text
    assert "refreshHistory" in js_response.text
    assert "loadHistoryDetail" in js_response.text


def test_admin_status_returns_safe_rerank_config(client):
    response = client.get("/admin/status")

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body["rerank_enabled"], bool)
    assert body["rerank_model"]
    assert isinstance(body["rerank_top_n"], int)
    assert "api_key" not in body


def test_admin_dashboard_returns_404_when_disabled(client, monkeypatch):
    monkeypatch.setattr(admin.settings, "admin_enabled", False)

    response = client.get("/admin")

    assert response.status_code == 404
