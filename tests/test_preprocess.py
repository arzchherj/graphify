"""Tests for graphify.preprocess"""
from __future__ import annotations

from pathlib import Path

import pytest

from graphify.preprocess import (
    build_extraction_prompt,
    make_concept_filename,
    parse_concept_cards,
    preprocess_dir,
    split_document,
    split_markdown,
    split_text,
    write_concept_card,
)


# ---------------------------------------------------------------------------
# split_markdown
# ---------------------------------------------------------------------------


def test_split_markdown_by_h1(tmp_path):
    md = tmp_path / "book.md"
    md.write_text("# Chapter 1\n\nContent one.\n\n# Chapter 2\n\nContent two.\n")
    chunks = split_markdown(md)
    assert len(chunks) == 2
    assert chunks[0]["title"] == "Chapter 1"
    assert chunks[1]["title"] == "Chapter 2"
    assert all(c["source_file"] == str(md) for c in chunks)


def test_split_markdown_respects_max_level(tmp_path):
    md = tmp_path / "book.md"
    md.write_text(
        "# Part 1\n\nIntro.\n\n## Section 1.1\n\nDetail.\n\n### Sub 1.1.1\n\nDeep.\n"
    )
    chunks = split_markdown(md, max_level=2)
    titles = [c["title"] for c in chunks]
    assert "Part 1" in titles
    assert "Section 1.1" in titles
    # Sub-section at level 3 should NOT be a split point
    assert not any("Sub 1.1.1" == t for t in titles)


def test_split_markdown_no_headings(tmp_path):
    md = tmp_path / "flat.md"
    md.write_text("Just some content without any headings.\n")
    chunks = split_markdown(md)
    assert len(chunks) == 1
    assert chunks[0]["title"] == "flat"
    assert "Just some content" in chunks[0]["content"]


def test_split_markdown_chapter_index(tmp_path):
    md = tmp_path / "book.md"
    md.write_text("# A\n\nContent A.\n\n# B\n\nContent B.\n\n# C\n\nContent C.\n")
    chunks = split_markdown(md)
    indices = [c["chapter_index"] for c in chunks]
    assert indices == sorted(indices), "chapter_index should be monotonically increasing"
    assert len(set(indices)) == len(indices), "chapter_index values should be unique"


def test_split_markdown_pre_heading_content(tmp_path):
    md = tmp_path / "book.md"
    md.write_text("Preamble before any heading.\n\n# Chapter 1\n\nContent.\n")
    chunks = split_markdown(md)
    # Preamble + Chapter 1
    assert len(chunks) == 2
    assert "Preamble" in chunks[0]["content"]


# ---------------------------------------------------------------------------
# split_text
# ---------------------------------------------------------------------------


def test_split_text_chapter_headers(tmp_path):
    txt = tmp_path / "notes.txt"
    txt.write_text(
        "Chapter 1. Introduction\n\nSome intro text.\n\nChapter 2. Methods\n\nMethod details.\n"
    )
    chunks = split_text(txt)
    assert len(chunks) == 2
    assert "Chapter 1" in chunks[0]["title"]
    assert "Chapter 2" in chunks[1]["title"]


def test_split_text_no_headers(tmp_path):
    txt = tmp_path / "flat.txt"
    txt.write_text("Just a flat text file with no chapter markers.\n")
    chunks = split_text(txt)
    assert len(chunks) == 1
    assert chunks[0]["title"] == "flat"


def test_split_text_section_symbol(tmp_path):
    txt = tmp_path / "course.txt"
    txt.write_text("§1 Overview\n\nIntroductory material.\n\n§2 Details\n\nDetailed content.\n")
    chunks = split_text(txt)
    assert len(chunks) == 2


# ---------------------------------------------------------------------------
# split_document dispatcher
# ---------------------------------------------------------------------------


def test_split_document_markdown(tmp_path):
    p = tmp_path / "doc.md"
    p.write_text("# Head\n\nBody.\n")
    chunks = split_document(p)
    assert len(chunks) >= 1


def test_split_document_txt(tmp_path):
    p = tmp_path / "doc.txt"
    p.write_text("Chapter 1. Hello\n\nWorld.\n")
    chunks = split_document(p)
    assert len(chunks) >= 1


def test_split_document_unsupported(tmp_path):
    p = tmp_path / "doc.html"
    p.write_text("<html>content</html>")
    chunks = split_document(p)
    assert len(chunks) == 1
    assert chunks[0]["source_file"] == str(p)


# ---------------------------------------------------------------------------
# make_concept_filename
# ---------------------------------------------------------------------------


def test_make_concept_filename_ascii():
    assert make_concept_filename("Attention Mechanism") == "attention_mechanism.md"


def test_make_concept_filename_with_index():
    name = make_concept_filename("Backpropagation", index=3)
    assert name == "backpropagation_0003.md"


def test_make_concept_filename_cjk():
    # CJK-only titles should fall back to a hex digest
    name = make_concept_filename("注意力机制")
    assert name.endswith(".md")
    assert len(name) > 4


def test_make_concept_filename_max_len():
    long_name = "a" * 200
    name = make_concept_filename(long_name)
    # Slug portion should be at most 60 chars + '.md'
    assert len(name) <= 64


# ---------------------------------------------------------------------------
# parse_concept_cards
# ---------------------------------------------------------------------------

