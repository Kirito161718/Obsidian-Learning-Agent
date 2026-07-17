import pytest

from app.obsidian.markdown_parser import MarkdownParser


def parse(text: str):
    return MarkdownParser().parse(text, "Knowledge/Note.md")


def test_parser_uses_frontmatter_title_tags_and_aliases():
    note = parse("---\ntitle: My Note\ntags: [rag, ai]\naliases: [RAG Note]\n---\nBody")
    assert note.title == "My Note"
    assert note.tags == ["rag", "ai"]
    assert note.aliases == ["RAG Note"]


def test_parser_falls_back_to_first_h1_title():
    assert parse("# Heading\nBody").title == "Heading"


def test_parser_falls_back_to_file_stem():
    assert parse("Body").title == "Note"


def test_parser_preserves_heading_hierarchy():
    note = parse("# One\ntext\n## Two\nmore\n### Three\nend")
    assert [block.heading_path for block in note.blocks] == [("One",), ("One", "Two"), ("One", "Two", "Three")]


@pytest.mark.parametrize(
    ("markdown", "block_type"),
    [
        ("paragraph text", "paragraph"),
        ("- one\n  - two", "list"),
        ("> quote\n> more", "blockquote"),
        ("> [!NOTE] title\n> body", "callout"),
        ("```python\nprint('x')\n```", "code"),
        ("| A | B |\n|---|---|\n| 1 | 2 |", "table"),
    ],
)
def test_parser_recognizes_structural_blocks(markdown, block_type):
    assert parse(markdown).blocks[0].block_type == block_type


def test_parser_extracts_wikilink_alias_heading_and_embed():
    links = parse("[[Target#Section|Alias]] and ![[Image.png]]").outbound_links
    assert links[0] == {"target": "Target", "heading": "Section", "alias": "Alias", "embed": False}
    assert links[1]["embed"] is True


def test_parser_deduplicates_wikilinks():
    assert len(parse("[[Target]] [[Target]]").outbound_links) == 1


def test_parser_extracts_inline_tags_outside_code():
    note = parse("Body #rag\n\n```\n#not-a-tag\n```")
    assert note.tags == ["rag"]


def test_parser_deduplicates_frontmatter_and_inline_tags():
    note = parse("---\ntags: [rag]\n---\n#rag #agent")
    assert note.tags == ["rag", "agent"]


def test_parser_handles_invalid_yaml_and_continues_body():
    note = parse("---\ninvalid: [\n---\nBody after")
    assert note.warnings == ["frontmatter_yaml_invalid"]
    assert note.blocks[0].text == "Body after"


def test_parser_handles_missing_frontmatter_closer_as_body():
    note = parse("---\ntitle: Broken\nBody")
    assert "frontmatter_closing_delimiter_missing" in note.warnings
    assert note.blocks


def test_parser_converts_yaml_date_to_json_safe_string():
    note = parse("---\ncreated: 2026-07-17\n---\nBody")
    assert note.frontmatter["created"] == "2026-07-17"


def test_parser_records_original_line_numbers_after_frontmatter():
    note = parse("---\ntitle: X\n---\n# H\nBody")
    assert note.blocks[0].line_start == 5
    assert note.blocks[0].line_end == 5


def test_parser_does_not_include_absolute_path():
    note = parse("Body")
    assert note.relative_path == "Knowledge/Note.md"
    assert not hasattr(note, "absolute_path")
