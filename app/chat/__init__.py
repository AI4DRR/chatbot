"""The chat pipeline (retrieval-augmented answer generation).

Legacy ``chat_service.py`` plus the helpers duplicated in ``chat_cli.py``,
split by responsibility:

- ``pipeline``        the boundary: ``ChatTurnInput`` → ``ChatTurnResult`` callables,
                      ``unconfigured_pipeline`` (503) and ``select_pipeline``   (implemented)
- ``stub``            non-AI dev/test pipeline behind ``CHAT_PIPELINE=stub``    (implemented)
- ``cost``            ``estimate_cost`` (legacy step 13)                          (implemented)
- ``rag``             the minimal grounded pipeline behind ``CHAT_PIPELINE=rag``:
                      one search, one completion, ``[n]`` citations (phase 2C)   (implemented)
- ``context``         which retrieved chunks (grouped per document) and which
                      history reach the model                                    (implemented)
- ``prompts``         system prompt and the numbered excerpt block               (implemented)
- ``citations``       resolve ``[n]`` in the answer to cited-only sources         (implemented)
- ``routing``         internal-model query understanding before retrieval:
                      search query + active-subject decision (Phase 2)           (implemented)
- ``memory``          compaction of older messages into the session summary on
                      the internal model (Phase 2)                                (implemented)

Not ported (deliberately, phase 2C keeps one search + one completion):
``query_analysis`` (topic change, follow-up, LIST-vs-SYNTHESIS), ``intent``,
``entities`` (spaCy), ``ranking`` (legacy diversify), online sources.

The pipeline depends on ``app.integrations`` for every external call and on
nothing in ``app.api`` or ``app.db``.
"""
