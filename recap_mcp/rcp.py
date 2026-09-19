# -*- coding: utf-8 -*-
"""
Đọc định dạng project của Autodesk ReCap — không cần SDK của Autodesk.
=====================================================================

Hai định dạng, hai cách đọc khác hẳn nhau:

``.rcp`` (project)
    Là một file ZIP. Bên trong có đúng một file XML manifest chứa toàn bộ
    thông tin quản lý: danh sách scan, ma trận đăng ký, chất lượng đăng ký,
    phép đo, phân vùng, hệ toạ độ, camera. Đọc bằng ``zipfile`` + ``ElementTree``.

``.rcs`` (dữ liệu điểm của một scan)
    Là octree nhị phân đóng, magic ``ADOCT``. KHÔNG giải mã được toạ độ điểm.
    Nhưng phần header thì đọc được, và đã được kiểm chứng trên nhiều scan:
    nó cho ra vị trí đăng ký, góc xoay, hai bounding box và số điểm chính xác.

Vì sao không dùng SDK: các DLL ``AdskRealityStudio*.dll`` chỉ export tên C++
mangled của phương thức lớp, không có entry point C ABI nào — không bọc được
bằng ctypes. Đường đọc file là đường khả thi duy nhất từ Python.
"""

from __future__ import annotations

import math
import re
import struct
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional
from xml.etree import ElementTree as ET


class RecapError(Exception):
    """Lỗi nghiệp vụ đã được diễn giải sang thông điệp người đọc hiểu được."""


# ============================================================================
# Tiện ích đọc thuộc tính XML
# ============================================================================

def _f(el: Optional[ET.Element], *names: str, default: float = 0.0) -> float:
    """Đọc thuộc tính số thực đầu tiên tìm thấy trong ``names``."""
    if el is None:
        return default
    for n in names:
        if n in el.attrib:
            try:
                return float(el.attrib[n])
            except (TypeError, ValueError):
                return default
    return default


def _i(el: Optional[ET.Element], *names: str, default: int = 0) -> int:
    try:
        return int(round(_f(el, *names, default=default)))
    except (TypeError, ValueError):
        return default


def _s(el: Optional[ET.Element], *names: str, default: str = "") -> str:
    if el is None:
        return default
    for n in names:
        if n in el.attrib:
            return el.attrib[n]
    return default


def _b(el: Optional[ET.Element], *names: str) -> bool:
    return _i(el, *names, default=0) != 0


def _xyz(el: Optional[ET.Element], lower: bool = True) -> List[float]:
    """Đọc một node có thuộc tính x/y/z (hoặc X/Y/Z)."""
    if el is None:
        return [0.0, 0.0, 0.0]
    return [_f(el, "x", "X"), _f(el, "y", "Y"), _f(el, "z", "Z")]


# ============================================================================
# Hình học
# ============================================================================

