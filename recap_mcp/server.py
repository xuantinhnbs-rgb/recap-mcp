# -*- coding: utf-8 -*-
"""
Autodesk ReCap MCP Server
=========================
Giao tiếp chuẩn Model Context Protocol (MCP) để AI làm việc với Autodesk ReCap
và dữ liệu quét thực địa, phục vụ nghiên cứu Scan-to-BIM.

ReCap KHÔNG có COM/.NET API như AutoCAD hay Navisworks. Server này đi ba đường
khác nhau, và biết rõ giới hạn của từng đường:

  1. Đọc project — file ``.rcp`` là ZIP chứa XML manifest, đọc trực tiếp.
     Cho ra: danh sách scan, ma trận đăng ký, CHẤT LƯỢNG đăng ký, phép đo 3D,
     phân vùng, hệ toạ độ, hộp bao.
  2. Xử lý headless — ``decap.exe`` đi kèm bản cài, chạy nền, không mở giao diện.
     Cho ra: import scan, giảm mật độ, cắt khoảng cách, gộp scan, đổi hệ toạ độ.
  3. Phân tích điểm — đọc file LAS/LAZ GỐC bằng laspy.
     Dữ liệu điểm trong ``.rcs`` là octree đóng của Autodesk, không giải mã được,
     nên mọi tính toán định lượng phải dựa trên file quét gốc giữ song song.

Hợp đồng trả về: MỌI tool đều trả về một object JSON có khóa "ok".
  * Thành công -> {"ok": true, ...dữ liệu...}
  * Thất bại   -> {"ok": false, "error": "<thông điệp tiếng Việt nói rõ cách khắc phục>"}
Không tool nào ném ngoại lệ ra ngoài.

Chạy bằng:  python -m recap_mcp.server
"""

from __future__ import annotations

import functools
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
from mcp.server.mcpserver import MCPServer

from . import decap as dc
from . import pointcloud as pc
from . import rcp as rc
from .rcp import RecapError

mcp = MCPServer(
    "Autodesk-ReCap-MCP",
    instructions=(
        "Làm việc với Autodesk ReCap và dữ liệu quét thực địa trên máy này: đọc project "
        ".rcp (scan, ma trận và chất lượng đăng ký, phép đo, phân vùng), chạy decap.exe "
        "headless để import/giảm mật độ/đổi hệ toạ độ, và phân tích point cloud LAS/LAZ "
        "cho nghiên cứu Scan-to-BIM.\n"
        "Gọi check_recap_installation trước tiên để biết bản ReCap nào đang có và license "
        "còn hiệu lực không.\n"
        "GIỚI HẠN QUAN TRỌNG: dữ liệu điểm trong file .rcs là octree đóng, KHÔNG đọc được "
        "toạ độ từng điểm. Mọi tool phân tích định lượng (mặt cắt, khớp mặt phẳng, so sánh "
        "với BIM) đều nhận đường dẫn file LAS/LAZ GỐC, không nhận .rcs. Hãy giữ song song "
        "hai bộ: ReCap để đăng ký và xem, LAS/LAZ để tính toán.\n"
        "Import point cloud chạy hàng phút tới hàng giờ nên luôn chạy NỀN: import_scans trả "
        "về job_id ngay, hỏi tiến độ bằng get_job_status. decap.exe có thể trả mã 0 mà không "
        "tạo ra file — get_job_status đã kiểm tra sự tồn tại của file đích, hãy đọc khoá "
        "output_exists chứ đừng tin mỗi returncode.\n"
        "Đơn vị: mọi toạ độ giữ nguyên đơn vị của file nguồn (LAS/LAZ hạ tầng giao thông "
        "thường là mét). Riêng cờ decimation của decap.exe tính bằng MILIMÉT."
    ),
)


# ============================================================================
# Khung an toàn
# ============================================================================

def _jsonable(value: Any) -> Any:
    """Đổi kiểu numpy sang kiểu Python thuần — numpy không tự tuần tự hoá sang JSON."""
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return [_jsonable(v) for v in value.tolist()]
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
        return None                     # NaN/Inf không hợp lệ trong JSON
    return value


