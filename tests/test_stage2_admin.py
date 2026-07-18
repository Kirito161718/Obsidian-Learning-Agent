def test_admin_contains_read_only_obsidian_area(client):
    html = client.get("/admin").text
    assert 'id="obsidianPanel"' in html
    assert "Obsidian Vault（只读）" in html
    assert 'id="obsidianScanBtn"' in html
    assert 'id="obsidianIndexBtn"' in html


def test_admin_contains_rag_source_scope_controls(client):
    html = client.get("/admin").text
    assert 'id="retrieveUploadedSourceInput"' in html
    assert 'id="retrieveObsidianSourceInput"' in html
    assert 'id="retrievePathPrefixesInput"' in html
    assert 'id="askObsidianSourceInput"' in html


def test_admin_javascript_uses_obsidian_read_and_index_apis(client):
    javascript = client.get("/static/admin.js").text
    for path in ("/obsidian/status", "/obsidian/scan", "/obsidian/index", "/obsidian/notes"):
        assert path in javascript
    assert "source_path_prefixes" in javascript
    assert '"obsidian_note"' in javascript


def test_admin_obsidian_area_has_no_vault_file_delete_or_edit_action(client):
    html = client.get("/admin").text
    panel = html.split('id="obsidianPanel"', 1)[1].split("</section>", 1)[0]
    assert "删除笔记" not in panel
    assert "编辑笔记" not in panel
    assert "移动笔记" not in panel
