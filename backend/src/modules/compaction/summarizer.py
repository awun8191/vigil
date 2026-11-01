"""Tier 2: the Fast LLM summarizer for context compaction.

Tier 0 locks the units that must stay. Tier 1 keeps, stubs, or drops the rest.
Tier 2 turns the dropped units and the stubbed units into one short block.

The block has three sections: what happened, what is decided, and what is
still open. The block obeys a token budget. The budget is enforced after the
model answers, so a long answer cannot break it.

Tier 2 never receives a locked unit. A user message, an approval, or a
decision in the input raises an error.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from src.integrations.repositories import LLMClientProtocol
from src.modules.compaction.tiers import TokenCounter, UnitKind

# Default budget for the summary block, in provider tokens.
DEFAULT_SUMMARY_TOKEN_BUDGET = 700

# Character limit for one source in the Tier 2 prompt. A dropped log can be
# large, and the prompt must stay bounded.
SOURCE_CHARS_LIMIT = 4000

# Character limit for the whole Tier 2 prompt. The newest sources win when the
# removed units do not fit, because a recent turn matters more.
MAX_PROMPT_CHARS = 48000

SUMMARY_TITLE = "== Compaction Summary =="
TRUNCATION_MARK = " [...]"
EMPTY_SECTION_TEXT = "None recorded."

# Tier 2 is the Fast model tier from settings.
SUMMARY_TIER = "fast"

HEADING_WHAT_HAPPENED = "What happened"
HEADING_DECISIONS = "Decisions"
HEADING_OPEN_ITEMS = "Open items"
SECTION_HEADINGS: tuple[str, ...] = (
    HEADING_WHAT_HAPPENED,
    HEADING_DECISIONS,
    HEADING_OPEN_ITEMS,
)

# Prefixes that a model heading may use for each canonical section.
_HEADING_PREFIXES: dict[str, tuple[str, ...]] = {
    HEADING_WHAT_HAPPENED: ("what happened", "summary", "context", "history"),
    HEADING_DECISIONS: ("decisions", "decision", "decided"),
    HEADING_OPEN_ITEMS: ("open items", "open item", "still open", "remaining", "pending"),
}

_HEADING_SLACK = 16

# Kinds that Tier 0 locks. Tier 2 must never summarize them.
_LOCKED_KINDS = frozenset({UnitKind.USER, UnitKind.APPROVAL, UnitKind.DECISION})

SUMMARIZER_SYSTEM_PROMPT = (
    "Role: compaction summarizer.\n"
    "Task: replace removed history with one short block.\n"
    "Answer with exactly these three headings, in this order:\n"
    "What happened:\n"
    "Decisions:\n"
    "Open items:\n"
    "Rules:\n"
    "1. Write one or two sentences in each section.\n"
    "2. State only facts from the removed history.\n"
    "3. Do not add advice or a new plan.\n"
    "4. Write plain text. Do not use markdown.\n"
)


class SummarizerError(RuntimeError):
    """Raised when Tier 2 cannot build the summary block."""


class SummaryInputError(ValueError):
    """Raised when Tier 2 receives a unit that Tier 0 locks."""


@dataclass(frozen=True)
class SummarySource:
    """One removed unit that Tier 2 reads.

    `content` is the original text. A stubbed unit sends its full text to the
    model, not the stub, because the stub holds only a preview.
    """

    unit_id: str
    kind: UnitKind
    role: str
    content: str
    tokens: int
    verdict: str


@dataclass(frozen=True)
class SummarySection:
    """One named section of the summary block."""

    heading: str
    body: str


@dataclass(frozen=True)
class SummaryBlock:
    """Bounded summary of the removed units.

    The block always holds the three sections in a fixed order. A section with
    no text holds `EMPTY_SECTION_TEXT`, so the heading is never absent.
    """

    text: str
    sections: tuple[SummarySection, ...]
    tokens: int
    budget_tokens: int
    model: str
    source_unit_ids: tuple[str, ...]
    source_tokens: int
    truncated: bool

    def section(self, heading: str) -> str:
        """Return the body of one section, or an empty string."""
        for section in self.sections:
            if section.heading == heading:
                return section.body
        return ""

    @property
    def happened(self) -> str:
        """What happened before the compaction."""
        return self.section(HEADING_WHAT_HAPPENED)

    @property
    def decisions(self) -> str:
        """The decisions that the history records."""
        return self.section(HEADING_DECISIONS)

    @property
    def open_items(self) -> str:
        """The work that is still open."""
        return self.section(HEADING_OPEN_ITEMS)

    @property
    def fits_budget(self) -> bool:
        """True when the block is inside its token budget."""
        return self.tokens <= self.budget_tokens


def normalize_text(text: str) -> str:
    """Collapse whitespace runs to one space."""
    return " ".join(text.split())


def clean_heading(line: str) -> str:
    """Return a comparison form of one line, without markdown marks."""
    return line.strip().strip("#*_-: ").strip().lower()


def match_heading(cleaned: str) -> str | None:
    """Return the canonical heading name for one cleaned line, or None."""
    for heading, prefixes in _HEADING_PREFIXES.items():
        for prefix in prefixes:
            if cleaned.startswith(prefix) and len(cleaned) <= len(prefix) + _HEADING_SLACK:
                return heading
    return None


def heading_remainder(line: str) -> str:
    """Return the text after the colon on a heading line, or an empty string."""
    if ":" not in line:
        return ""
    return line.split(":", 1)[1].strip()


def parse_sections(answer: str) -> tuple[SummarySection, ...]:
    """Split a model answer into the three canonical sections.

    A missing section holds `EMPTY_SECTION_TEXT`. The order is always the
    canonical order, whatever order the model used.
    """
    bodies: dict[str, list[str]] = {heading: [] for heading in SECTION_HEADINGS}
    current: str | None = None

    for line in (answer or "").splitlines():
        heading = match_heading(clean_heading(line))
        if heading is not None:
            current = heading
            rest = heading_remainder(line)
            if rest:
                bodies[current].append(rest)
            continue
        if current is not None:
            bodies[current].append(line)

    sections: list[SummarySection] = []
    for heading in SECTION_HEADINGS:
        body = normalize_text(" ".join(bodies[heading]))
        sections.append(SummarySection(heading=heading, body=body or EMPTY_SECTION_TEXT))
    return tuple(sections)


def render_block(sections: Sequence[SummarySection], title: str = SUMMARY_TITLE) -> str:
    """Render the sections as the text of the summary block."""
    lines: list[str] = [title]
    for section in sections:
        lines.append(f"{section.heading}:")
        lines.append(section.body)
    return "\n".join(lines)


def truncate_to_tokens(
    text: str,
    counter: TokenCounter,
    max_tokens: int,
    mark: str = TRUNCATION_MARK,
) -> str:
    """Return the longest prefix of `text` that fits `max_tokens`.

    The result ends with `mark` when the text is too long. The counter is the
    single measure, so the result never exceeds the budget.
    """
    if max_tokens <= 0:
        return ""
    if counter.count(text) <= max_tokens:
        return text

    mark_tokens = counter.count(mark)
    allowance = max(0, max_tokens - mark_tokens)
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if counter.count(text[:middle]) <= allowance:
            low = middle
        else:
            high = middle - 1
    return text[:low].rstrip() + mark


def fit_sections(
    sections: Sequence[SummarySection],
    counter: TokenCounter,
    budget: int,
) -> tuple[tuple[SummarySection, ...], bool]:
    """Trim the section bodies until the rendered block fits `budget`.

    The body budget is shared by the three sections in proportion to their
    size, so one long section cannot empty the other two. Returns the fitted
    sections and the truncated flag.
    """
    bodies = [section.body for section in sections]

    def render(bodies_now: Sequence[str]) -> str:
        return render_block(
            [
                SummarySection(section.heading, body)
                for section, body in zip(sections, bodies_now)
            ]
        )

    if counter.count(render(bodies)) <= budget:
        return tuple(sections), False

    overhead = counter.count(
        render_block([SummarySection(section.heading, "") for section in sections])
    )
    body_budget = max(0, budget - overhead)
    truncated = False

    for _ in range(8):
        counts = [counter.count(body) for body in bodies]
        total = sum(counts)
        if total == 0:
            break
        for index, count in enumerate(counts):
            share = int(body_budget * count / total)
            if counter.count(bodies[index]) > share:
                bodies[index] = truncate_to_tokens(bodies[index], counter, share)
                truncated = True
        rendered = render(bodies)
        total_now = counter.count(rendered)
        if total_now <= budget:
            break
        body_budget = max(0, body_budget - (total_now - budget) - 1)

    fitted = tuple(
        SummarySection(section.heading, body) for section, body in zip(sections, bodies)
    )
    return fitted, truncated


def bound_source(content: str, limit: int = SOURCE_CHARS_LIMIT) -> str:
    """Return at most `limit` characters of one source, with a marker."""
    if len(content) <= limit:
        return content
    head = limit // 2
    tail = limit - head
    omitted = len(content) - limit
    return f"{content[:head]}\n[... {omitted} characters omitted ...]\n{content[-tail:]}"


def build_prompt(
    sources: Sequence[SummarySource],
    limit: int = SOURCE_CHARS_LIMIT,
    max_chars: int = MAX_PROMPT_CHARS,
) -> str:
    """Build the Tier 2 user message from the removed units.

    Each source is bounded. When the whole prompt passes `max_chars`, the
    oldest sources leave the prompt and one marker line says how many.
    """
    blocks = [
        f"[{source.verdict}] {source.kind.value} {source.unit_id} "
        f"({source.tokens} tokens)\n{bound_source(source.content, limit)}"
        for source in sources
    ]

    selected = blocks
    omitted = 0
    while selected and len("\n".join(selected)) > max_chars:
        selected = selected[1:]
        omitted += 1

    lines = ["Removed history follows."]
    if omitted:
        lines.append(f"[... {omitted} earlier removed units omitted from this prompt ...]")
    lines.extend(selected)
    return "\n".join(lines)


class CompactionSummarizer:
    """Tier 2 summarizer. It calls the Fast LLM tier once per compaction."""

    def __init__(
        self,
        llm: LLMClientProtocol | None,
        model: str,
        max_tokens: int = DEFAULT_SUMMARY_TOKEN_BUDGET,
        token_counter: TokenCounter | None = None,
    ) -> None:
        if max_tokens <= 0:
            raise ValueError("max_tokens must be positive")
        if not model or not model.strip():
            raise ValueError("A model name is required for the summary tokenizer")
        self.llm = llm
        self.model = model
        self.max_tokens = max_tokens
        self._token_counter = token_counter

    @property
    def token_counter(self) -> TokenCounter:
        """Tokenizer of the Fast model, built on first use."""
        if self._token_counter is None:
            self._token_counter = TokenCounter(self.model)
        return self._token_counter

    def reject_locked_sources(self, sources: Sequence[SummarySource]) -> None:
        """Raise when the input holds a unit that Tier 0 locks."""
        for source in sources:
            if source.kind in _LOCKED_KINDS:
                raise SummaryInputError(
                    f"Tier 2 must not summarize a {source.kind.value} unit: {source.unit_id}"
                )

    async def summarize(self, sources: Sequence[SummarySource]) -> SummaryBlock | None:
        """Return the summary block for the removed units.

        Returns None when the input is empty, because there is nothing to
        summarize. Raises `SummarizerError` when the Fast tier is absent.
        """
        items = tuple(sources)
        if not items:
            return None

        self.reject_locked_sources(items)
        if self.llm is None:
            raise SummarizerError(
                "Tier 2 needs the Fast LLM client. Configure FAST_LLM_API_KEY and pass fast_llm."
            )
        overhead = self.token_counter.count(render_block(parse_sections("")))
        if self.max_tokens < overhead:
            raise SummarizerError(
                f"The summary budget of {self.max_tokens} tokens cannot hold the "
                f"three headings. The minimum is {overhead} tokens."
            )

        response: Any = await self.llm.complete(
            messages=[
                {"role": "system", "content": SUMMARIZER_SYSTEM_PROMPT},
                {"role": "user", "content": build_prompt(items)},
            ],
            tools=None,
            temperature=0.0,
            tier=SUMMARY_TIER,
            max_tokens=self.max_tokens,
        )
        answer = getattr(response, "content", None)
        sections, truncated = fit_sections(
            parse_sections("" if answer is None else str(answer)),
            self.token_counter,
            self.max_tokens,
        )
        text = render_block(sections)

        return SummaryBlock(
            text=text,
            sections=sections,
            tokens=self.token_counter.count(text),
            budget_tokens=self.max_tokens,
            model=self.model,
            source_unit_ids=tuple(source.unit_id for source in items),
            source_tokens=sum(source.tokens for source in items),
            truncated=truncated,
        )
