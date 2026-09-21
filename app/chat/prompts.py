"""Prompt assembly for the grounded answer (legacy ``ChatService._chat``, SYNTHESIS only).

Text only — no network, no settings. The excerpt numbering here is what the
model cites as ``[n]`` and what ``app.chat.citations`` resolves back to
documents, so the two modules must agree on the ``[n]`` convention.
"""

from __future__ import annotations

from app.chat.context import ContextDocument, HistoryMessage

ChatMessage = dict[str, str]

SYSTEM_PROMPT = (
    "You are the AI4DRR Chatbot, an assistant for the United Nations Office for Disaster Risk "
    "Reduction (UNDRR). You answer questions about disaster risk reduction using a curated "
    "knowledge base of UNDRR and PreventionWeb content.\n\n"
    "Rules:\n"
    "- Ground factual statements in the KNOWLEDGE BASE EXCERPTS when they cover the question.\n"
    "- Cite an excerpt inline with its number in square brackets, e.g. [2], immediately after the "
    "statement it supports. Cite only excerpts you actually used; several may be cited together "
    "as [1][3].\n"
    "- Do NOT add a separate Sources or References section and do NOT write out URLs; the "
    "citations are turned into a source list for the reader.\n"
    "- Quantitative figures are allowed only when stated in an excerpt, cited in the same sentence.\n"
    "- Cite only claims the excerpts actually support. If the excerpts cover part of the question, "
    "answer the covered part with citations and say plainly which part is general information rather "
    "than UNDRR knowledge-base content. Never present general knowledge as if it came from the "
    "excerpts.\n"
    "- If the excerpts do not cover the question at all, answer from general knowledge WITHOUT "
    "citations. Do not write a 'not covered' preamble or apology: a notice is added for the reader "
    "automatically whenever an answer cites nothing.\n"
    "- Do not give personal legal, medical, financial or investment advice. Organizational and "
    "policy-level analysis is fine; remind the reader to verify figures with qualified staff.\n"
    "- Questions about the chatbot's deployment, authorship, infrastructure or ownership are outside "
    "your scope; say so briefly without questioning the user.\n"
    "- Answer in the language of the question. Use Markdown; keep answers focused."
)

NO_EXCERPTS_NOTE = (
    "KNOWLEDGE BASE EXCERPTS: none were retrieved for this question. Answer from general "
    "knowledge without citations and without a preamble about the knowledge base."
)

# Prepended by the pipeline (not the model) to every answer that cites nothing:
# a deterministic provenance notice, identical every time (plan §4, Phase 3).
UNSOURCED_NOTICE = "_Not sourced from the UNDRR knowledge base._"


def with_unsourced_notice(text: str) -> str:
    """The answer with the provenance notice as its first line."""
    return f"{UNSOURCED_NOTICE}\n\n{text}"


def format_excerpts(documents: list[ContextDocument]) -> str:
    """The excerpt block: ``[n] title (release_date)`` followed by the text."""
    parts = []
    for d in documents:
        dated = f" (released {d.release_date})" if d.release_date else ""
        parts.append(f"[{d.n}] {d.title}{dated}\n{d.content}")
    return "KNOWLEDGE BASE EXCERPTS (cite by number):\n\n" + "\n\n".join(parts)


MEMORY_NOTE = "CONVERSATION SO FAR (context only, not a source of facts; do not mention it):\n"


def build_messages(
    documents: list[ContextDocument],
    history: list[HistoryMessage],
    question: str,
    memory_summary: str = "",
) -> list[ChatMessage]:
    """System prompt, excerpts, the conversation summary (if any), recent raw history, then the question."""
    messages: list[ChatMessage] = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.append(
        {"role": "system", "content": format_excerpts(documents) if documents else NO_EXCERPTS_NOTE}
    )
    if memory_summary:
        messages.append({"role": "system", "content": MEMORY_NOTE + memory_summary})
    messages.extend({"role": m.role, "content": m.content} for m in history)
    messages.append({"role": "user", "content": question})
    return messages
