"""Target-agent and judge-LLM adapters.

The host app is reached only through these adapters — never by importing
`agent/` or `src/`. Porting the framework = writing a new adapter file.
"""
