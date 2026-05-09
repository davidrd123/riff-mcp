"""FastMCP server exposing media analysis tools.

Tools land in subsequent steps per MCP_DESIGN.md sequencing:
- describe_image, score_image     (Step 5)
- describe_video, score_video     (Step 6)
- extract_video_frames            (Step 7)
- compare_images, extract_visual_tokens  (Step 8)

This file is the FastMCP scaffold only.
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP


mcp = FastMCP("media-analysis-mcp")


def main() -> None:
    """Console-script entry point — runs the FastMCP server on stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
