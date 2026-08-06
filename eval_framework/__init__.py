"""Portable evaluation framework — human + LLM-as-a-judge Pass/Fail scoring.

This package is intentionally self-contained: it must never import from
`agent/` or `src/`. The host app is reached only through a Target Adapter
(HTTP in this project). See `new_features_history/PRD-evals.md`.
"""
