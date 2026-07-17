import pytest

from app.obsidian.markdown_chunker import StructuredMarkdownChunker
from app.obsidian.markdown_parser import MarkdownParser


def chunk(markdown: str, *, minimum=20, target=80, maximum=120, hard=200):
    note = MarkdownParser().parse(markdown, "Folder/Note.md")
    return StructuredMarkdownChunker(
        min_chars=minimum,
        target_chars=target,
        max_chars=maximum,
        hard_max_chars=hard,
    ).chunk(note, note_id="11111111-1111-1111-1111-111111111111", note_hash="a" * 64)


def test_chunker_never_merges_across_headings():
    chunks = chunk("# A\nshort\n# B\nshort")
    assert len(chunks) == 2
    assert chunks[0].heading_path == ("A",)
    assert chunks[1].heading_path == ("B",)


def test_chunker_merges_adjacent_blocks_within_heading():
    chunks = chunk("# A\none\n\ntwo")
    assert len(chunks) == 1
    assert "one\n\ntwo" in chunks[0].text


def test_chunker_stable_ids_for_identical_input():
    first = chunk("# A\ntext")
    second = chunk("# A\ntext")
    assert first[0].chunk_id == second[0].chunk_id
    assert first[0].parent_section_id == second[0].parent_section_id


def test_chunker_id_changes_with_note_hash():
    note = MarkdownParser().parse("# A\ntext", "Folder/Note.md")
    chunker = StructuredMarkdownChunker(min_chars=20, target_chars=80, max_chars=120, hard_max_chars=200)
    first = chunker.chunk(note, note_id="id", note_hash="a")[0]
    second = chunker.chunk(note, note_id="id", note_hash="b")[0]
    assert first.chunk_id != second.chunk_id


def test_chunker_records_required_metadata():
    item = chunk("# A\ntext #tag [[Target]]")[0]
    assert item.relative_path == "Folder/Note.md"
    assert item.line_start == 2
    assert item.line_end == 2
    assert item.content_hash
    assert item.tags == ("tag",)
    assert item.links[0]["target"] == "Target"


def test_chunker_splits_long_paragraph_at_maximum():
    chunks = chunk("# A\n" + "x" * 270)
    assert len(chunks) == 3
    assert all(len(item.text) <= 120 for item in chunks)


def test_chunker_prefers_sentence_boundaries():
    chunks = chunk("# A\n" + "第一句。" * 40)
    assert len(chunks) > 1
    assert all(item.text.endswith("。") for item in chunks)


def test_chunker_keeps_normal_code_block_atomic():
    markdown = "# A\n```python\n" + "x" * 130 + "\n```"
    chunks = chunk(markdown, hard=250)
    assert len(chunks) == 1
    assert chunks[0].block_types == ("code",)


def test_chunker_splits_code_over_hard_limit_with_legal_fences():
    body = "\n".join("print('long line')" for _ in range(30))
    chunks = chunk(f"# A\n```python\n{body}\n```", maximum=100, hard=160)
    assert len(chunks) > 1
    assert all(item.text.startswith("```python") and item.text.endswith("```") for item in chunks)
    assert all("code_split" in item.metadata["split_flags"] for item in chunks)


def test_chunker_splits_long_table_and_repeats_headers():
    rows = "\n".join(f"| {i} | value {i} |" for i in range(30))
    markdown = f"# A\n| N | Value |\n|---|---|\n{rows}"
    chunks = chunk(markdown, maximum=120, hard=200)
    assert len(chunks) > 1
    assert all(item.text.startswith("| N | Value |\n|---|---|") for item in chunks)


@pytest.mark.parametrize("block", ["- item\n  continuation", "> quote\n> continuation", "> [!TIP]\n> body"])
def test_chunker_keeps_lists_quotes_and_callouts_atomic_below_hard_limit(block):
    assert len(chunk("# A\n" + block, target=20, maximum=25, hard=100)) == 1


def test_chunker_uses_forward_slash_relative_path_only():
    item = chunk("Body")[0]
    assert item.relative_path == "Folder/Note.md"
    assert ":\\" not in item.relative_path


def test_chunker_validates_limit_order():
    with pytest.raises(ValueError):
        StructuredMarkdownChunker(min_chars=100, target_chars=50, max_chars=120, hard_max_chars=200)