_SAMPLE_LLM_OUTPUT = """\
Some preamble the LLM added (should be ignored).

---CONCEPT: Attention Mechanism---
定义：A mechanism that allows models to focus on relevant parts of the input.
核心要点：
- Computes query, key, value vectors
- Produces a weighted sum over values
- Enables parallel processing
与其他概念的关系：
- Attention Mechanism depends on Softmax
- Attention Mechanism is used by Transformer
来源：deep_learning_book.pdf > Chapter 12
---END---

---CONCEPT: Softmax---
定义：A function that converts a vector of numbers into probabilities.
核心要点：
- Outputs values in (0, 1)
- Values sum to 1
与其他概念的关系：
- Softmax is used by Attention Mechanism
来源：deep_learning_book.pdf > Chapter 6
---END---
"""


def test_parse_concept_cards_count():
    cards = parse_concept_cards(_SAMPLE_LLM_OUTPUT)
    assert len(cards) == 2


def test_parse_concept_cards_fields():
    cards = parse_concept_cards(_SAMPLE_LLM_OUTPUT)
    attn = cards[0]
    assert attn["name"] == "Attention Mechanism"
    assert "focus on relevant parts" in attn["definition"]
    assert len(attn["key_points"]) == 3
    assert len(attn["relations"]) == 2
    assert "Chapter 12" in attn["source"]


def test_parse_concept_cards_empty_output():
    assert parse_concept_cards("") == []


def test_parse_concept_cards_malformed_skipped():
    # Card with no definition or key points should be skipped
    bad = "---CONCEPT: Orphan---\n---END---\n"
    cards = parse_concept_cards(bad)
    assert cards == []


def test_parse_concept_cards_partial():
    # Only definition, no key_points
    partial = (
        "---CONCEPT: Gradient Descent---\n"
        "定义：An optimisation algorithm.\n"
        "---END---\n"
    )
    cards = parse_concept_cards(partial)
    assert len(cards) == 1
    assert cards[0]["name"] == "Gradient Descent"


# ---------------------------------------------------------------------------
# write_concept_card
# ---------------------------------------------------------------------------


def test_write_concept_card_creates_file(tmp_path):
    out = write_concept_card(
        concept_name="Attention Mechanism",
        definition="Allows focusing on relevant input parts.",
        key_points=["Uses query/key/value", "Parallel"],
        relations=["Depends on Softmax"],
        source="book.pdf > Chapter 12",
        output_dir=tmp_path,
    )
    assert out.exists()
    assert out.suffix == ".md"


def test_write_concept_card_content(tmp_path):
    out = write_concept_card(
        concept_name="Backpropagation",
        definition="Algorithm to compute gradients.",
        key_points=["Chain rule", "Reverse pass"],
        relations=["Used by Gradient Descent"],
        source="mlbook.pdf",
        output_dir=tmp_path,
    )
    content = out.read_text()
    assert "Backpropagation" in content
    assert "Algorithm to compute gradients" in content
    assert "Chain rule" in content
    assert "Used by Gradient Descent" in content
    assert "mlbook.pdf" in content


def test_write_concept_card_no_overwrite(tmp_path):
    kwargs = dict(
        concept_name="Test Concept",
        definition="A test.",
        key_points=["p1"],
        relations=[],
        source="src",
        output_dir=tmp_path,
    )
    out1 = write_concept_card(**kwargs)
    out2 = write_concept_card(**kwargs)
    assert out1 != out2  # second write gets a different filename
    assert out1.exists() and out2.exists()


def test_write_concept_card_creates_dir(tmp_path):
    nested = tmp_path / "deep" / "learning"
    assert not nested.exists()
    write_concept_card(
        concept_name="Dropout",
        definition="Regularisation technique.",
        key_points=["Randomly zeros activations"],
        relations=[],
        source="dlbook.pdf",
        output_dir=nested,
    )
    assert nested.exists()


# ---------------------------------------------------------------------------
# build_extraction_prompt
# ---------------------------------------------------------------------------


def test_build_extraction_prompt_contains_content():
    chunk = {
        "title": "Chapter 3: Attention",
        "content": "Attention allows the model to...",
        "source_file": "transformer_book.pdf",
        "chapter_index": 2,
    }
    prompt = build_extraction_prompt(chunk)
    assert "Chapter 3: Attention" in prompt
    assert "Attention allows the model" in prompt
    assert "transformer_book.pdf" in prompt


# ---------------------------------------------------------------------------
# preprocess_dir (integration)
# ---------------------------------------------------------------------------


def test_preprocess_dir_creates_manifest(tmp_path):
    doc_dir = tmp_path / "books"
    doc_dir.mkdir()
    (doc_dir / "book.md").write_text("# Chapter 1\n\nContent.\n\n# Chapter 2\n\nMore content.\n")

    out_dir = tmp_path / "concepts"
    chunks = preprocess_dir(doc_dir, out_dir, verbose=False)

    assert (out_dir / ".graphify_chunks.json").exists()
    assert len(chunks) == 2


def test_preprocess_dir_empty(tmp_path):
    doc_dir = tmp_path / "empty"
    doc_dir.mkdir()
    out_dir = tmp_path / "out"
    chunks = preprocess_dir(doc_dir, out_dir, verbose=False)
    assert chunks == []


def test_preprocess_dir_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        preprocess_dir(tmp_path / "nonexistent", tmp_path / "out", verbose=False)


def test_preprocess_dir_multiple_files(tmp_path):
    doc_dir = tmp_path / "books"
    doc_dir.mkdir()
    (doc_dir / "a.md").write_text("# A1\n\nContent A1.\n\n# A2\n\nContent A2.\n")
    (doc_dir / "b.txt").write_text("Chapter 1. B1\n\nContent B1.\n\nChapter 2. B2\n\nContent B2.\n")

    out_dir = tmp_path / "concepts"
    chunks = preprocess_dir(doc_dir, out_dir, verbose=False)

    # 2 chunks from a.md + 2 from b.txt
    assert len(chunks) == 4
    sources = {c["source_file"] for c in chunks}
    assert len(sources) == 2
