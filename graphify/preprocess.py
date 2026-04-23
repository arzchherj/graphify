# graphify preprocess - split documents into concept-card files before graphify extraction.
#
# The standard graphify pipeline works best when each input file contains one coherent
# concept. Technical books and textbooks are structured as chapters, so graphify tends to
# produce chapter-level nodes instead of concept-level nodes. This module solves that by
# splitting each document into chapter/section chunks that can then be sent to an LLM for
# concept extraction, producing one concept-card .md file per concept.
#
# Typical workflow:
#   graphify preprocess ./books --output ./concepts
#   # (AI dispatches subagents to extract concept cards from each chunk)
#   /graphify ./concepts
#
from __future__ import annotations

import re
import unicodedata
from pathlib import Path


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_CHAPTER_RE = re.compile(
    r"^(?:chapter|章|第\s*\d+\s*[章节]|section|part|§|\d+\.)[\s\d]",
    re.IGNORECASE,
)

_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+)$", re.MULTILINE)


def _safe_filename(text: str, max_len: int = 60) -> str:
    """Convert arbitrary text into a safe ASCII filename slug."""
    # Normalize unicode (e.g. Chinese → decomposed) then strip non-ASCII
    normalized = unicodedata.normalize("NFKD", text)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^\w\s-]", "", ascii_text).strip()
    slug = re.sub(r"[\s_-]+", "_", slug)
    slug = slug[:max_len].strip("_")
    if not slug:
        # Fall back to hex digest of original text for CJK-only titles
        import hashlib
        slug = hashlib.md5(text.encode()).hexdigest()[:12]
    return slug.lower()


def _clean_title(raw: str) -> str:
    """Strip leading # characters and trim whitespace from a heading."""
    return raw.lstrip("#").strip()


# ---------------------------------------------------------------------------
# Per-format splitters
# ---------------------------------------------------------------------------


def split_markdown(path: Path, max_level: int = 2) -> list[dict]:
    """Split a markdown file into sections by headings up to *max_level*.

    Returns a list of dicts:
      {title, content, source_file, chapter_index}
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    sections: list[dict] = []

    # Find all heading positions
    matches = list(_HEADING_RE.finditer(text))
    if not matches:
        # No headings: treat the whole file as one chunk
        return [
            {
                "title": path.stem,
                "content": text.strip(),
                "source_file": str(path),
                "chapter_index": 0,
            }
        ]

    # Use headings up to max_level as split points
    split_points: list[tuple[int, str]] = []  # (start_pos, title)
    for m in matches:
        level = len(m.group(1))  # number of # chars
        if level <= max_level:
            split_points.append((m.start(), m.group(2).strip()))

    if not split_points:
        # All headings are deeper than max_level - treat whole file as one chunk
        return [
            {
                "title": path.stem,
                "content": text.strip(),
                "source_file": str(path),
                "chapter_index": 0,
            }
        ]

    # Optionally include content before the first heading
    pre = text[: split_points[0][0]].strip()
    idx = 0
    if pre:
        sections.append(
            {
                "title": path.stem,
                "content": pre,
                "source_file": str(path),
                "chapter_index": idx,
            }
        )
        idx += 1

    for i, (pos, title) in enumerate(split_points):
        end = split_points[i + 1][0] if i + 1 < len(split_points) else len(text)
        body = text[pos:end].strip()
        if body:
            sections.append(
                {
                    "title": title,
                    "content": body,
                    "source_file": str(path),
                    "chapter_index": idx,
                }
            )
            idx += 1

    return sections


def split_text(path: Path) -> list[dict]:
    """Split a plain-text file into sections by chapter-like headers.

    Recognises patterns like "Chapter 1", "第一章", "Part II", "§3", "1. Introduction".
    Falls back to splitting by blank-line-separated paragraphs for files without headers.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()

    # Detect chapter-header lines
    header_positions: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped and _CHAPTER_RE.match(stripped):
            header_positions.append((i, stripped))

    if not header_positions:
        # No chapter headers: treat the whole file as one section
        return [
            {
                "title": path.stem,
                "content": text.strip(),
                "source_file": str(path),
                "chapter_index": 0,
            }
        ]

    sections: list[dict] = []
    pre_lines = lines[: header_positions[0][0]]
    if pre_lines and "".join(pre_lines).strip():
        sections.append(
            {
                "title": path.stem,
                "content": "\n".join(pre_lines).strip(),
                "source_file": str(path),
                "chapter_index": 0,
            }
        )

    for i, (line_no, title) in enumerate(header_positions):
        end = header_positions[i + 1][0] if i + 1 < len(header_positions) else len(lines)
        body = "\n".join(lines[line_no:end]).strip()
        if body:
            sections.append(
                {
                    "title": title,
                    "content": body,
                    "source_file": str(path),
                    "chapter_index": len(sections),
                }
            )

    return sections


