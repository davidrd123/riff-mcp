"""media-analysis-mcp — MCP server for image and video analysis.

Exposes typed MCP tools wrapping Gemini multimodal (describe / score / compare /
token-extract) plus an ffmpeg-based frame extraction utility. Companion to
``gemini-prompts-mcp``: that server generates media, this one analyzes it.

See MCP_DESIGN.md §MCP #2 at the repo root for the architecture and tool
surface.
"""

__version__ = "0.1.0"
