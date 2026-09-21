"""UNDRR (AI4DRR) Chatbot backend.

Package layout (see docs/architecture.md):

- ``app.main``          application factory, lifespan, middleware, routers
- ``app.config``        settings read once from the environment
- ``app.log``           logging configuration
- ``app.errors``        application exceptions and their HTTP mapping
- ``app.api``           HTTP layer: routes and FastAPI dependencies
- ``app.schemas``       request / response models (the public API contract)
- ``app.db``            PostgreSQL access: connection handling and per-table queries
- ``app.services``      session / memory logic that sits between routes and the DB
- ``app.chat``          the RAG pipeline: query analysis, intent, ranking, answer generation
- ``app.integrations``  clients for external systems (Azure OpenAI, Azure AI Search, online sources)
"""