def split_pdf(path: Path) -> list[dict]:
    """Split a PDF file into page-level chunks using pypdf (if installed).

    Returns one chunk per page. Falls back to a single-chunk stub when pypdf is
    unavailable so the caller can still process the path.
    """
    try:
        from pypdf import PdfReader  # type: ignore[import]
    except ImportError:
        return [
            {
                "title": path.stem,
                "content": f"[PDF file: {path.name}. Install pypdf to split pages.]",
                "source_file": str(path),
                "chapter_index": 0,
            }
        ]

    try:
        reader = PdfReader(str(path))
    except Exception as exc:
        return [
            {
                "title": path.stem,
                "content": f"[Could not read PDF {path.name}: {exc}]",
                "source_file": str(path),
                "chapter_index": 0,
            }
        ]

    sections: list[dict] = []
    for i, page in enumerate(reader.pages):
        try:
            page_text = page.extract_text() or ""
        except Exception:
            page_text = ""
        page_text = page_text.strip()
        if page_text:
            sections.append(
                {
                    "title": f"{path.stem} – page {i + 1}",
                    "content": page_text,
                    "source_file": str(path),
                    "chapter_index": i,
                }
            )

    if not sections:
        sections = [
            {
                "title": path.stem,
                "content": f"[PDF {path.name} contained no extractable text.]",
                "source_file": str(path),
                "chapter_index": 0,
            }
        ]
    return sections


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_SUPPORTED_EXTENSIONS: set[str] = {".md", ".txt", ".rst", ".pdf"}


def split_document(path: Path) -> list[dict]:
    """Split a single document file into chapter/section chunks.

    Dispatches to the appropriate splitter based on file extension.
    Returns a list of chunk dicts (title, content, source_file, chapter_index).
    """
    ext = path.suffix.lower()
    if ext in (".md", ".rst"):
        return split_markdown(path)
    if ext == ".txt":
        return split_text(path)
    if ext == ".pdf":
        return split_pdf(path)
    # Unsupported extension: return as a single chunk so callers see *something*
    try:
        content = path.read_text(encoding="utf-8", errors="replace").strip()
    except Exception:
        content = f"[Could not read {path.name}]"
    return [
        {
            "title": path.stem,
            "content": content,
            "source_file": str(path),
            "chapter_index": 0,
        }
    ]


def split_all(doc_dir: Path) -> list[dict]:
    """Recursively split all supported documents under *doc_dir*.

    Returns a flat list of all chunks across all files, sorted by
    (source_file, chapter_index).
    """
    doc_dir = Path(doc_dir)
    chunks: list[dict] = []
    for ext in sorted(_SUPPORTED_EXTENSIONS):
        for path in sorted(doc_dir.rglob(f"*{ext}")):
            chunks.extend(split_document(path))
    chunks.sort(key=lambda c: (c["source_file"], c["chapter_index"]))
    return chunks


def make_concept_filename(concept_name: str, index: int | None = None) -> str:
    """Return a safe .md filename for a concept card.

    >>> make_concept_filename("Attention Mechanism")
    'attention_mechanism.md'
    >>> make_concept_filename("注意力机制", 3)
    '...md'
    """
    slug = _safe_filename(concept_name)
    if index is not None:
        return f"{slug}_{index:04d}.md"
    return f"{slug}.md"


def write_concept_card(
    concept_name: str,
    definition: str,
    key_points: list[str],
    relations: list[str],
    source: str,
    output_dir: Path,
    index: int | None = None,
) -> Path:
    """Write a single concept card to *output_dir* as a Markdown file.

    The card uses the structured format that graphify's semantic extractor
    understands best: YAML frontmatter plus a plain-language body.

    Returns the path of the written file.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    filename = make_concept_filename(concept_name, index)
    out_path = output_dir / filename

    # Avoid overwriting an existing card with the same name
    counter = 1
    while out_path.exists() and counter < 1000:
        stem = Path(filename).stem
        out_path = output_dir / f"{stem}_{counter}.md"
        counter += 1

    points_block = "\n".join(f"- {p}" for p in key_points)
    relations_block = "\n".join(f"- {r}" for r in relations)

    content = f"""---
concept: "{concept_name}"
source: "{source}"
type: concept_card
---

# {concept_name}

**Definition:** {definition}

## Key Points

{points_block}

## Relations to Other Concepts

{relations_block}

## Source

