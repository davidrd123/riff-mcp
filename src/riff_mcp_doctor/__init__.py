"""riff-mcp doctor — environment + dependency readiness diagnostic.

Run via the console script ``riff-mcp-doctor`` to verify that both MCP
servers (``gemini-prompts-mcp``, ``media-analysis-mcp``) and the batch CLI
(``gemini-video-prompts``) have everything they need: env vars, importable
Python packages, and binaries on PATH.
"""
