def test_stage1_admin_page_remains_accessible(client):
    response = client.get("/admin")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_stage1_admin_page_contains_goal_and_learning_status_areas(client):
    html = client.get("/admin").text
    assert 'id="learningGoalsPanel"' in html
    assert 'id="goalForm"' in html
    assert 'id="learningStatusPanel"' in html
    assert 'id="learningStatusForm"' in html
    assert "学习目标" in html
    assert "学习状态" in html


def test_stage1_admin_javascript_and_css_are_served(client):
    js = client.get("/static/admin.js")
    css = client.get("/static/admin.css")
    assert js.status_code == 200
    assert css.status_code == 200
    assert "refreshLearningStatuses" in js.text
    assert ".importance-stars" in css.text


def test_stage1_admin_javascript_uses_crud_api_paths(client):
    javascript = client.get("/static/admin.js").text
    assert 'requestJson("/goals")' in javascript
    assert "`/goals/${encodeURIComponent(goalId)}`" in javascript
    assert 'requestJson("/learning-status")' in javascript
    assert "`/learning-status/${encodeURIComponent(statusId)}`" in javascript
