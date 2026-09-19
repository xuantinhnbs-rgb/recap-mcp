# -*- coding: utf-8 -*-
"""
Điểm khởi chạy Autodesk ReCap MCP Server.
=========================================
Dùng file này trong cấu hình MCP (Claude Desktop / Claude Code / VS Code):

    "command": "<đường dẫn python.exe>",
    "args": ["<thư mục dự án>/recap-mcp/recap_server.py"],
    "cwd": "<thư mục dự án>/recap-mcp"

Biến môi trường tuỳ chọn:
    RECAP_HOME          thư mục cài ReCap, nếu không nằm ở vị trí mặc định
    RECAP_MCP_LOG_DIR   nơi ghi log của các job decap.exe chạy nền
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from recap_mcp.server import main  # noqa: E402

if __name__ == "__main__":
    main()
