"""Split Markdown into ~500-token chunks that remember their heading path and line range.

Blocks (paragraphs, lists, fenced code, headings) are never split unless a single block is
bigger than a whole chunk. A heading starts a new chunk once the current one has some
substance, so chunks tend to follow the note's sections. Consecutive chunks within a long
section share ~50 tokens of overlap so a sentence cut at a boundary is still findable.
"""

import re
from dataclasses import dataclass, field

CHARS_PER_TOKEN = 4  # rough average for English text with nomic-embed-text's WordPiece tokenizer
TARGET_TOKENS = 500
OVERLAP_TOKENS = 50
MIN_SECTION_TOKENS = 100  # a heading only starts a new chunk once the current one is this big

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_FRONTMATTER_TITLE = re.compile(r"""^title:\s*["']?(.*?)["']?\s*$""", re.IGNORECASE)


def estimate_tokens(text: str) -> int:
    return -(-len(text) // CHARS_PER_TOKEN)  # ceil


@dataclass
class Chunk:
    index: int
    content: str
    start_line: int  # 1-based, inclusive, in the original file
    end_line: int
    heading_path: list[str] = field(default_factory=list)

    @property
    def token_estimate(self) -> int:
        return estimate_tokens(self.content)


@dataclass
class ParsedMarkdown:
    title: str | None
    chunks: list[Chunk]


@dataclass
class _Block:
    text: str
    start: int
    end: int
    heading_path: list[str]
    is_heading: bool = False
    is_overlap: bool = False

    @property
    def tokens(self) -> int:
        return estimate_tokens(self.text)


def _split_frontmatter(lines: list[str]) -> tuple[int, str | None]:
    """Return (index of first body line, frontmatter title)."""
    if not lines or lines[0].strip() != "---":
        return 0, None
    for i in range(1, len(lines)):
        if lines[i].strip() in ("---", "..."):
            title = None
            for fm_line in lines[1:i]:
                if m := _FRONTMATTER_TITLE.match(fm_line.strip()):
                    title = m.group(1).strip() or None
            return i + 1, title
    return 0, None  # unterminated: treat as ordinary text


def _blocks(lines: list[str], start: int) -> tuple[list[_Block], str | None]:
    blocks: list[_Block] = []
    stack: list[tuple[int, str]] = []
    first_h1: str | None = None
    buf: list[str] = []
    buf_start = 0
    in_fence = False

    def path() -> list[str]:
        return [t for _, t in stack]

    def flush(end_line: int) -> None:
        nonlocal buf
        if buf and any(x.strip() for x in buf):
            blocks.append(_Block("\n".join(buf).strip("\n"), buf_start, end_line, path()))
        buf = []

    for i in range(start, len(lines)):
        line, n = lines[i], i + 1
        if _FENCE.match(line):
            if not buf:
                buf_start = n
            buf.append(line)
            in_fence = not in_fence
            continue
        if in_fence:
            buf.append(line)
            continue
        if m := _HEADING.match(line):
            flush(n - 1)
            level, heading = len(m.group(1)), m.group(2).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, heading))
            if level == 1 and first_h1 is None:
                first_h1 = heading
            blocks.append(_Block(line.strip(), n, n, path(), is_heading=True))
            continue
        if not line.strip():
            flush(n - 1)
            continue
        if not buf:
            buf_start = n
        buf.append(line)
    flush(len(lines))
    return blocks, first_h1


def _split_oversized(block: _Block, max_chars: int) -> list[_Block]:
    """Break a block bigger than one chunk on line boundaries, hard-wrapping very long lines."""
    pieces: list[_Block] = []
    cur: list[str] = []
    cur_start = block.start
    for offset, line in enumerate(block.text.split("\n")):
        n = block.start + offset
        while len(line) > max_chars:  # one enormous line (minified JSON, base64, ...)
            if cur:
                pieces.append(_Block("\n".join(cur), cur_start, n - 1, block.heading_path))
                cur = []
            pieces.append(_Block(line[:max_chars], n, n, block.heading_path))
            line = line[max_chars:]
        if cur and len("\n".join(cur)) + 1 + len(line) > max_chars:
            pieces.append(_Block("\n".join(cur), cur_start, n - 1, block.heading_path))
            cur = []
        if not cur:
            cur_start = n
        cur.append(line)
    if cur and any(x.strip() for x in cur):
        pieces.append(_Block("\n".join(cur), cur_start, block.end, block.heading_path))
    return pieces


def _overlap_tail(block: _Block) -> _Block | None:
    """The last ~OVERLAP_TOKENS of a block, cut at a word boundary."""
    max_chars = OVERLAP_TOKENS * CHARS_PER_TOKEN
    text = block.text
    if len(text) > max_chars:
        text = text[-max_chars:]
        cut = re.search(r"\s", text)
        text = text[cut.end():] if cut else text
    text = text.strip()
    if not text:
        return None
    start = block.end - text.count("\n")
    return _Block(text, start, block.end, block.heading_path, is_overlap=True)


def chunk_markdown(text: str) -> ParsedMarkdown:
    lines = text.splitlines()
    body_start, fm_title = _split_frontmatter(lines)
    raw_blocks, first_h1 = _blocks(lines, body_start)

    max_chars = TARGET_TOKENS * CHARS_PER_TOKEN
    blocks: list[_Block] = []
    for b in raw_blocks:
        blocks.extend(_split_oversized(b, max_chars) if b.tokens > TARGET_TOKENS else [b])

    chunks: list[Chunk] = []
    cur: list[_Block] = []

    def emit(keep_overlap: bool) -> None:
        nonlocal cur
        if not any(not b.is_overlap for b in cur):
            cur = []  # only carried-over overlap left: nothing new to emit
            return
        # A chunk belongs to the section of its first new block (overlap may come from before).
        owner = next(b for b in cur if not b.is_overlap)
        chunks.append(
            Chunk(
                index=len(chunks),
                content="\n\n".join(b.text for b in cur),
                start_line=cur[0].start,
                end_line=cur[-1].end,
                heading_path=owner.heading_path,
            )
        )
        tail = _overlap_tail(cur[-1]) if keep_overlap and not cur[-1].is_heading else None
        cur = [tail] if tail else []

    for b in blocks:
        cur_tokens = sum(x.tokens for x in cur)
        if b.is_heading and sum(x.tokens for x in cur if not x.is_overlap) >= MIN_SECTION_TOKENS:
            emit(keep_overlap=False)  # new section: no overlap across section boundaries
        elif cur and cur_tokens + b.tokens > TARGET_TOKENS:
            emit(keep_overlap=not b.is_heading)
        cur.append(b)
    emit(keep_overlap=False)

    return ParsedMarkdown(title=fm_title or first_h1, chunks=chunks)
