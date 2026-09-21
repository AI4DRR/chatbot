"""Online (web) retrieval.

- ``retrieval``        orchestrator: intent → sources → parallel fetch → score →
                       top-k → ``format_results_for_llm`` (legacy online_retrieval.py)
- ``fetchers``         Wikipedia, DuckDuckGo, World Bank, USGS, NOAA, ReliefWeb
                       (legacy source_fetchers.py)
- ``trusted_sources``  whitelist / neutral / blocklist scoring (legacy trusted_sources.py)
- ``foundry``          Azure AI Foundry "grounding with Bing" path used when
                       configured (legacy azure_foundry_retrieval.py)
"""