{source}
"""
    out_path.write_text(content, encoding="utf-8")
    return out_path


# ---------------------------------------------------------------------------
# Concept-card parser
# ---------------------------------------------------------------------------

_CONCEPT_BLOCK_RE = re.compile(
    r"---CONCEPT:\s*(?P<name>[^\n-]+?)---\n(?P<body>.*?)---END---",
    re.DOTALL,
)

_FIELD_RE = {
    "definition": re.compile(r"(?:定义|Definition)[：:]\s*(.+)", re.IGNORECASE),
    "key_points": re.compile(
        r"(?:核心要点|Key Points?)[：:]\s*\n((?:[ \t]*[-•*·].+\n?)*)",
        re.IGNORECASE,
    ),
    "relations": re.compile(
        r"(?:与其他概念的关系|Relations?)[：:]\s*\n((?:[ \t]*[-•*·].+\n?)*)",
        re.IGNORECASE,
    ),
    "source": re.compile(r"(?:来源|Source)[：:]\s*(.+)", re.IGNORECASE),
}


def _parse_list_block(raw: str) -> list[str]:
    """Extract bullet-list items from a raw text block."""
    items = []
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith(("-", "•", "*", "·")):
            items.append(line.lstrip("-•*· ").strip())
        elif line and not line.endswith(":"):
            items.append(line)
    return [i for i in items if i]


def parse_concept_cards(llm_output: str) -> list[dict]:
    """Parse an LLM response into a list of concept-card dicts.

    Each dict has keys: name, definition, key_points, relations, source.

    The expected format (as instructed in the preprocess prompt) is::

        ---CONCEPT: Attention Mechanism---
        定义：...
        核心要点：
        - ...
        与其他概念的关系：
        - ...
        来源：...
        ---END---

    Any card that cannot be parsed is silently skipped (an empty name or
    missing definition results in no entry).
    """
    cards: list[dict] = []
    for match in _CONCEPT_BLOCK_RE.finditer(llm_output):
        name = match.group("name").strip()
        body = match.group("body")
        if not name:
            continue

        def_m = _FIELD_RE["definition"].search(body)
        definition = def_m.group(1).strip() if def_m else ""

        kp_m = _FIELD_RE["key_points"].search(body)
        key_points = _parse_list_block(kp_m.group(1)) if kp_m else []

        rel_m = _FIELD_RE["relations"].search(body)
        relations = _parse_list_block(rel_m.group(1)) if rel_m else []

        src_m = _FIELD_RE["source"].search(body)
        source = src_m.group(1).strip() if src_m else ""

        if not definition and not key_points:
            continue

        cards.append(
            {
                "name": name,
                "definition": definition,
                "key_points": key_points,
                "relations": relations,
                "source": source,
            }
        )
    return cards


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

CONCEPT_EXTRACTION_PROMPT = """\
You are a knowledge engineer. Extract all independent "concept units" from the \
following technical document section.

Requirements:
- Each concept unit should be a distinct technical concept, algorithm, data structure, \
principle, or method.
- Do NOT keep chapter titles, preambles, or transition sentences.
- Use standard academic terminology for concept names so that the same concept from \
different books is named consistently and can be merged in the knowledge graph.
- Output ONLY the concept blocks below — no introduction, no commentary.

Format each concept exactly like this:

---CONCEPT: <Concept Name>---
定义：<one-sentence definition>
核心要点：
- <point 1>
- <point 2>
- <point 3>
与其他概念的关系：
- <A depends on B>
- <A is a special case of C>
- <A and D solve the same class of problem>
来源：{source}
---END---

Document section title: {title}

Document section content:
{content}
"""


def build_extraction_prompt(chunk: dict) -> str:
    """Return the concept-extraction prompt for a document chunk."""
    return CONCEPT_EXTRACTION_PROMPT.format(
        source=chunk.get("source_file", "unknown"),
        title=chunk.get("title", ""),
        content=chunk.get("content", ""),
    )


# ---------------------------------------------------------------------------
# CLI entry point (used by __main__.py)
# ---------------------------------------------------------------------------


def preprocess_dir(
    doc_dir: Path,
    output_dir: Path,
    *,
    verbose: bool = True,
) -> list[dict]:
    """Split all documents under *doc_dir* and print a summary of chunks found.

    This is the deterministic first half of the preprocess pipeline. The LLM
    concept-extraction step is performed by AI subagents following the
    instructions in skill.md.

    Returns the list of chunk dicts so that the AI can reference them.
    The chunk list is also saved to *output_dir*/.graphify_chunks.json.
    """
    import json

    doc_dir = Path(doc_dir)
    output_dir = Path(output_dir)

    if not doc_dir.exists():
        raise FileNotFoundError(f"preprocess: directory not found: {doc_dir}")

    chunks = split_all(doc_dir)

    if not chunks:
        if verbose:
            print(f"preprocess: no supported files found in {doc_dir}")
            print(f"  Supported extensions: {', '.join(sorted(_SUPPORTED_EXTENSIONS))}")
        return []

    # Save chunk manifest for AI subagents
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / ".graphify_chunks.json"
    manifest_path.write_text(json.dumps(chunks, indent=2, ensure_ascii=False), encoding="utf-8")

    if verbose:
        # Group by source file for the summary
        by_file: dict[str, int] = {}
        for c in chunks:
            by_file[c["source_file"]] = by_file.get(c["source_file"], 0) + 1

        print(f"\nPreprocess: {len(chunks)} sections from {len(by_file)} file(s)")
        for src, count in sorted(by_file.items()):
            rel = Path(src).name
            print(f"  {rel}: {count} section(s)")
        print(f"\nChunk manifest saved to: {manifest_path}")
        print(
            f"\nNext step: the AI will dispatch subagents to extract concept cards "
            f"from each section and write them to {output_dir}/"
        )
        print(
            "After concept extraction completes, run:\n"
            f"  /graphify {output_dir}"
        )

    return chunks
