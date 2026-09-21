"""Context selection: which retrieved documents and which history reach the model.

Pure functions over plain data so they are testable without Azure. The rules
are deliberately simple (phase 2C): keep the ranker's order, drop unusable
chunks, group chunks by document, bound the text size.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.integrations.azure_search import KbDocument
from app.schemas.chat import HistoryMessage

__all__ = ["ContextDocument", "HistoryMessage", "select_documents", "select_history"]


@dataclass(frozen=True)
class ContextDocument:
    """One citable document: the retrieved chunks of one URL, joined, under one number."""

    n: int  # 1-based citation number as shown to the model
    title: str
    url: str
    content: str
    release_date: str | None
    chunks: int = 1


# The index is chunked (many hits per document). Chunks of one URL are
# presented under one number so a citation always means a document; more
# than this many chunks of the same document add little.
CHUNKS_PER_DOCUMENT = 3
CHUNK_SEPARATOR = "\n[…]\n"


@dataclass
class _Group:
    title: str
    url: str
    release_date: str | None
    parts: list[str]
    chars: int = 0


def select_documents(
    documents: list[KbDocument], max_documents: int, max_chars_per_document: int, max_total_chars: int
) -> list[ContextDocument]:
    """Citable documents in ranker order (first chunk decides the order), numbered from 1.

    Chunks without content or URL are dropped. Chunks of the same URL are
    grouped, at most ``CHUNKS_PER_DOCUMENT`` per document, each document
    bounded by ``max_chars_per_document`` and all of them together by
    ``max_total_chars``; at most ``max_documents`` distinct documents.
    """
    groups: dict[str, _Group] = {}
    total = 0
    for doc in documents:
        content = (doc.content or "").strip()
        url = (doc.url or "").strip()
        if not content or not url:
            continue
        group = groups.get(url)
        if group is None:
            if len(groups) >= max_documents:
                continue
            group = _Group(
                title=(doc.title or "").strip() or url, url=url, release_date=doc.release_date, parts=[]
            )
            groups[url] = group
        if len(group.parts) >= CHUNKS_PER_DOCUMENT:
            continue
        allowed = min(max_chars_per_document - group.chars, max_total_chars - total)
        if allowed <= 0:
            continue
        part = content[:allowed]
        group.parts.append(part)
        group.chars += len(part)
        total += len(part)
    return [
        ContextDocument(
            n=i,
            title=g.title,
            url=g.url,
            content=CHUNK_SEPARATOR.join(g.parts),
            release_date=g.release_date,
            chunks=len(g.parts),
        )
        for i, g in enumerate((g for g in groups.values() if g.parts), start=1)
    ]


def select_history(history: list[HistoryMessage], max_messages: int, max_chars: int) -> list[HistoryMessage]:
    """The newest ``max_messages`` messages in chronological order, each truncated.

    Only user/assistant turns are kept; anything else stored in the session
    (legacy ``system`` rows) is not conversation.
    """
    if max_messages <= 0:
        return []
    turns = [m for m in history if m.role in ("user", "assistant") and m.content.strip()]
    return [HistoryMessage(role=m.role, content=m.content[:max_chars]) for m in turns[-max_messages:]]
