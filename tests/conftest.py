# -*- coding: utf-8 -*-
"""
Tiện ích chung cho test.
========================
Cho phép chạy `pytest` từ bất kỳ thư mục nào mà vẫn import được gói `recap_mcp`,
và cung cấp một fixture nạp sẵn server để các nhóm test hợp đồng dùng chung.

Không test nào trong thư mục này cần ReCap ĐANG CHẠY: server kết nối lười, nên
việc đăng ký tool không chạm tới `decap.exe` hay tới license. Vài mục cần bản
cài ReCap có mặt trên đĩa thì tự bỏ qua khi không có — xem `test_mcp.py`.
"""

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))


@pytest.fixture(scope="session")
def server_module():
    """Module `recap_mcp/server.py` đã nạp xong."""
    from recap_mcp import server

    return server


@pytest.fixture(scope="session")
def tools(server_module):
    """Danh sách tool đã đăng ký (`list_tools` là coroutine)."""
    return asyncio.run(server_module.mcp.list_tools())
