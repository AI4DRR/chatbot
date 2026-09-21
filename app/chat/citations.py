"""Resolve ``[n]`` citations in the model's answer to the excerpts they point at.

The model cites excerpts by the numbers ``prompts.format_excerpts`` assigned.
The answer returned to the client is renumbered by first appearance so that
``[k]`` in the text is ``sources[k-1]`` in the response, citations to numbers
that were never offered are removed, and ``sources`` lists cited documents
only (the phase 2C contract).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.chat.context import ContextDocument
from app.schemas.chat import SourceItem

# ``[2]``, ``[2, 5]`` and ``[2][5]`` are all accepted; each group is rewritten
# as one bracket per citation.
_CITATION_GROUP = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")


@dataclass(frozen=True)
class ResolvedAnswer:
    """The renumbered answer text and the documents it cites, in citation order."""

    text: str
    cited: list[ContextDocument]


def resolve_citations(text: str, documents: list[ContextDocument]) -> ResolvedAnswer:
    """Renumber valid citations by first appearance, drop invalid ones, list the cited documents."""
    by_n = {d.n: d for d in documents}
    order: dict[int, int] = {}  # original n -> new k

    def replace(match: re.Match[str]) -> str:
        out = []
        for raw in match.group(1).split(","):
            n = int(raw)
            if n not in by_n:
                continue
            if n not in order:
                order[n] = len(order) + 1
            out.append(f"[{order[n]}]")
        return "".join(out)

    renumbered = _CITATION_GROUP.sub(replace, text)
    # Removing a dangling citation can leave a space before punctuation.
    renumbered = re.sub(r" +([.,;:!?])", r"\1", renumbered).strip()
    cited = [by_n[n] for n in order]
    return ResolvedAnswer(text=renumbered, cited=cited)


def sources_for(cited: list[ContextDocument]) -> list[SourceItem]:
    """``SourceItem`` list in citation order (``sources[k-1]`` is ``[k]``)."""
    return [SourceItem(title=d.title, url=d.url, type="KB") for d in cited]
