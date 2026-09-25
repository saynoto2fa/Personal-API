from app.watcher.chunker import CHARS_PER_TOKEN, OVERLAP_TOKENS, TARGET_TOKENS, chunk_markdown


def _para(words: int, word: str = "lorem") -> str:
    return " ".join([word] * words)


def test_title_from_frontmatter_then_h1():
    doc = "---\ntitle: From Frontmatter\ntags: [a]\n---\n# Heading One\n\nBody."
    assert chunk_markdown(doc).title == "From Frontmatter"
    assert chunk_markdown("# Heading One\n\nBody.").title == "Heading One"
    assert chunk_markdown("Just text.").title is None


def test_frontmatter_excluded_and_line_numbers_are_original():
    doc = "---\ntitle: T\n---\n\n# Intro\n\nFirst paragraph."
    (chunk,) = chunk_markdown(doc).chunks
    assert "title:" not in chunk.content
    assert chunk.start_line == 5  # the '# Intro' line
    assert chunk.end_line == 7
    assert chunk.heading_path == ["Intro"]


def test_empty_and_whitespace_docs_have_no_chunks():
    assert chunk_markdown("").chunks == []
    assert chunk_markdown("\n\n   \n").chunks == []
    assert chunk_markdown("---\ntitle: only frontmatter\n---\n").chunks == []


def test_h1_h2_always_split_even_when_short():
    body = _para(150)  # ~190 tokens, above MIN_SECTION_TOKENS
    doc = f"# A\n\n{body}\n\n## B\n\n{body}\n\n# C\n\n{body}"
    chunks = chunk_markdown(doc).chunks
    assert [c.heading_path for c in chunks] == [["A"], ["A", "B"], ["C"]]
    assert chunks[1].content.startswith("## B")
    assert [c.index for c in chunks] == [0, 1, 2]

    small = "# A\n\nshort.\n\n## B\n\nalso short."
    chunks = chunk_markdown(small).chunks
    assert [(c.heading_path, c.content) for c in chunks] == [(["A"], "# A\n\nshort."), (["A", "B"], "## B\n\nalso short.")]


def test_short_entries_in_a_troubleshooting_doc_keep_their_own_labels():
    # The bug this fixes: a short "## One" merged into "## Two" and the hit was labeled "One".
    doc = (
        "# Troubleshooting\n\nIntro.\n\n"
        "## Slow first search\n\nOllama unloads the model after 5 minutes.\n\n"
        f"## Tools missing in Claude Desktop\n\n{_para(120, 'config')}\n\n"
        "## Tiny\n\nOne line."
    )
    chunks = chunk_markdown(doc).chunks
    assert [c.heading_path[-1] for c in chunks] == ["Troubleshooting", "Slow first search", "Tools missing in Claude Desktop", "Tiny"]
    desktop = chunks[2]
    assert desktop.content.startswith("## Tools missing") and "Ollama" not in desktop.content


def test_title_line_joins_first_section_and_takes_its_label():
    chunks = chunk_markdown("# Guide\n\n## Setup\n\nInstall it.\n\n## Use\n\nRun it.").chunks
    assert [c.heading_path for c in chunks] == [["Guide", "Setup"], ["Guide", "Use"]]
    assert chunks[0].content == "# Guide\n\n## Setup\n\nInstall it."  # no heading-only chunk


def test_short_subsections_merge_and_are_labeled_by_the_dominant_one():
    doc = f"# G\n\n## Setup\n\n### Prereqs\n\nPython.\n\n### Install\n\n{_para(30, 'install')}\n\n### Check\n\nRun it."
    (chunk,) = chunk_markdown(doc).chunks  # H3 sections under 100 tokens merge together
    assert chunk.heading_path == ["G", "Setup", "Install"]  # the section with most of the text
    assert "### Prereqs" in chunk.content and "### Check" in chunk.content
    assert chunk.start_line == 1

    big = _para(150, "big")
    chunks = chunk_markdown(f"## S\n\n### One\n\n{big}\n\n### Two\n\nsmall").chunks
    assert [c.heading_path for c in chunks] == [["S", "One"], ["S", "Two"]]  # substantial H3 still splits


def test_long_section_splits_near_target_with_overlap():
    paragraphs = [_para(86, f"word{i:02d}") for i in range(12)]  # 86 * 7 chars ~ 150 tokens each
    chunks = chunk_markdown("# Long\n\n" + "\n\n".join(paragraphs)).chunks
    assert len(chunks) > 3
    for c in chunks:
        assert c.token_estimate <= TARGET_TOKENS + OVERLAP_TOKENS + 5
        assert c.heading_path == ["Long"]
    for prev, nxt in zip(chunks, chunks[1:]):
        tail = prev.content[-OVERLAP_TOKENS * CHARS_PER_TOKEN // 2 :].split()[-1]
        assert tail in nxt.content  # the next chunk starts with the end of the previous one
        assert nxt.start_line <= prev.end_line


def test_no_overlap_across_sections():
    body = _para(150)
    chunks = chunk_markdown(f"# A\n\n{body} ENDA\n\n# B\n\n{body}").chunks
    assert "ENDA" not in chunks[1].content


def test_fenced_code_is_one_block_and_hash_lines_inside_are_not_headings():
    doc = "# Setup\n\n```bash\n# not a heading\necho hi\n\necho bye\n```\n\nAfter."
    (chunk,) = chunk_markdown(doc).chunks
    assert chunk.heading_path == ["Setup"]
    assert "# not a heading\necho hi\n\necho bye" in chunk.content


def test_oversized_block_and_giant_line_are_split():
    lines = "\n".join(_para(20) for _ in range(200))  # one ~5000-token paragraph
    chunks = chunk_markdown(lines).chunks
    assert len(chunks) >= 8
    assert all(c.token_estimate <= TARGET_TOKENS + OVERLAP_TOKENS + 5 for c in chunks)
    assert chunks[0].start_line == 1 and chunks[-1].end_line == 200

    giant = "x" * (TARGET_TOKENS * CHARS_PER_TOKEN * 3)
    chunks = chunk_markdown(giant).chunks
    assert len(chunks) >= 3
    assert all(c.start_line == 1 and c.end_line == 1 for c in chunks)
