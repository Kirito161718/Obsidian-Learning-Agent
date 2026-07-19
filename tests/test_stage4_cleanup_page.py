from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.db.models import (
    AgentRun,
    LearningGoal,
    LearningSession,
    LearningSessionReview,
    LearningStatus,
    ObsidianNote,
    QAHistory,
    ToolExecution,
)


def seed_cleanup_data(db_session):
    goal = LearningGoal(name="Keep", goal_type="learning", priority=3, status="active", related_topics=[])
    status = LearningStatus(topic="Keep", status="learning", mastery_level=1, manual_importance=3)
    note = ObsidianNote(relative_path="Keep.md", file_name="Keep.md", index_status="indexed", chunk_count=0)
    db_session.add_all([goal, status, note])
    db_session.commit()
    session = LearningSession(
        title="Keep",
        goal_id=goal.id,
        learning_status_id=status.id,
        source_types=["obsidian_note"],
        source_path_prefixes=[],
    )
    db_session.add(session)
    db_session.commit()
    review = LearningSessionReview(
        session_id=session.id,
        knowledge_summary="Keep",
        covered_topics=[],
        unresolved_questions=[],
        next_steps=[],
        source_qa_ids=[],
    )
    old = datetime.now(UTC) - timedelta(days=20)
    recent = datetime.now(UTC)
    old_run = AgentRun(run_type="rag_ask", session_id=session.id, status="succeeded", created_at=old, started_at=old)
    recent_run = AgentRun(run_type="rag_retrieve", status="succeeded", created_at=recent, started_at=recent)
    db_session.add_all([review, old_run, recent_run])
    db_session.commit()
    qa = QAHistory(question="keep", rewritten_query="keep", answer="keep", source_chunks=[], session_id=session.id, run_id=old_run.id)
    old_tool = ToolExecution(
        run_id=old_run.id,
        tool_name="old",
        risk_level="READ_ONLY",
        caller_type="user",
        decision="allowed",
        status="succeeded",
        input_hash="a" * 64,
        input_summary={},
        output_summary={},
        created_at=old,
    )
    recent_tool = ToolExecution(
        tool_name="recent",
        risk_level="READ_ONLY",
        caller_type="user",
        decision="allowed",
        status="succeeded",
        input_hash="b" * 64,
        input_summary={},
        output_summary={},
        created_at=recent,
    )
    db_session.add_all([qa, old_tool, recent_tool])
    db_session.commit()
    return {
        "goal": goal,
        "status": status,
        "note": note,
        "session": session,
        "review": review,
        "old_run": old_run,
        "recent_run": recent_run,
        "qa": qa,
        "old_tool": old_tool,
        "recent_tool": recent_tool,
    }


def test_cleanup_dry_run_counts_without_deleting(client, db_session):
    data = seed_cleanup_data(db_session)
    response = client.post("/agent/maintenance/cleanup-history", json={"dry_run": True, "older_than_days": 10})
    assert response.status_code == 200
    body = response.json()
    assert body["agent_runs"] == 1 and body["tool_executions"] == 1
    assert body["qa_history_links_cleared"] == 1
    assert db_session.get(AgentRun, data["old_run"].id) is not None
    assert db_session.get(ToolExecution, data["old_tool"].id) is not None


def test_cleanup_apply_deletes_only_history_and_nulls_qa_link(client, db_session):
    data = seed_cleanup_data(db_session)
    ids = {name: record.id for name, record in data.items()}
    response = client.post("/agent/maintenance/cleanup-history", json={"dry_run": False, "older_than_days": 10})
    assert response.status_code == 200
    db_session.expire_all()
    assert db_session.get(AgentRun, ids["old_run"]) is None
    assert db_session.get(ToolExecution, ids["old_tool"]) is None
    assert db_session.get(AgentRun, ids["recent_run"]) is not None
    assert db_session.get(ToolExecution, ids["recent_tool"]) is not None
    assert db_session.get(QAHistory, ids["qa"]).run_id is None
    assert db_session.get(LearningSession, ids["session"]) is not None
    assert db_session.get(LearningSessionReview, ids["review"]) is not None
    assert db_session.get(LearningGoal, ids["goal"]) is not None
    assert db_session.get(LearningStatus, ids["status"]) is not None
    assert db_session.get(ObsidianNote, ids["note"]) is not None


def test_cleanup_rejects_less_than_one_day(client):
    assert client.post("/agent/maintenance/cleanup-history", json={"older_than_days": 0}).status_code == 422


def test_agent_page_contains_all_stage4_sections(client):
    response = client.get("/agent")
    assert response.status_code == 200
    html = response.text
    for text in ("用户偏好", "Context Preview", "Run History", "Tool Registry", "Tool Audit", "预览清理"):
        assert text in html
    assert "OBSIDIAN_VAULT_PATH" not in html and "C:\\Users" not in html


def test_admin_and_learning_link_to_agent_console(client):
    assert 'href="/agent"' in client.get("/admin").text
    assert 'href="/agent"' in client.get("/learning").text


def test_stage4_migration_declares_single_next_revision_and_safe_downgrade_order():
    source = Path("alembic/versions/20260719_0005_add_memory_context_tools.py").read_text(encoding="utf-8")
    assert 'revision = "20260719_0005"' in source
    assert 'down_revision = "20260718_0004"' in source
    for name in ("user_preferences", "agent_runs", "tool_executions", "memory_enabled", "run_id"):
        assert name in source
    assert source.index('op.drop_constraint("fk_qa_history_run_id_agent_runs"') < source.index('op.drop_column("qa_history", "run_id")')
    assert source.index('op.drop_column("qa_history", "run_id")') < source.index('op.drop_table("agent_runs")')


def test_stage4_javascript_files_exist():
    assert Path("app/static/agent.js").exists()
    assert Path("app/static/learning.js").exists()
