from pathlib import Path


def test_learning_page_contains_workspace_controls(client):
    response = client.get("/learning")
    assert response.status_code == 200
    text = response.text
    for marker in (
        "学习会话", "当前目标", "当前学习状态", "来源范围", "输入问题", "本次 Sources",
        "保存学习状态", "将本次引用笔记关联到当前学习状态", "生成本次学习复盘",
    ):
        assert marker in text


def test_admin_contains_learning_workspace_entry(client):
    response = client.get("/admin")
    assert response.status_code == 200
    assert 'href="/learning"' in response.text


def test_learning_javascript_uses_safe_obsidian_uri_and_relative_source_path():
    source = Path("app/static/learning.js").read_text(encoding="utf-8")
    assert "obsidian://open?vault=" in source
    assert "encodeURIComponent(source.source_path)" in source
    assert "obsidian_vault_path" not in source
    assert "保存学习状态" not in source or "PATCH" in source


def test_learning_page_does_not_embed_absolute_vault_path(client, monkeypatch):
    response = client.get("/learning")
    assert "C:\\" not in response.text