def compose_matrix(translation: List[float], rot3x3: Optional[List[List[float]]]) -> List[List[float]]:
    """Ghép tịnh tiến + xoay 3x3 thành ma trận thuần nhất 4x4 (row-major)."""
    r = rot3x3 or [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    t = translation or [0.0, 0.0, 0.0]
    return [
        [r[0][0], r[0][1], r[0][2], t[0]],
        [r[1][0], r[1][1], r[1][2], t[1]],
        [r[2][0], r[2][1], r[2][2], t[2]],
        [0.0, 0.0, 0.0, 1.0],
    ]


def _read_tform(tform: Optional[ET.Element]) -> Dict[str, Any]:
    """Đọc block ``<tform><T/><R/></tform>`` — ma trận xoay đầy đủ, không phải euler."""
    if tform is None:
        return {}
    t_el, r_el = tform.find("T"), tform.find("R")
    translation = _xyz(t_el)
    rot = None
    if r_el is not None:
        rot = [
            [_f(r_el, "xx"), _f(r_el, "xy"), _f(r_el, "xz")],
            [_f(r_el, "yx"), _f(r_el, "yy"), _f(r_el, "yz")],
            [_f(r_el, "zx"), _f(r_el, "zy"), _f(r_el, "zz")],
        ]
    out: Dict[str, Any] = {"translation": translation, "matrix4x4": compose_matrix(translation, rot)}
    if rot:
        out["rotation_matrix"] = rot
        # Góc quanh trục đứng, đại lượng hay dùng nhất khi kiểm tra đăng ký trạm quét.
        out["heading_deg"] = round(math.degrees(math.atan2(rot[0][1], rot[0][0])) % 360.0, 6)
    return out


# ============================================================================
# Header file .rcs
# ============================================================================

RCS_MAGIC = b"ADOCT"
_GUID_RE = re.compile(rb"\{[0-9A-Fa-f_]{36}\}")


def read_rcs_header(path: str | Path) -> Dict[str, Any]:
    """
    Đọc header của một file ``.rcs``.

    Các trường dưới đây đã được kiểm chứng bằng cách đối chiếu với manifest
    ``.rcp`` trên nhiều scan: ``num_points`` khớp chính xác, ``guid`` khớp
    ``VoxelTreeRunTime/@Id``, ``translation`` khớp ma trận đăng ký.

    Hai bounding box khác nhau và đừng lẫn:
      * ``octree_bounds`` — hộp của cấu trúc octree, luôn là bội của luỹ thừa 2,
        RỘNG HƠN dữ liệu thật.
      * ``data_bounds`` — hộp bó sát tập điểm thật. Dùng cái này khi tính toán.

    Toạ độ ở đây là hệ CỤC BỘ của scan (chưa nhân ma trận đăng ký).
    """
    p = Path(path)
    if not p.is_file():
        raise RecapError(f"Không tìm thấy file .rcs: {p}")

    with p.open("rb") as fh:
        head = fh.read(512)
    if len(head) < 0x140:
        raise RecapError(f"File .rcs quá ngắn, hỏng hoặc chưa ghi xong: {p}")
    if head[:5] != RCS_MAGIC:
        raise RecapError(
            f"'{p.name}' không phải file .rcs của ReCap "
            f"(magic đọc được: {head[:5]!r}, cần {RCS_MAGIC!r})."
        )

    ver_major, ver_minor = struct.unpack_from("<II", head, 8)
    d = struct.unpack_from("<24d", head, 0x10)
    reserved_a, reserved_b, num_points, _pad = struct.unpack_from("<4I", head, 0xD0)

    m = _GUID_RE.search(head, 0xE0)
    guid = m.group(0).decode("latin1") if m else ""

    return {
        "file": str(p),
        "size_bytes": p.stat().st_size,
        "format_version": f"{ver_major}.{ver_minor}",
        "guid": guid,
        "num_points": num_points,
        "translation": [round(v, 9) for v in d[0:3]],
        "rotation_euler_deg": [round(v, 9) for v in d[3:6]],
        "scale": [round(v, 9) for v in d[6:9]],
        "octree_bounds": {
            "min": [round(v, 6) for v in d[12:15]],
            "max": [round(v, 6) for v in d[15:18]],
        },
        "data_bounds": {
            "min": [round(v, 6) for v in d[18:21]],
            "max": [round(v, 6) for v in d[21:24]],
        },
        "reserved": [reserved_a, reserved_b],
        "note": (
            "Toạ độ trong header là hệ CỤC BỘ của scan. Muốn về hệ project thì nhân "
            "với registration.matrix4x4 lấy từ get_scan_registration. Dữ liệu điểm bên "
            "trong .rcs là octree đóng, không giải mã được — muốn đọc từng điểm thì "
            "dùng file LAS/LAZ gốc."
        ),
    }


# ============================================================================
# Manifest .rcp
# ============================================================================

# ReCap không công bố bảng enum này. Các nhãn dưới đây là SUY ĐOÁN dựa trên
# đối chiếu với dữ liệu mẫu, nên luôn trả kèm giá trị thô để người dùng tự quyết.
_UNIT_GUESS = {0: "không xác định", 1: "mét", 2: "foot", 3: "inch", 4: "milimét", 5: "centimét"}


def _parse_scans(root: ET.Element, rcp_path: Path) -> List[Dict[str, Any]]:
    """Gộp ba nguồn rời rạc trong manifest thành một danh sách scan thống nhất."""
    base = rcp_path.parent

    # Nguồn 1: <Nodes>/<VoxelTreeRunTime> — thuộc tính hiển thị và thống kê điểm.
    scans: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for node in root.findall("./Nodes/VoxelTreeRunTime"):
        pcp_id = node.get("Id", "")
        rel = _s(node.find("RelativePath"), "Value")
        absolute = _s(node.find("Path"), "Value")
        # RelativePath đáng tin hơn Path: Path còn giữ đường dẫn của máy đã tạo file.
        resolved = (base / rel.lstrip(".\\/")).resolve() if rel else Path(absolute)
        entry = {
            "id": pcp_id,
            "name": _s(node.find("NodeName"), "Value"),
            "group": _s(node.find("Group"), "Value"),
            "visible": _b(node.find("Visible"), "Value"),
            "selected": _b(node.find("Selected"), "Value"),
            "num_points": _i(node.find("NumPoints"), "Value"),
            "has_rgb": _b(node.find("HasRGB"), "Value"),
            "has_normals": _b(node.find("HasNormals"), "Value"),
            "has_intensity": _b(node.find("HasIntensity"), "Value"),
            "intensity": {
                "min": _i(node.find("MinIntensity"), "Value"),
                "max": _i(node.find("MaxIntensity"), "Value"),
                "normalized": _b(node.find("NormalizeIntensity"), "Value"),
            },
            "range_image": {
                "width": _i(node.find("RangeImageWidth"), "Value"),
                "height": _i(node.find("RangeImageHeight"), "Value"),
            },
            "is_lidar": _b(node.find("IsLidarData"), "Value"),
            "translation": _xyz(node.find("Translation")),
            "rotation_euler_deg": _xyz(node.find("Rotation")),
            "scale": _xyz(node.find("Scale")),
            "rcs_relative_path": rel,
            "rcs_path": str(resolved),
            "rcs_exists": resolved.is_file(),
            "rcs_size_bytes": resolved.stat().st_size if resolved.is_file() else 0,
            "registration": {},
        }
        scans[pcp_id] = entry
        order.append(pcp_id)

    # Nguồn 2: <Project>/<ShotInfo> — nối PCPid của node với shot id dùng cho đăng ký.
    shot_to_pcp: Dict[str, str] = {}
    for shot in root.findall("./Project/ShotInfo"):
        pcp_id, shot_id = shot.get("PCPid", ""), shot.get("id", "")
        if pcp_id in scans:
            shot_to_pcp[shot_id] = pcp_id
            scans[pcp_id]["shot_id"] = shot_id
            raw = shot.get("rawScanPath", "")
            if raw:
                scans[pcp_id]["raw_scan_path"] = raw
            tf = _read_tform(shot.find("tform"))
            if tf:
                scans[pcp_id]["registration"].update(tf)

    # Nguồn 3: <GroupInfo> lồng nhau — chất lượng đăng ký, thứ mà nghiên cứu cần.
    for grp in root.iter("GroupInfo"):
        pcp_id = shot_to_pcp.get(grp.get("id", ""))
        if not pcp_id:
            continue
        reg = grp.find("LaserRegInfo")
        target = scans[pcp_id]["registration"]
        if reg is not None:
            hist = reg.find("histogram")
            target.update({
                "is_registered": _b(reg, "isReg"),
                "level": reg.get("level", ""),
                "status": reg.get("status", ""),
                "histogram_bin_size": _f(hist, "binSize") if hist is not None else None,
            })
        tf = _read_tform(grp.find("tform"))
        for k, v in tf.items():
            target.setdefault(k, v)

    for pcp_id in order:
        reg = scans[pcp_id]["registration"]
        reg.setdefault("is_registered", False)
        reg.setdefault("level", "")
        reg.setdefault("status", "")
        if "matrix4x4" not in reg:
            reg["matrix4x4"] = compose_matrix(scans[pcp_id]["translation"], None)
            reg["matrix_source"] = "chỉ có tịnh tiến — manifest không chứa ma trận xoay cho scan này"
        else:
            reg["matrix_source"] = "ShotInfo/tform (ma trận xoay đầy đủ)"

    return [scans[i] for i in order]


def _parse_measurements(root: ET.Element) -> List[Dict[str, Any]]:
    """Đọc ghi chú, khoảng cách và phép đo nâng cao — kèm toạ độ 3D."""
    out: List[Dict[str, Any]] = []
    container = root.find("Measurements")
    if container is None:
        return out

    for el in container:
        item: Dict[str, Any] = {
            "kind": el.tag,
            "name": _s(el.find("NodeName"), "Value"),
            "visible": _b(el.find("Visible"), "Value"),
            "precision": _i(el.find("Precision"), "Value"),
            "guid": _s(el.find("GUID"), "Value"),
        }
        text = _s(el.find("Text"), "Value")
        if text:
            item["text"] = text

        pos = el.find("Position")
        if pos is not None:
            item["position"] = _xyz(pos)

        picks = []
        for tag in ("Pick1", "Pick2", "Pick3"):
            pk = el.find(tag)
            if pk is not None:
                inner = pk.find("Position")
                picks.append(_xyz(inner if inner is not None else pk))
        if picks:
            item["picks"] = picks
            if len(picks) >= 2:
                a, b = picks[0], picks[1]
                item["distance"] = round(math.dist(a, b), 6)
                item["delta"] = [round(b[i] - a[i], 6) for i in range(3)]

        normals = [_xyz(el.find(t)) for t in ("Normal1", "Normal2") if el.find(t) is not None]
        if normals:
            item["normals"] = normals

        ann = el.find("AnnotationInfo")
        if ann is not None and ann.get("Title"):
            item["title"] = ann.get("Title")
        out.append(item)
    return out


def _parse_regions(root: ET.Element) -> List[Dict[str, Any]]:
    """Đọc các layer phân vùng — kết quả phân loại thủ công trong ReCap."""
    out: List[Dict[str, Any]] = []
    for layer in root.findall("./PointCloudSegmentList/RegionManager/RegionArray/Layer"):
        color = layer.find("Color")
        bounds = layer.find("SelectionBounds")
        item: Dict[str, Any] = {
            "id": layer.get("Id", ""),
            "name": layer.get("Name", ""),
            "index": _i(layer, "Index"),
            "visible": _b(layer, "Visible"),
            "locked": _b(layer, "Locked"),
            "removed": _b(layer, "Removed"),
            "color_rgb": [_f(color, "R"), _f(color, "G"), _f(color, "B")] if color is not None else None,
        }
        if bounds is not None:
            mn, mx = bounds.find("Min"), bounds.find("Max")
            item["selection_bounds"] = {"min": _xyz(mn), "max": _xyz(mx)}
        out.append(item)
    return out


def _parse_limit_boxes(root: ET.Element) -> List[Dict[str, Any]]:
    out = []
    for lb in root.findall("./LimitBoxes/LimitBox"):
        item = {"name": lb.get("NodeName", ""), "visible": _b(lb, "Visible")}
        mn, mx = lb.find("Min"), lb.find("Max")
        if mn is not None or mx is not None:
            item["bounds"] = {"min": _xyz(mn), "max": _xyz(mx)}
        out.append(item)
    return out


def _parse_bounds(root: ET.Element) -> Optional[Dict[str, List[float]]]:
    """Hộp bao của toàn project, lấy từ ``<Project>/<map>/<bounds>``."""
    for mp in root.findall("./Project/map"):
        b = mp.find("bounds")
        if b is None:
            continue
        mn = [_f(b, "minx"), _f(b, "miny"), _f(b, "minz")]
        mx = [_f(b, "maxx"), _f(b, "maxy"), _f(b, "maxz")]
        if all(mx[i] >= mn[i] for i in range(3)):       # hộp rỗng ghi ngược min/max
            return {"min": mn, "max": mx,
                    "size": [round(mx[i] - mn[i], 6) for i in range(3)]}
    return None


def _parse_global_transform(root: ET.Element) -> Optional[List[List[float]]]:
    el = root.find("GlobalTransformation")
    if el is None:
        return None
    return [[_f(el, f"m{r}{c}") for c in (1, 2, 3, 4)] for r in (1, 2, 3, 4)]


def parse_rcp(path: str | Path, include_geometry: bool = True) -> Dict[str, Any]:
    """
    Mở file ``.rcp`` và trả về toàn bộ manifest đã được cấu trúc hoá.

    ``include_geometry=False`` bỏ qua measurement/region/limit box để trả về
    nhanh phần tóm tắt, dùng khi chỉ cần liệt kê scan.
    """
    p = Path(path)
    if not p.is_file():
        raise RecapError(f"Không tìm thấy file project: {p}")
    if not zipfile.is_zipfile(p):
        raise RecapError(
            f"'{p.name}' không mở được như file .rcp. File .rcp hợp lệ là một ZIP "
            "chứa manifest XML. File này có thể đã hỏng, hoặc là .rcs (dữ liệu một "
            "scan) — nếu vậy hãy dùng read_rcs_header."
        )

    with zipfile.ZipFile(p) as z:
        names = z.namelist()
        xml_names = [n for n in names if n.lower().endswith(".xml")]
        if not xml_names:
            raise RecapError(
                f"'{p.name}' là ZIP nhưng không chứa manifest XML nào "
                f"(các mục: {', '.join(names[:8]) or 'rỗng'})."
            )
        manifest_name = xml_names[0]
        raw = z.read(manifest_name)
        previews = [
            {"name": n, "size_bytes": z.getinfo(n).file_size}
            for n in names if n.lower().endswith((".jpg", ".jpeg", ".png", ".bmp"))
        ]

    try:
        root = ET.fromstring(raw.decode("utf-8", "replace"))
    except ET.ParseError as exc:
        raise RecapError(f"Manifest XML trong '{p.name}' hỏng: {exc}") from exc

    meta = root.find("./ProjectMetaData")
    save = root.find("./ProjectMetaData/SaveInformation")
    unit_el = root.find("UnitType")
    unit_raw = _i(unit_el, "Value", default=-1)
    geo = root.find("./GeoReference/Origin")
    proj = root.find("Project")

    result: Dict[str, Any] = {
        "project_file": str(p),
        "project_name": p.stem,
        "support_folder": str(p.parent / f"{p.stem} Support"),
        "manifest_entry": manifest_name,
        "preview_entries": previews,
        "schema_version": root.get("Version", ""),
        "metadata": {
            "version": (
                f"{_s(meta, 'MajorVersion')}.{_s(meta, 'MinorVersion')}.{_s(meta, 'PatchVersion')}"
                if meta is not None else ""
            ),
            "saved_by_application": _s(save, "Application"),
            "saved_by_version": _s(save, "ApplicationVersion"),
            "saved_timestamp": _s(save, "Timestamp"),
            "original_path": _s(proj, "path") if proj is not None else "",
        },
        "units": {
            "raw_value": unit_raw,
            "guess": _UNIT_GUESS.get(unit_raw, "không rõ"),
            "survey_mode": _b(unit_el, "IsSurveyMode"),
            "precision": _i(root.find("UnitPrecision"), "Value"),
            "warning": (
                "Autodesk không công bố bảng enum đơn vị của ReCap. 'guess' là suy đoán "
                "từ dữ liệu mẫu — hãy đối chiếu với kích thước thật trong 'bounds' trước "
                "khi dùng cho tính toán khoa học."
            ),
        },
        "coordinate_system": _s(root.find("CoordinateSystem"), "Value"),
        "geo_reference_origin": _xyz(geo) if geo is not None else None,
        "global_transformation": _parse_global_transform(root),
        "bounds": _parse_bounds(root),
        "scans": _parse_scans(root, p),
    }
    result["scan_count"] = len(result["scans"])
    result["total_points"] = sum(s["num_points"] for s in result["scans"])
    result["registered_scan_count"] = sum(
        1 for s in result["scans"] if s["registration"].get("is_registered")
    )

    if include_geometry:
        result["measurements"] = _parse_measurements(root)
        result["regions"] = _parse_regions(root)
        result["limit_boxes"] = _parse_limit_boxes(root)
        result["view_states"] = [
            {"id": v.get("ID", ""), "title": v.get("Title", ""), "color_mode": v.get("ColorMode", "")}
            for v in root.findall("./ViewStates/ViewState")
        ]
    return result


def extract_preview(rcp_path: str | Path, out_path: str | Path,
                    entry: Optional[str] = None) -> Dict[str, Any]:
    """Rút ảnh xem trước / ảnh chụp màn hình nhúng trong file .rcp ra đĩa."""
    p, out = Path(rcp_path), Path(out_path)
    if not zipfile.is_zipfile(p):
        raise RecapError(f"'{p.name}' không phải file .rcp hợp lệ.")
    with zipfile.ZipFile(p) as z:
        images = [n for n in z.namelist() if n.lower().endswith((".jpg", ".jpeg", ".png", ".bmp"))]
        if not images:
            raise RecapError(f"'{p.name}' không chứa ảnh xem trước nào.")
        name = entry or min(images, key=lambda n: z.getinfo(n).file_size)
        if name not in images:
            raise RecapError(f"Không có mục ảnh '{name}'. Có: {', '.join(images)}")
        data = z.read(name)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    return {"entry": name, "output": str(out), "size_bytes": len(data), "available": images}
