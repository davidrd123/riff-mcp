"""FastMCP server exposing gemini-video-prompts as MCP tools.

Tools are added incrementally per MCP_DESIGN.md sequencing:
- generate_image  (Step 2) — Gemini image generation, wraps generate_image_job
- generate_video  (Step 3) — Seedance via Replicate, new adapter

This file is the FastMCP scaffold only; tool implementations land in
subsequent commits.
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP


mcp = FastMCP("gemini-prompts-mcp")


def main() -> None:
    """Console-script entry point — runs the FastMCP server on stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
