# -*- coding: utf-8 -*-
"""
Kiểm tra hợp đồng của toàn bộ tool MCP.
=======================================
Chạy được trên máy không có ReCap: chỉ nạp server và soi phần khai báo tool.
Đây là lưới an toàn cho việc đổi chữ ký hàm — thứ trực tiếp sinh ra JSON schema
mà AI nhìn thấy, và là thứ duy nhất AI dựa vào để quyết định gọi tool nào.

Một con số nhắc lại trong tài liệu KHÔNG được chứng thực bởi việc nó được nhắc
lại: mọi bản sao đều bắt nguồn từ một lần đếm duy nhất, không ai kiểm. Nên số
tool ở đây được suy ra từ ba nguồn độc lập — mã nguồn, server đã nạp, và câu chữ
trong README — rồi bắt cả ba phải khớp nhau.
"""

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SERVER_PY = ROOT / "recap_mcp" / "server.py"

SNAKE_CASE = re.compile(r"^[a-z][a-z0-9_]*$")


def _dem_tool_trong_ma_nguon():
    """Đếm hàm mang decorator `@mcp.tool` bằng AST, không chạy gì cả.

    Dùng AST chứ không dùng grep: một dòng `@mcp.tool` nằm trong chuỗi hay trong
    khối chú thích vẫn khớp biểu thức chính quy, còn AST thì chỉ thấy decorator
    thật. Và cách này chạy được ở nơi không cài nổi `mcp`.
    """
    cay = ast.parse(SERVER_PY.read_text(encoding="utf-8"))
    so = 0
    for node in ast.walk(cay):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for deco in node.decorator_list:
            dich = deco.func if isinstance(deco, ast.Call) else deco
            if isinstance(dich, ast.Attribute) and dich.attr == "tool":
                so += 1
                break
    return so


# Nguồn sự thật cho mọi con số "bao nhiêu tool" trong repo này.
SO_TOOL_TRONG_TAI_LIEU = _dem_tool_trong_ma_nguon()


def test_ma_nguon_co_dang_ky_tool():
    """Nếu cách viết decorator đổi, phép đếm AST ở trên phải hỏng to chứ không
    được âm thầm trả về 0 rồi làm mọi phép so sánh bên dưới thành đúng."""
    assert SO_TOOL_TRONG_TAI_LIEU > 0


def test_server_nap_duoc_dung_so_tool_nhu_ma_nguon(tools):
    """Bắc cầu giữa tĩnh và động: một tool được định nghĩa nhưng đăng ký hỏng
    (trùng tên, decorator gọi sai) sẽ làm hai con số lệch nhau."""
    assert len(tools) == SO_TOOL_TRONG_TAI_LIEU


@pytest.mark.parametrize("ten_file", ["README.md", "README.vi.md"])
def test_so_tool_trong_readme_khop_voi_ma_nguon(ten_file):
    """README nêu số tool ở tiêu đề mục. Thêm một tool mà quên sửa câu đó thì
    test này đỏ — đó là toàn bộ lý do nó tồn tại.

    Hai README song ngữ đều bị kiểm: bản dịch bao giờ cũng là bản trôi trước,
    vì người sửa mã nguồn thường chỉ mở đúng một trong hai file.
    """
    van_ban = (ROOT / ten_file).read_text(encoding="utf-8")
    so = [int(n) for n in re.findall(r"(?:tool|Tools?)\s*\((\d+)\)", van_ban)]
    assert so, "%s không nêu số tool ở dạng 'tool (N)'" % ten_file
    assert set(so) == {SO_TOOL_TRONG_TAI_LIEU}, (
        "%s ghi %s tool, mã nguồn có %d" % (ten_file, so, SO_TOOL_TRONG_TAI_LIEU)
    )


@pytest.mark.parametrize("ten_file", ["README.md", "README.vi.md"])
def test_moi_tool_deu_duoc_liet_ke_trong_readme(ten_file, tools):
    """Một tool không có trong bảng tài liệu là một tool không ai biết để gọi."""
    van_ban = (ROOT / ten_file).read_text(encoding="utf-8")
    thieu = [t.name for t in tools if "`%s`" % t.name not in van_ban]
    assert not thieu, "%s không nhắc tới: %s" % (ten_file, thieu)


def test_ten_tool_khong_trung_va_dung_snake_case(tools):
    names = [t.name for t in tools]
    assert len(names) == len(set(names)), "Có tool bị đặt trùng tên"
    sai = [n for n in names if not SNAKE_CASE.match(n)]
    assert not sai, "Tên tool không đúng snake_case: %s" % sai


def test_moi_tool_deu_co_mo_ta(tools):
    """Không có mô tả thì AI không biết khi nào nên gọi tool."""
    thieu = [t.name for t in tools if not (t.description or "").strip()]
    assert not thieu, "Tool thiếu docstring: %s" % thieu


def test_moi_tool_co_input_schema_kieu_object(tools):
    for tool in tools:
        schema = tool.input_schema
        assert isinstance(schema, dict), tool.name
        assert schema.get("type") == "object", tool.name
        assert "properties" in schema, tool.name


def test_moi_tham_so_bat_buoc_deu_ton_tai_trong_properties(tools):
    """Tham số nằm trong `required` mà không có trong `properties` là schema hỏng."""
    for tool in tools:
        schema = tool.input_schema
        props = set(schema.get("properties", {}))
        thieu = [r for r in schema.get("required", []) if r not in props]
        assert not thieu, "%s: %s" % (tool.name, thieu)


def test_server_khai_bao_instructions_cho_ai(server_module):
    """`instructions` là chỗ duy nhất nói cho AI biết giới hạn `.rcs` trước khi
    nó kịp thiết kế một quy trình dựa trên giả định sai."""
    instructions = server_module.mcp.instructions or ""
    assert ".rcs" in instructions
    assert "check_recap_installation" in instructions