def safe(fn):
    """Bọc một tool: không bao giờ ném lỗi, luôn trả về dict có khóa 'ok'."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs) -> dict:
        try:
            result = fn(*args, **kwargs)
        except RecapError as exc:
            return {"ok": False, "error": str(exc)}
        except FileNotFoundError as exc:
            return {"ok": False, "error": f"Không tìm thấy file: {exc}"}
        except PermissionError as exc:
            return {"ok": False, "error": f"Không có quyền truy cập: {exc}. "
                                          "File có thể đang mở trong ReCap hoặc chương trình khác."}
        except MemoryError:
            return {"ok": False, "error": "Hết bộ nhớ. Hãy thu hẹp bbox, tăng every_nth, "
                                          "hoặc giảm max_points."}
        except (ValueError, TypeError, KeyError, IndexError) as exc:
            return {"ok": False, "error": f"Tham số không hợp lệ: {type(exc).__name__}: {exc}"}
        except Exception as exc:                     # lưới an toàn cuối cùng
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

        if isinstance(result, dict):
            out = result if "ok" in result else {"ok": True, **result}
        elif isinstance(result, list):
            out = {"ok": True, "count": len(result), "items": result}
        else:
            out = {"ok": True, "result": result}
        return _jsonable(out)

    return wrapper


def _resolve_out(path: Optional[str], default_name: str) -> Path:
    """Đường dẫn ra mặc định nằm cạnh file nguồn, không rải rác vào thư mục tạm."""
    if path:
        return Path(path)
    return Path.cwd() / default_name


# ============================================================================
# Môi trường & license
# ============================================================================

@mcp.tool()
@safe
def check_recap_installation() -> dict:
    """
    Kiểm tra Autodesk ReCap đã cài chưa và tìm các executable điều khiển được.

    Luôn gọi tool này trước tiên. Nó trả về đường dẫn decap.exe (engine headless),
    ReCap.exe (giao diện), phiên bản, và trạng thái license.
    """
    info = dc.find_recap_install()
    if not info["found"]:
        return {
            "ok": False,
            "error": (
                "Không tìm thấy Autodesk ReCap trên máy này. Nếu đã cài ở vị trí khác "
                "thường lệ, đặt biến môi trường RECAP_HOME trỏ tới thư mục chứa decap.exe."
            ),
            "searched": info.get("searched", []),
        }
    try:
        lic = dc.run_sync(["--checkLicense"], timeout=90)
        license_ok = lic["returncode"] == 0 and "OK" in lic["output"].upper()
        license_detail = lic["messages"] or lic["output"][-300:]
    except RecapError as exc:
        license_ok, license_detail = False, str(exc)

    return {
        "install_dir": info["install_dir"],
        "decap_exe": info["decap_exe"],
        "recap_exe": info["recap_exe"],
        "version": info["version"],
        "license_ok": license_ok,
        "license_detail": license_detail,
        "capabilities": {
            "headless_import": True,
            "read_rcp_project": True,
            "read_rcs_point_data": False,
            "point_analysis_from_las": pc.laspy is not None,
            "mesh_comparison": pc.cKDTree is not None,
        },
        "note": (
            "read_rcs_point_data=false là giới hạn của định dạng, không phải lỗi cấu hình: "
            "octree trong .rcs là định dạng đóng. Dùng file LAS/LAZ gốc cho phân tích điểm."
        ),
    }


@mcp.tool()
@safe
def open_project_in_recap(rcp_path: str) -> dict:
    """
    Mở một project .rcp bằng giao diện ReCap để xem trực quan.

    ReCap không có API điều khiển giao diện, nên tool này chỉ khởi chạy ứng dụng —
    sau đó không đọc hay điều khiển được gì thêm từ cửa sổ đang mở.
    """
    p = Path(rcp_path)
    if not p.is_file():
        raise RecapError(f"Không tìm thấy project: {p}")
    info = dc.find_recap_install()
    if not info["found"] or not info["recap_exe"]:
        raise RecapError("Không tìm thấy ReCap.exe để mở giao diện.")
    subprocess.Popen([info["recap_exe"], str(p.resolve())])
    return {
        "launched": info["recap_exe"],
        "project": str(p.resolve()),
        "note": "Giao diện ReCap không điều khiển được bằng script sau khi mở.",
    }


# ============================================================================
# Đọc project ReCap
# ============================================================================

@mcp.tool()
@safe
def find_projects(folder: str, recursive: bool = True, limit: int = 200) -> dict:
    """Quét một thư mục tìm các file project .rcp và scan .rcs."""
    root = Path(folder)
    if not root.is_dir():
        raise RecapError(f"Không tìm thấy thư mục: {root}")
    pattern = "**/*" if recursive else "*"
    projects, scans = [], []
    for f in root.glob(pattern):
        if not f.is_file():
            continue
        ext = f.suffix.lower()
        if ext == ".rcp" and len(projects) < limit:
            projects.append({"file": str(f), "size_bytes": f.stat().st_size,
                             "modified": f.stat().st_mtime})
        elif ext == ".rcs" and len(scans) < limit:
            scans.append({"file": str(f), "size_bytes": f.stat().st_size})
    return {"folder": str(root), "projects": projects, "project_count": len(projects),
            "loose_scans": scans, "loose_scan_count": len(scans)}


@mcp.tool()
@safe
def read_project(rcp_path: str, include_geometry: bool = True) -> dict:
    """
    Đọc toàn bộ manifest của một project .rcp.

    Trả về metadata, đơn vị, hệ toạ độ, hộp bao, danh sách scan kèm ma trận đăng ký,
    và (khi include_geometry=true) các phép đo, phân vùng, limit box, view state.

    Đặt include_geometry=false nếu chỉ cần phần tóm tắt và danh sách scan.
    """
    return rc.parse_rcp(rcp_path, include_geometry=include_geometry)


@mcp.tool()
@safe
def list_scans(rcp_path: str, include_rcs_header: bool = False) -> dict:
    """
    Liệt kê các trạm quét trong project, kèm số điểm và thuộc tính sẵn có.

    include_rcs_header=true sẽ đọc thêm header từng file .rcs để lấy hộp bao thật
    của dữ liệu (chậm hơn một chút, nhưng cho biết phạm vi thực của từng trạm).
    """
    data = rc.parse_rcp(rcp_path, include_geometry=False)
    scans = []
    for s in data["scans"]:
        item = {
            "name": s["name"], "id": s["id"], "group": s["group"],
            "num_points": s["num_points"],
            "has_rgb": s["has_rgb"], "has_normals": s["has_normals"],
            "has_intensity": s["has_intensity"], "is_lidar": s["is_lidar"],
            "registered": s["registration"].get("is_registered"),
            "registration_level": s["registration"].get("level"),
            "rcs_path": s["rcs_path"], "rcs_exists": s["rcs_exists"],
            "rcs_size_bytes": s["rcs_size_bytes"],
        }
        if include_rcs_header and s["rcs_exists"]:
            try:
                h = rc.read_rcs_header(s["rcs_path"])
                item["data_bounds"] = h["data_bounds"]
                item["header_num_points"] = h["num_points"]
                if h["num_points"] != s["num_points"]:
                    item["point_count_mismatch"] = (
                        f"manifest báo {s['num_points']}, header .rcs báo {h['num_points']}"
                    )
            except RecapError as exc:
                item["rcs_header_error"] = str(exc)
        scans.append(item)

    missing = [s["name"] for s in data["scans"] if not s["rcs_exists"]]
    out = {
        "project": data["project_file"],
        "scan_count": data["scan_count"],
        "registered_scan_count": data["registered_scan_count"],
        "total_points": data["total_points"],
        "bounds": data["bounds"],
        "scans": scans,
    }
    if missing:
        out["warning"] = (
            f"{len(missing)} scan thiếu file .rcs: {', '.join(missing[:5])}"
            f"{'...' if len(missing) > 5 else ''}. Thư mục '<tên project> Support' có thể "
            "chưa được chép cùng file .rcp."
        )
    return out


@mcp.tool()
@safe
def get_scan_registration(rcp_path: str, scan_name: Optional[str] = None) -> dict:
    """
    Lấy ma trận đăng ký và CHẤT LƯỢNG đăng ký của từng trạm quét.

    Đây là dữ liệu định lượng quan trọng nhất khi đánh giá độ tin cậy của một
    mô hình quét: 'level' (ví dụ FINE_ALIGNED) cho biết mức khớp mà ReCap đạt
    được, 'status' cho biết ReCap có báo vấn đề không, 'matrix4x4' là phép biến
    đổi từ hệ cục bộ của trạm về hệ project.

    Bỏ trống scan_name để lấy tất cả các trạm.
    """
    data = rc.parse_rcp(rcp_path, include_geometry=False)
    scans = data["scans"]
    if scan_name:
        scans = [s for s in scans if s["name"] == scan_name]
        if not scans:
            raise RecapError(
                f"Không có trạm quét tên '{scan_name}'. Các trạm có trong project: "
                + ", ".join(s["name"] for s in data["scans"])
            )

    rows = [{
        "name": s["name"],
        "id": s["id"],
        "translation": s["translation"],
        "rotation_euler_deg": s["rotation_euler_deg"],
        **s["registration"],
    } for s in scans]

    levels: Dict[str, int] = {}
    for r in rows:
        levels[r.get("level") or "(trống)"] = levels.get(r.get("level") or "(trống)", 0) + 1
    unregistered = [r["name"] for r in rows if not r.get("is_registered")]

    return {
        "project": data["project_file"],
        "scan_count": len(rows),
        "registrations": rows,
        "level_summary": levels,
        "unregistered_scans": unregistered,
        "note": (
            "ReCap không ghi sai số đăng ký dạng số (RMS) vào manifest — chỉ ghi mức "
            "định tính ('level') và cỡ bin của histogram. Muốn con số RMS cho bài báo "
            "thì phải tự tính bằng compare_point_clouds giữa hai trạm có vùng chồng lấn."
        ),
    }


@mcp.tool()
@safe
def get_measurements(rcp_path: str) -> dict:
    """
    Lấy các phép đo và ghi chú đã tạo trong ReCap, kèm toạ độ 3D.

    Khoảng cách được tính lại từ hai điểm pick chứ không lấy giá trị ReCap hiển thị,
    nên nó luôn nhất quán với toạ độ trả về.
    """
    data = rc.parse_rcp(rcp_path, include_geometry=True)
    items = data["measurements"]
    by_kind: Dict[str, int] = {}
    for m in items:
        by_kind[m["kind"]] = by_kind.get(m["kind"], 0) + 1
    return {
        "project": data["project_file"],
        "count": len(items),
        "by_kind": by_kind,
        "measurements": items,
        "units_hint": data["units"],
    }


@mcp.tool()
@safe
def get_regions(rcp_path: str) -> dict:
    """Lấy các layer phân vùng (region) — kết quả phân loại thủ công trong ReCap."""
    data = rc.parse_rcp(rcp_path, include_geometry=True)
    regions = data["regions"]
    return {
        "project": data["project_file"],
        "count": len(regions),
        "regions": regions,
        "limit_boxes": data["limit_boxes"],
        "note": (
            "Manifest chỉ ghi ĐỊNH NGHĨA vùng (tên, màu, cây phép toán chọn), không ghi "
            "danh sách điểm thuộc vùng. Muốn tách điểm theo vùng thì xuất từng vùng ra "
            "file riêng trong giao diện ReCap rồi phân tích file đó."
        ),
    }


@mcp.tool()
@safe
def read_rcs_header(rcs_path: str) -> dict:
    """
    Đọc header của một file .rcs (dữ liệu một trạm quét).

    Cho ra hộp bao thật của dữ liệu, số điểm chính xác, vị trí và góc đăng ký.
    KHÔNG đọc được toạ độ từng điểm — phần thân file là octree đóng của Autodesk.
    """
    return rc.read_rcs_header(rcs_path)


@mcp.tool()
@safe
def extract_project_preview(rcp_path: str, output_path: str,
                            entry: Optional[str] = None) -> dict:
    """Rút ảnh xem trước nhúng trong file .rcp ra đĩa (dùng làm hình minh hoạ báo cáo)."""
    return rc.extract_preview(rcp_path, output_path, entry)


# ============================================================================
# Xử lý headless bằng decap.exe
# ============================================================================

@mcp.tool()
@safe
def check_recap_license() -> dict:
    """Hỏi decap.exe xem license ReCap trên máy còn hiệu lực không."""
    result = dc.run_sync(["--checkLicense"], timeout=90)
    ok = result["returncode"] == 0 and "OK" in result["output"].upper()
    return {"license_ok": ok, **result}


@mcp.tool()
@safe
def import_scans(
    output_folder: str,
    project_name: str,
    scans: Optional[List[str]] = None,
    control_file: Optional[str] = None,
    decimation_mm: Optional[float] = None,
    min_range: Optional[float] = None,
    max_range: Optional[float] = None,
    unify: bool = False,
    normalize_intensity: Optional[bool] = None,
    input_unit: Optional[str] = None,
    current_coordinate_system: Optional[str] = None,
    target_coordinate_system: Optional[str] = None,
    e57_import_images: bool = False,
    e57_scan_per_image: bool = False,
    e57_match_image_resolution: bool = False,
) -> dict:
    """
    Import các file quét thành một project ReCap mới, chạy NỀN, không mở giao diện.

    Tool trả về ngay một job_id — hỏi tiến độ bằng get_job_status(job_id).
    Import point cloud mất từ vài phút tới vài giờ tuỳ khối lượng.

    Tham số đáng chú ý cho nghiên cứu:
      * decimation_mm — khoảng cách tối thiểu giữa hai điểm, tính bằng MILIMÉT.
        Đặt giá trị này sẽ làm thưa dữ liệu; với bài toán đo sai lệch hình học,
        hãy giữ nguyên (bỏ trống) để không mất độ phân giải.
      * min_range / max_range — loại điểm quá gần hoặc quá xa máy quét, nơi nhiễu
        đo lớn nhất.
      * unify — gộp mọi scan vào một đám mây duy nhất; mất khả năng phân tích
        theo từng trạm, nên chỉ bật khi không cần đánh giá đăng ký.
      * input_unit — đơn vị của dữ liệu gốc: mm, cm, m, in, ft, usft.
      * current/target_coordinate_system — quy đổi hệ toạ độ; phải đặt cả hai.

    Cung cấp ĐÚNG MỘT trong hai: 'scans' (danh sách file) hoặc 'control_file'.
    """
    plan = dc.build_import_args(
        output_folder=output_folder, project_name=project_name, scans=scans,
        control_file=control_file, decimation_mm=decimation_mm, min_range=min_range,
        max_range=max_range, unify=unify, normalize_intensity=normalize_intensity,
        input_unit=input_unit, current_coordinate_system=current_coordinate_system,
        target_coordinate_system=target_coordinate_system,
        e57_import_images=e57_import_images, e57_scan_per_image=e57_scan_per_image,
        e57_match_image_resolution=e57_match_image_resolution,
    )
    job = dc.run_async(plan["args"], kind="import", expected_output=plan["expected_output"])
    return {
        **job,
        "scan_count": plan["scan_count"],
        "input_bytes": plan["input_bytes"],
        "expected_output": plan["expected_output"],
        "warnings": plan["warnings"],
    }


@mcp.tool()
@safe
def get_job_status(job_id: str, tail_lines: int = 25) -> dict:
    """
    Tra tiến độ một job decap.exe đang chạy nền.

    Đọc khoá 'output_exists' chứ đừng chỉ tin 'returncode': decap.exe đã được ghi
    nhận trả mã 0 trong khi không tạo ra file project nào. Khi đó status sẽ là
    'failed' kèm cảnh báo, và 'log_tail' cho biết lý do.
    """
    return dc.job_status(job_id, tail_lines=tail_lines)


@mcp.tool()
@safe
def list_jobs() -> dict:
    """Liệt kê mọi job decap.exe đã khởi chạy trong phiên làm việc này."""
    jobs = dc.list_jobs()
    return {"count": len(jobs), "jobs": jobs}


@mcp.tool()
@safe
def cancel_job(job_id: str) -> dict:
    """Dừng một job decap.exe đang chạy. Project dở dang phải xoá thủ công."""
    return dc.cancel_job(job_id)


# ============================================================================
# Phân tích point cloud LAS/LAZ
# ============================================================================

@mcp.tool()
@safe
def inspect_point_cloud(path: str) -> dict:
    """
    Đọc header file LAS/LAZ: số điểm, hộp bao, hệ toạ độ, các trường dữ liệu có sẵn.

    Không nạp điểm vào bộ nhớ nên chạy tức thì kể cả với file hàng chục GB.
    Luôn gọi tool này trước khi phân tích, để biết phạm vi toạ độ mà đặt bbox.
    """
    return pc.inspect_las(path)


@mcp.tool()
@safe
def sample_points(path: str, count: int = 1000, bbox: Optional[Dict[str, List[float]]] = None,
                  classification: Optional[List[int]] = None, seed: int = 0) -> dict:
    """
    Lấy một mẫu ngẫu nhiên các điểm để xem nhanh dữ liệu trông thế nào.

    bbox dạng {"min": [x,y,z], "max": [x,y,z]}. classification là danh sách mã lớp
    LAS (ví dụ [2] cho mặt đất, [6] cho công trình).
    """
    if count < 1 or count > 100_000:
        raise RecapError("count phải nằm trong khoảng 1..100000.")
    pts, extra, info = pc.load_points(path, bbox=bbox, classification=classification,
                                      max_points=2_000_000, with_extra=True)
    if len(pts) == 0:
        return {"sample": [], "info": info,
                "hint": "Không điểm nào thoả bộ lọc. Kiểm tra lại bbox bằng inspect_point_cloud."}
    rng = np.random.default_rng(seed)
    idx = rng.choice(len(pts), size=min(count, len(pts)), replace=False)
    idx.sort()
    sample = [{"x": round(float(pts[i, 0]), 6), "y": round(float(pts[i, 1]), 6),
               "z": round(float(pts[i, 2]), 6),
               **{k: int(v[i]) for k, v in extra.items()}} for i in idx]
    return {"sample": sample, "returned": len(sample), "info": info,
            "bounds": {"min": pts.min(axis=0).tolist(), "max": pts.max(axis=0).tolist()}}


@mcp.tool()
@safe
def crop_point_cloud(source: str, output: str, bbox: Optional[Dict[str, List[float]]] = None,
                     classification: Optional[List[int]] = None, every_nth: int = 1,
                     max_points: int = 20_000_000) -> dict:
    """
    Cắt một vùng khỏi file LAS/LAZ và ghi ra file mới.

    Dùng để tách riêng một hạng mục công trình (một nhịp cầu, một đoạn tường chắn)
    trước khi phân tích, thay vì xử lý cả tuyến.
    """
    pts, extra, info = pc.load_points(source, bbox=bbox, classification=classification,
                                      every_nth=every_nth, max_points=max_points,
                                      with_extra=True)
    if len(pts) == 0:
        raise RecapError("Không điểm nào thoả bộ lọc — không có gì để ghi. "
                         "Kiểm tra bbox bằng inspect_point_cloud.")
    pc._require_laspy()
    with pc.laspy.open(source) as reader:
        header = reader.header
    result = pc.write_las(output, pts, source_header=header, extra=extra)
    return {**result, "info": info,
            "bounds": {"min": pts.min(axis=0).tolist(), "max": pts.max(axis=0).tolist()}}


@mcp.tool()
@safe
def voxel_downsample(source: str, output: str, voxel_size: float,
                     bbox: Optional[Dict[str, List[float]]] = None,
                     max_points: int = 20_000_000) -> dict:
    """
    Làm thưa đám mây bằng lưới voxel — giữ một điểm đại diện cho mỗi ô lập phương.

    Khác với 'lấy mỗi điểm thứ N': voxel giữ mật độ ĐỀU trong không gian, nên các
    phép khớp hình học sau đó không bị vùng quét dày kéo lệch kết quả.
    """
    if voxel_size <= 0:
        raise RecapError("voxel_size phải lớn hơn 0.")
    pts, _, info = pc.load_points(source, bbox=bbox, max_points=max_points)
    if len(pts) == 0:
        raise RecapError("Không có điểm nào trong phạm vi lọc.")

    keys = np.floor((pts - pts.min(axis=0)) / voxel_size).astype(np.int64)
    _, first = np.unique(keys, axis=0, return_index=True)
    first.sort()
    kept = pts[first]

    pc._require_laspy()
    with pc.laspy.open(source) as reader:
        header = reader.header
    result = pc.write_las(output, kept, source_header=header)
    return {
        **result,
        "input_points": int(len(pts)),
        "output_points": int(len(kept)),
        "reduction_pct": round(100.0 * (1 - len(kept) / len(pts)), 3),
        "voxel_size": voxel_size,
        "info": info,
    }


@mcp.tool()
@safe
def extract_cross_section(
    path: str,
    alignment_start: List[float],
    alignment_end: List[float],
    station: float,
    half_thickness: float = 0.05,
    lateral_reach: Optional[float] = None,
    output_csv: Optional[str] = None,
    max_points: int = 10_000_000,
) -> dict:
    """
    Cắt mặt cắt NGANG vuông góc với tim tuyến tại một lý trình cho trước.

    alignment_start / alignment_end xác định tim tuyến (chỉ dùng hình chiếu bằng để
    tính hướng). 'station' là khoảng cách dọc tim tính từ alignment_start.
    half_thickness là nửa bề dày lát cắt (cùng đơn vị với dữ liệu, thường là mét) —
    0.05 nghĩa là lát dày 10 cm. lateral_reach giới hạn bề rộng lấy về hai bên tim
    (bỏ trống thì lấy bằng chiều dài tuyến, tối thiểu 50) — đặt nhỏ lại để tránh
    nạp thừa điểm khi chỉ cần phần thân công trình.

    Kết quả cho toạ độ 2D trong mặt cắt: 'offset' là khoảng cách ngang so với tim,
    'elevation' là cao độ tương đối — đúng quy ước đọc bản vẽ mặt cắt ngang.
    """
    a = np.asarray(alignment_start, dtype=float)
    b = np.asarray(alignment_end, dtype=float)
    if a.shape != (3,) or b.shape != (3,):
        raise RecapError("alignment_start và alignment_end phải là [x, y, z].")
    direction = b - a
    length = float(np.linalg.norm(direction))
    if length == 0:
        raise RecapError("alignment_start trùng alignment_end — không xác định được hướng tuyến.")
    if station < 0 or station > length:
        raise RecapError(
            f"station={station} nằm ngoài tuyến (dài {round(length, 3)}). "
            "Đặt station trong khoảng 0..chiều dài tuyến."
        )
    direction = direction / length
    origin = a + direction * station

    # Chỉ nạp điểm quanh mặt cắt thay vì cả file. Lát cắt là một đĩa tâm origin,
    # bán kính lateral_reach, dày 2*half_thickness theo phương direction. Hộp bao
    # trục toạ độ CHẶT của đĩa đó có nửa cạnh theo trục i là:
    #     half_thickness*|n_i| + lateral_reach*sqrt(1 - n_i^2)
    reach = float(lateral_reach) if lateral_reach else max(length, 50.0)
    n_abs = np.abs(direction)
    half_extent = half_thickness * n_abs + reach * np.sqrt(np.clip(1.0 - direction ** 2, 0.0, 1.0))
    bbox = {"min": (origin - half_extent).tolist(), "max": (origin + half_extent).tolist()}

    pts, _, info = pc.load_points(path, bbox=bbox, max_points=max_points)
    sec = pc.slab_section(pts, origin.tolist(), direction.tolist(), half_thickness)

    result: Dict[str, Any] = {
        "station": station,
        "alignment_length": round(length, 6),
        "origin": sec["origin"],
        "normal": sec["normal"],
        "half_thickness": half_thickness,
        "points_in_section": sec["selected"],
        "points_scanned": sec["total"],
        "load_info": info,
    }
    if sec["selected"] == 0:
        result["hint"] = (
            "Mặt cắt rỗng. Thường do lý trình nằm ngoài vùng có dữ liệu, hoặc "
            "half_thickness quá nhỏ so với mật độ điểm. Kiểm tra hộp bao bằng "
            "inspect_point_cloud rồi tăng half_thickness."
        )
        return result

    offset, elevation = sec["u"], sec["v"]
    result["offset_range"] = [float(offset.min()), float(offset.max())]
    result["elevation_range"] = [float(sec["points"][:, 2].min()),
                                 float(sec["points"][:, 2].max())]
    result["width"] = round(float(offset.max() - offset.min()), 6)
    result["height"] = round(float(sec["points"][:, 2].max() - sec["points"][:, 2].min()), 6)

    if output_csv:
        rows = ((round(float(offset[i]), 6), round(float(elevation[i]), 6),
                 round(float(sec["points"][i, 0]), 6), round(float(sec["points"][i, 1]), 6),
                 round(float(sec["points"][i, 2]), 6))
                for i in range(sec["selected"]))
        result["csv"] = pc.write_csv(
            output_csv, ["offset", "section_v", "x", "y", "z"], rows)
    else:
        result["hint"] = "Đặt output_csv để ghi toàn bộ điểm mặt cắt ra file cho vẽ biểu đồ."
    return result


@mcp.tool()
@safe
def extract_section_by_plane(
    path: str,
    origin: List[float],
    normal: List[float],
    half_thickness: float = 0.05,
    output_csv: Optional[str] = None,
    bbox: Optional[Dict[str, List[float]]] = None,
    max_points: int = 10_000_000,
) -> dict:
    """
    Cắt một lát mỏng bằng mặt phẳng bất kỳ (tổng quát hơn extract_cross_section).

    Dùng khi cần mặt cắt dọc, mặt cắt bằng, hoặc mặt cắt xiên theo hướng tự chọn.
    normal=[0,0,1] cho mặt cắt bằng ở cao độ origin[2].
    """
    pts, _, info = pc.load_points(path, bbox=bbox, max_points=max_points)
    sec = pc.slab_section(pts, origin, normal, half_thickness)
    out: Dict[str, Any] = {
        "origin": sec["origin"], "normal": sec["normal"],
        "axis_u": sec["axis_u"], "axis_v": sec["axis_v"],
        "half_thickness": half_thickness,
        "points_in_section": sec["selected"], "points_scanned": sec["total"],
        "load_info": info,
    }
    if sec["selected"] == 0:
        out["hint"] = ("Mặt cắt rỗng — kiểm tra origin có nằm trong vùng dữ liệu không, "
                       "hoặc tăng half_thickness.")
        return out
    out["u_range"] = [float(sec["u"].min()), float(sec["u"].max())]
    out["v_range"] = [float(sec["v"].min()), float(sec["v"].max())]
    if output_csv:
        rows = ((round(float(sec["u"][i]), 6), round(float(sec["v"][i]), 6),
                 round(float(sec["points"][i, 0]), 6), round(float(sec["points"][i, 1]), 6),
                 round(float(sec["points"][i, 2]), 6))
                for i in range(sec["selected"]))
        out["csv"] = pc.write_csv(output_csv, ["u", "v", "x", "y", "z"], rows)
    return out


@mcp.tool()
@safe
def fit_plane_to_region(
    path: str,
    bbox: Dict[str, List[float]],
    tolerance: Optional[float] = None,
    classification: Optional[List[int]] = None,
    max_points: int = 5_000_000,
) -> dict:
    """
    Khớp mặt phẳng vào một vùng điểm và đo độ phẳng, độ nghiêng, độ lệch.

    Dùng cho: kiểm tra độ phẳng bản mặt cầu, độ thẳng đứng của tường chắn, độ dốc
    mặt đường. 'tilt_from_vertical_deg' gần 0 nghĩa là mặt đang gần thẳng đứng —
    chỉ số trực tiếp cho tường chắn.

    tolerance (cùng đơn vị dữ liệu) cho ra tỉ lệ phần trăm điểm nằm trong dung sai.
    """
    pts, _, info = pc.load_points(path, bbox=bbox, classification=classification,
                                  max_points=max_points)
    if len(pts) < 3:
        raise RecapError(
            f"Chỉ tìm được {len(pts)} điểm trong bbox — cần ít nhất 3 để khớp mặt phẳng. "
            "Kiểm tra bbox bằng inspect_point_cloud."
        )
    plane = pc.fit_plane(pts)
    residuals = plane.pop("residuals")
    stats = pc.describe(residuals, tolerance=tolerance)

    quality = "phẳng" if plane["planarity_ratio"] < 0.02 else (
        "hơi cong" if plane["planarity_ratio"] < 0.1 else "không phẳng")
    return {
        "plane": plane,
        "deviation": stats,
        "histogram": pc.histogram(residuals, bins=20),
        "planarity_verdict": quality,
        "points_used": int(len(pts)),
        "load_info": info,
        "note": (
            "'deviation' là khoảng cách có dấu từ điểm tới mặt phẳng khớp, theo hướng "
            "pháp tuyến đã chuẩn hoá hướng lên. rms chính là chỉ số độ phẳng dùng trong "
            "báo cáo nghiệm thu."
        ),
    }


@mcp.tool()
@safe
def compare_to_bim_mesh(
    points_path: str,
    mesh_path: str,
    tolerance: Optional[float] = 0.02,
    bbox: Optional[Dict[str, List[float]]] = None,
    classification: Optional[List[int]] = None,
    every_nth: int = 1,
    max_points: int = 3_000_000,
    candidates: int = 8,
    output_csv: Optional[str] = None,
    output_las: Optional[str] = None,
) -> dict:
    """
    SO SÁNH POINT CLOUD VỚI MÔ HÌNH BIM — tool lõi cho nghiên cứu Scan-to-BIM.

    Tính khoảng cách có dấu từ mỗi điểm quét tới bề mặt gần nhất của lưới tam giác
    xuất từ mô hình BIM, rồi tổng hợp thành bộ chỉ số dùng được cho bài báo: mean,
    std, RMS, P95, max, và tỉ lệ phần trăm điểm nằm trong dung sai.

    mesh_path nhận .obj, .stl hoặc .ply — Revit, Navisworks và Forma đều xuất được.
    QUAN TRỌNG: lưới BIM và point cloud phải ở CÙNG hệ toạ độ và cùng đơn vị. Nếu
    kết quả cho sai lệch hàng chục mét thì gần như chắc chắn là lệch hệ toạ độ chứ
    không phải công trình sai.

    Dấu: dương = điểm quét nằm về phía pháp tuyến của mặt BIM, âm = phía ngược lại.
    Hướng pháp tuyến do file lưới quyết định, nên hãy kiểm chứng bằng một vị trí đã
    biết trước khi diễn giải.

    'uncertain_points' là số điểm mà thuật toán KHÔNG chứng minh được là đã tìm đúng
    tam giác gần nhất. Con số này phải rất nhỏ; nếu lớn, hãy tăng 'candidates'.
    """
    verts, faces = pc.load_mesh(mesh_path)
    pts, _, info = pc.load_points(points_path, bbox=bbox, classification=classification,
                                  every_nth=every_nth, max_points=max_points)
    if len(pts) == 0:
        raise RecapError("Không có điểm nào để so sánh. Kiểm tra bbox và bộ lọc.")

    mesh_min, mesh_max = verts.min(axis=0), verts.max(axis=0)
    pts_min, pts_max = pts.min(axis=0), pts.max(axis=0)
    sep = pc.boxes_separation(pts_min, pts_max, mesh_min, mesh_max)

    res = pc.point_to_mesh(pts, verts, faces, k=candidates, signed=True)
    signed = res["signed"]
    stats = pc.describe(signed, tolerance=tolerance)
    uncertain = int(res["uncertain"].sum())

    out: Dict[str, Any] = {
        "points_compared": int(len(pts)),
        "mesh": {
            "file": mesh_path,
            "vertices": int(len(verts)),
            "triangles": int(len(faces)),
            "bounds": {"min": mesh_min.tolist(), "max": mesh_max.tolist()},
            "max_circumradius": res["max_circumradius"],
        },
        "point_cloud_bounds": {"min": pts_min.tolist(), "max": pts_max.tolist()},
        "bounds_overlap": bool(sep["aligned"]),
        "bounds_separation": sep,
        "deviation": stats,
        "histogram": pc.histogram(signed, bins=20),
        "uncertain_points": uncertain,
        "uncertain_pct": round(100.0 * uncertain / len(pts), 4),
        "exhaustive_search": res["exhaustive"],
        "load_info": info,
    }

    if not sep["aligned"]:
        axis = ["X", "Y", "Z"][int(np.argmax(sep["gap_per_axis"]))]
        out["warning"] = (
            f"Point cloud và lưới BIM cách nhau {sep['max_gap']} (lớn nhất theo trục {axis}), "
            f"bằng {round(sep['gap_ratio'] * 100, 1)}% kích thước dữ liệu. Mọi con số sai lệch "
            "bên dưới là vô nghĩa — hai bộ dữ liệu đang ở hệ toạ độ khác nhau. Hãy đưa về "
            "cùng hệ trước (dùng current/target_coordinate_system khi import, hoặc dời gốc "
            "toạ độ khi xuất mô hình BIM)."
        )
    if uncertain > 0.01 * len(pts):
        out["accuracy_warning"] = (
            f"{out['uncertain_pct']}% số điểm không chứng minh được là đã tìm đúng tam giác "
            f"gần nhất (candidates={candidates}). Hãy chạy lại với candidates lớn hơn "
            "(ví dụ 16 hoặc 32) và so hai kết quả."
        )

    if output_csv:
        d, f = res["distance"], res["face_index"]
        rows = ((round(float(pts[i, 0]), 6), round(float(pts[i, 1]), 6),
                 round(float(pts[i, 2]), 6), round(float(signed[i]), 6),
                 round(float(d[i]), 6), int(f[i]), int(res["uncertain"][i]))
                for i in range(len(pts)))
        out["csv"] = pc.write_csv(
            output_csv,
            ["x", "y", "z", "signed_deviation", "abs_distance", "face_index", "uncertain"],
            rows)
    if output_las:
        scaled = np.clip(np.abs(signed) / (tolerance or 1.0), 0, 1) * 65535
        out["las"] = pc.write_las(output_las, pts,
                                  extra={"intensity": scaled.astype(np.uint16)})
        out["las"]["note"] = ("Trường intensity được ghi đè bằng độ lệch đã chuẩn hoá theo "
                              "dung sai, để tô màu trực quan trong phần mềm xem point cloud.")
    return out


@mcp.tool()
@safe
def compare_point_clouds(
    reference_path: str,
    target_path: str,
    tolerance: Optional[float] = 0.02,
    bbox: Optional[Dict[str, List[float]]] = None,
    every_nth: int = 1,
    max_points: int = 2_000_000,
    output_csv: Optional[str] = None,
) -> dict:
    """
    So sánh hai đám mây điểm bằng khoảng cách tới điểm gần nhất (cloud-to-cloud).

    Dùng cho: đo biến dạng/lún giữa hai đợt quét cùng một công trình, hoặc kiểm tra
    độ khớp giữa hai trạm quét có vùng chồng lấn (ra con số RMS đăng ký mà manifest
    ReCap không ghi).

    Lưu ý bản chất: khoảng cách cloud-to-cloud LUÔN dương và có xu hướng ĐÁNH GIÁ
    THẤP chuyển vị khi hai mặt trượt song song với nhau. Muốn chuyển vị có dấu theo
    phương pháp tuyến thì dùng compare_to_bim_mesh với lưới dựng từ đợt quét gốc.
    """
    pc._require_scipy()
    ref, _, ref_info = pc.load_points(reference_path, bbox=bbox, every_nth=every_nth,
                                      max_points=max_points)
    tgt, _, tgt_info = pc.load_points(target_path, bbox=bbox, every_nth=every_nth,
                                      max_points=max_points)
    if len(ref) == 0 or len(tgt) == 0:
        raise RecapError(
            f"Một trong hai đám mây rỗng sau khi lọc (tham chiếu: {len(ref)}, "
            f"đích: {len(tgt)}). Kiểm tra bbox."
        )

    tree = pc.cKDTree(ref)
    dist = np.empty(len(tgt))
    for s in range(0, len(tgt), pc.CHUNK):
        sl = slice(s, min(s + pc.CHUNK, len(tgt)))
        dist[sl], _ = tree.query(tgt[sl], k=1)

    stats = pc.describe(dist, tolerance=tolerance)
    out: Dict[str, Any] = {
        "reference": {"file": reference_path, "points": int(len(ref)), "info": ref_info},
        "target": {"file": target_path, "points": int(len(tgt)), "info": tgt_info},
        "distance": stats,
        "histogram": pc.histogram(dist, bins=20),
        "note": ("Khoảng cách cloud-to-cloud luôn không âm. Giá trị mean khác 0 rõ rệt "
                 "phản ánh cả chuyển vị thật lẫn chênh lệch mật độ điểm giữa hai lần quét."),
    }
    if output_csv:
        rows = ((round(float(tgt[i, 0]), 6), round(float(tgt[i, 1]), 6),
                 round(float(tgt[i, 2]), 6), round(float(dist[i]), 6))
                for i in range(len(tgt)))
        out["csv"] = pc.write_csv(output_csv, ["x", "y", "z", "distance"], rows)
    return out


@mcp.tool()
@safe
def elevation_grid_report(
    path: str,
    cell_size: float = 0.5,
    statistic: str = "mean",
    bbox: Optional[Dict[str, List[float]]] = None,
    classification: Optional[List[int]] = None,
    output_csv: Optional[str] = None,
    max_points: int = 10_000_000,
) -> dict:
    """
    Gộp điểm thành lưới ô vuông theo mặt bằng và tổng hợp cao độ từng ô.

    Dùng cho: bản đồ cao độ mặt đường, bản đồ lún, kiểm tra độ bằng phẳng bề mặt.
    statistic nhận: mean, min, max, count, std, range.
    'range' (max trừ min trong ô) rất hữu ích để phát hiện ô có vật thể đứng
    (lan can, cột) lẫn vào bề mặt.

    Với mặt đường, hãy đặt classification=[2] nếu file LAS đã phân loại mặt đất.
    """
    pts, _, info = pc.load_points(path, bbox=bbox, classification=classification,
                                  max_points=max_points)
    if len(pts) == 0:
        raise RecapError("Không có điểm nào trong phạm vi lọc.")
    grid = pc.elevation_grid(pts, cell_size, statistic)
    values = np.array([c["value"] for c in grid["cells"]], dtype=float)

    out: Dict[str, Any] = {
        "cell_size": cell_size,
        "statistic": statistic,
        "grid_size": grid["grid_size"],
        "occupied_cells": len(grid["cells"]),
        "total_cells": grid["grid_size"][0] * grid["grid_size"][1],
        "coverage_pct": round(100.0 * len(grid["cells"]) /
                              (grid["grid_size"][0] * grid["grid_size"][1]), 3),
        "value_stats": pc.describe(values),
        "points_used": int(len(pts)),
        "load_info": info,
    }
    if output_csv:
        rows = ((c["x"], c["y"], c["value"], c["count"]) for c in grid["cells"])
        out["csv"] = pc.write_csv(output_csv, ["x", "y", statistic, "point_count"], rows)
    else:
        out["cells_preview"] = grid["cells"][:50]
        out["hint"] = "Đặt output_csv để ghi toàn bộ lưới ra file cho vẽ bản đồ."
    return out


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
