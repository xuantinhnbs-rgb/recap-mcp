# -*- coding: utf-8 -*-
"""Kiểm thử giao thức: bắt tay MCP qua stdio và gọi thử tool như một client thật."""

import json
import pathlib
import subprocess
import sys

# Console Windows mặc định dùng cp1252, không in được tiếng Việt và sẽ làm script
# chết giữa chừng ngay cả khi phần kiểm thử đã chạy đúng. Ép UTF-8 ngay từ đầu.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

PY = sys.executable
ROOT = pathlib.Path(__file__).resolve().parent.parent
ENTRY = str(ROOT / "recap_server.py")

proc = subprocess.Popen(
    [PY, ENTRY],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    cwd=str(ROOT),
    env={"PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8",
         "PATH": r"C:\Windows\System32", "SYSTEMROOT": r"C:\Windows"},
    text=True, encoding="utf-8",
)


def rpc(method, params=None, msg_id=None):
    message = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        message["params"] = params
    if msg_id is not None:
        message["id"] = msg_id
    proc.stdin.write(json.dumps(message) + "\n")
    proc.stdin.flush()
    if msg_id is None:
        return None
    while True:
        line = proc.stdout.readline()
        if not line:
            raise RuntimeError("Server đóng stdout:\n" + proc.stderr.read())
        data = json.loads(line)
        if data.get("id") == msg_id:
            return data


FAILS = []


def check(label, condition, detail=""):
    if not condition:
        FAILS.append(label)
    print(f"[{'OK  ' if condition else 'FAIL'}] {label} {detail}")


SAMPLE = r"C:\ProgramData\Autodesk\Autodesk ReCap\Sample\AutodeskReCapSampleProject.rcp"

try:
    init = rpc("initialize", {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "1.0"},
    }, 1)
    name = init["result"]["serverInfo"]["name"]
    check("Bắt tay MCP thành công", name == "Autodesk-ReCap-MCP", name)

    instructions = init["result"].get("instructions") or ""
    check("Có instructions cho AI", "ReCap" in instructions)
    check("Instructions nêu rõ giới hạn .rcs",
          ".rcs" in instructions and "KHÔNG đọc được" in instructions,
          "AI phải biết trước là không đọc được điểm từ .rcs")

    rpc("notifications/initialized")

    tools = rpc("tools/list", {}, 2)["result"]["tools"]
    names = sorted(t["name"] for t in tools)
    check("Đăng ký được tool", len(names) >= 20, f"{len(names)} tool")

    expected = [
        "check_recap_installation", "read_project", "list_scans",
        "get_scan_registration", "get_measurements", "get_regions",
        "read_rcs_header", "import_scans", "get_job_status",
        "inspect_point_cloud", "compare_to_bim_mesh", "extract_cross_section",
        "fit_plane_to_region", "compare_point_clouds", "elevation_grid_report",
    ]
    missing = [n for n in expected if n not in names]
    check("Đủ các tool cốt lõi", not missing, f"thiếu: {missing}" if missing else "")

    undocumented = [t["name"] for t in tools if not (t.get("description") or "").strip()]
    check("Mọi tool đều có mô tả", not undocumented, str(undocumented))

    schema_missing = [t["name"] for t in tools if "inputSchema" not in t]
    check("Mọi tool đều có inputSchema", not schema_missing, str(schema_missing))

    # Gọi thật một tool không cần dữ liệu ngoài.
    res = rpc("tools/call", {"name": "check_recap_installation", "arguments": {}}, 3)
    payload = json.loads(res["result"]["content"][0]["text"])
    check("Gọi tool trả về JSON có khoá ok", payload.get("ok") is True, str(payload)[:120])
    check("Nhận diện được bản ReCap đã cài",
          bool(payload.get("version")), payload.get("version"))
    check("Báo đúng là không đọc được điểm từ .rcs",
          payload["capabilities"]["read_rcs_point_data"] is False)

    # Gọi một tool trên dữ liệu thật.
    if pathlib.Path(SAMPLE).is_file():
        res = rpc("tools/call",
                  {"name": "list_scans", "arguments": {"rcp_path": SAMPLE}}, 4)
        payload = json.loads(res["result"]["content"][0]["text"])
        check("Đọc được project mẫu qua giao thức MCP",
              payload.get("ok") and payload.get("scan_count") == 2,
              f"scan_count={payload.get('scan_count')}")
    else:
        print("[SKIP] Không có project mẫu của ReCap trên máy này")

    # Lỗi phải về dạng ok=false, không làm sập server.
    res = rpc("tools/call",
              {"name": "read_project", "arguments": {"rcp_path": r"C:\khong-ton-tai.rcp"}}, 5)
    payload = json.loads(res["result"]["content"][0]["text"])
    check("Đường dẫn sai trả ok=false chứ không sập",
          payload.get("ok") is False and payload.get("error"))

    # Server vẫn sống sau lỗi.
    res = rpc("tools/call", {"name": "list_jobs", "arguments": {}}, 6)
    payload = json.loads(res["result"]["content"][0]["text"])
    check("Server còn phục vụ được sau khi gặp lỗi", payload.get("ok") is True)

finally:
    proc.stdin.close()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()

print()
if FAILS:
    print(f"THẤT BẠI: {len(FAILS)} mục -> {FAILS}")
    sys.exit(1)
print("Tất cả kiểm thử giao thức MCP đều đạt.")
