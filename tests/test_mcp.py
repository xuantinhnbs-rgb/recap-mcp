# -*- coding: utf-8 -*-
"""
Kiểm thử giao thức: bắt tay MCP qua stdio và gọi thử tool như một client thật.
==============================================================================
Đây là nhóm test duy nhất chạy server trong một TIẾN TRÌNH RIÊNG và nói chuyện
với nó bằng đúng những dòng JSON-RPC mà Claude gửi. Các nhóm test còn lại import
thẳng module, nên chúng không bắt được lỗi ở lớp vỏ: server không khởi động nổi,
`instructions` bị rỗng, hay một tool ném ngoại lệ ra tới tận giao thức.

Vài mục cần ReCap được cài trên máy; những mục đó tự bỏ qua khi không có, để bộ
test vẫn chạy trọn vẹn trên CI. Trước đây file này là một script chạy ở mức
module: pytest nạp nó lúc thu thập test, toàn bộ phép thử chạy trong im lặng và
đóng góp đúng 0 test — còn trên máy không có ReCap thì `sys.exit(1)` của nó giết
luôn cả phiên pytest trước khi test nào kịp chạy.
"""

import json
import pathlib
import subprocess
import sys

import pytest

PY = sys.executable
ROOT = pathlib.Path(__file__).resolve().parent.parent
ENTRY = str(ROOT / "recap_server.py")

# Project mẫu đi kèm bản cài ReCap. Không có thì các mục dùng nó tự bỏ qua.
SAMPLE = pathlib.Path(
    r"C:\ProgramData\Autodesk\Autodesk ReCap\Sample\AutodeskReCapSampleProject.rcp"
)

chi_khi_co_recap = pytest.mark.skipif(
    not SAMPLE.is_file(), reason="Máy này không có project mẫu của ReCap"
)


class Client:
    """Một client MCP tối giản nói chuyện với server qua stdio."""

    def __init__(self):
        self.proc = subprocess.Popen(
            [PY, ENTRY],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(ROOT),
            # Môi trường tối thiểu: server không được ngầm phụ thuộc vào biến
            # môi trường nào của máy lập trình viên ngoài hai biến dưới đây.
            env={
                "PYTHONUNBUFFERED": "1",
                "PYTHONIOENCODING": "utf-8",
                "PATH": r"C:\Windows\System32",
                "SYSTEMROOT": r"C:\Windows",
            },
            text=True,
            encoding="utf-8",
        )
        self._id = 0

    def rpc(self, method, params=None, expect_reply=True):
        self._id += 1
        msg_id = self._id if expect_reply else None
        message = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        if msg_id is not None:
            message["id"] = msg_id
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()
        if msg_id is None:
            return None
        while True:
            line = self.proc.stdout.readline()
            if not line:
                raise RuntimeError("Server đóng stdout:\n" + self.proc.stderr.read())
            data = json.loads(line)
            if data.get("id") == msg_id:
                return data

    def call_tool(self, name, **arguments):
        """Gọi một tool và trả về payload JSON đã giải mã."""
        res = self.rpc("tools/call", {"name": name, "arguments": arguments})
        return json.loads(res["result"]["content"][0]["text"])

    def close(self):
        self.proc.stdin.close()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()


@pytest.fixture(scope="module")
def client():
    """Một phiên server dùng chung cho cả module — khởi động mất vài giây."""
    c = Client()
    try:
        c.init = c.rpc(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "1.0"},
            },
        )
        c.rpc("notifications/initialized", expect_reply=False)
        yield c
    finally:
        c.close()


@pytest.fixture(scope="module")
def tools(client):
    return client.rpc("tools/list", {})["result"]["tools"]


# --------------------------------------------------------------------------
# Bắt tay
# --------------------------------------------------------------------------


def test_bat_tay_mcp_thanh_cong(client):
    assert client.init["result"]["serverInfo"]["name"] == "Autodesk-ReCap-MCP"


def test_co_instructions_cho_ai(client):
    assert "ReCap" in (client.init["result"].get("instructions") or "")


def test_instructions_neu_ro_gioi_han_rcs(client):
    """AI phải biết TRƯỚC là không đọc được toạ độ điểm từ `.rcs`; biết muộn thì
    nó đã thiết kế xong một quy trình nghiên cứu không chạy được."""
    instructions = client.init["result"].get("instructions") or ""
    assert ".rcs" in instructions
    assert "KHÔNG đọc được" in instructions


# --------------------------------------------------------------------------
# Hợp đồng của tool, nhìn từ phía dây dẫn
# --------------------------------------------------------------------------


def test_dang_ky_du_tool(tools):
    from test_tool_contracts import SO_TOOL_TRONG_TAI_LIEU

    assert len(tools) == SO_TOOL_TRONG_TAI_LIEU


def test_du_cac_tool_cot_loi(tools):
    """Danh sách này là bề mặt mà tài liệu và quy trình nghiên cứu dựa vào; đổi
    tên một trong số chúng là phá vỡ hợp đồng với người dùng, không phải refactor."""
    names = {t["name"] for t in tools}
    expected = {
        "check_recap_installation", "read_project", "list_scans",
        "get_scan_registration", "get_measurements", "get_regions",
        "read_rcs_header", "import_scans", "get_job_status",
        "inspect_point_cloud", "compare_to_bim_mesh", "extract_cross_section",
        "fit_plane_to_region", "compare_point_clouds", "elevation_grid_report",
    }
    assert not (expected - names)


def test_moi_tool_deu_co_mo_ta(tools):
    assert not [t["name"] for t in tools if not (t.get("description") or "").strip()]


def test_moi_tool_deu_co_input_schema(tools):
    assert not [t["name"] for t in tools if "inputSchema" not in t]


# --------------------------------------------------------------------------
# Gọi thật, qua đúng đường dây của giao thức
# --------------------------------------------------------------------------


def test_goi_tool_tra_ve_json_co_khoa_ok(client):
    assert client.call_tool("check_recap_installation").get("ok") is True


def test_bao_dung_la_khong_doc_duoc_diem_tu_rcs(client):
    payload = client.call_tool("check_recap_installation")
    assert payload["capabilities"]["read_rcs_point_data"] is False


@chi_khi_co_recap
def test_nhan_dien_duoc_ban_recap_da_cai(client):
    assert client.call_tool("check_recap_installation").get("version")


@chi_khi_co_recap
def test_doc_duoc_project_mau_qua_giao_thuc(client):
    payload = client.call_tool("list_scans", rcp_path=str(SAMPLE))
    assert payload.get("ok")
    assert payload.get("scan_count") == 2


def test_duong_dan_sai_tra_ok_false_chu_khong_sap(client):
    payload = client.call_tool("read_project", rcp_path=r"C:\khong-ton-tai.rcp")
    assert payload.get("ok") is False
    assert payload.get("error")


def test_server_con_phuc_vu_duoc_sau_khi_gap_loi(client):
    """Chạy SAU mục trên và dùng chung tiến trình: một tool ném ngoại lệ không
    được phép làm hỏng phiên cho mọi lời gọi tiếp theo."""
    client.call_tool("read_project", rcp_path=r"C:\khong-ton-tai.rcp")
    assert client.call_tool("list_jobs").get("ok") is True
