# -*- coding: utf-8 -*-
"""
Phân tích point cloud LAS/LAZ phục vụ Scan-to-BIM.
==================================================

Vì sao lớp này đọc LAS/LAZ chứ không đọc .rcs: dữ liệu điểm trong .rcs là octree
đóng của Autodesk, không giải mã được. Quy trình đúng vì vậy là giữ SONG SONG hai
bộ dữ liệu — ReCap lo đăng ký trạm quét và lập chỉ mục để xem, còn file LAS/LAZ
gốc là nguồn toạ độ điểm cho mọi tính toán định lượng.

Quy ước dấu của độ lệch (dùng thống nhất toàn module):
    dương  = điểm quét nằm PHÍA PHÁP TUYẾN của mặt tham chiếu (thường là phía
             ngoài / lồi ra so với thiết kế)
    âm     = điểm quét nằm phía trong / lõm vào so với thiết kế
Dấu phụ thuộc hướng pháp tuyến của lưới BIM đầu vào, nên luôn kiểm tra lại bằng
một vị trí đã biết trước khi diễn giải kết quả.
"""

from __future__ import annotations

import csv
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .rcp import RecapError

try:
    import laspy
except ImportError:                                   # pragma: no cover
    laspy = None

try:
    from scipy.spatial import cKDTree
except ImportError:                                   # pragma: no cover
    cKDTree = None

CHUNK = 250_000        # số điểm xử lý mỗi lượt, giữ bộ nhớ ở mức vài trăm MB


def _require_laspy() -> None:
    if laspy is None:
        raise RecapError(
            "Thiếu thư viện 'laspy'. Cài bằng: pip install \"laspy[lazrs]\" "
            "(gói lazrs là phần giải nén .laz)."
        )


def _require_scipy() -> None:
    if cKDTree is None:
        raise RecapError("Thiếu thư viện 'scipy'. Cài bằng: pip install scipy")


# ============================================================================
# Đọc file
# ============================================================================

def inspect_las(path: str | Path) -> Dict[str, Any]:
    """Đọc header LAS/LAZ mà không nạp toàn bộ điểm vào bộ nhớ."""
    _require_laspy()
    p = Path(path)
    if not p.is_file():
        raise RecapError(f"Không tìm thấy file point cloud: {p}")

    with laspy.open(str(p)) as reader:
        h = reader.header
        mins, maxs = list(h.mins), list(h.maxs)
        crs = None
        try:
            parsed = h.parse_crs()
            crs = parsed.to_string() if parsed is not None else None
        except Exception:                              # CRS hỏng không được làm hỏng cả tool
            crs = None
        dims = [d.name for d in h.point_format.dimensions]

    size = [round(maxs[i] - mins[i], 6) for i in range(3)]
    volume = size[0] * size[1] * size[2]
    return {
        "file": str(p),
        "size_bytes": p.stat().st_size,
        "las_version": f"{h.version.major}.{h.version.minor}",
        "point_format": h.point_format.id,
        "point_count": int(h.point_count),
        "scales": [float(v) for v in h.scales],
        "offsets": [float(v) for v in h.offsets],
        "bounds": {"min": mins, "max": maxs, "size": size},
        "average_density_per_m3": round(h.point_count / volume, 3) if volume > 0 else None,
        "crs": crs,
        "dimensions": dims,
        "has_rgb": "red" in dims,
        "has_intensity": "intensity" in dims,
        "has_classification": "classification" in dims,
        "creation_date": str(h.creation_date) if h.creation_date else None,
        "generating_software": getattr(h, "generating_software", None),
        "vlr_count": len(h.vlrs),
    }


def _chunk_filter(pts: np.ndarray, bbox: Optional[Dict[str, List[float]]]) -> np.ndarray:
    if not bbox:
        return np.ones(len(pts), dtype=bool)
    mn = np.asarray(bbox["min"], dtype=float)
    mx = np.asarray(bbox["max"], dtype=float)
    return np.all((pts >= mn) & (pts <= mx), axis=1)


def load_points(
    path: str | Path,
    bbox: Optional[Dict[str, List[float]]] = None,
    classification: Optional[List[int]] = None,
    every_nth: int = 1,
    max_points: Optional[int] = 5_000_000,
    with_extra: bool = False,
) -> Tuple[np.ndarray, Dict[str, np.ndarray], Dict[str, Any]]:
    """
    Nạp toạ độ điểm theo lô, có lọc theo hộp bao / lớp phân loại / bước nhảy.

    ``max_points`` là hàng rào an toàn: một file LiDAR vài trăm triệu điểm nạp hết
    vào RAM sẽ giết tiến trình MCP. Khi chạm ngưỡng, hàm DỪNG và báo rõ trong
    ``info['truncated']`` thay vì âm thầm trả về một phần dữ liệu.
    """
    _require_laspy()
    p = Path(path)
    if not p.is_file():
        raise RecapError(f"Không tìm thấy file point cloud: {p}")
    if every_nth < 1:
        raise RecapError("every_nth phải >= 1.")

    keep_xyz: List[np.ndarray] = []
    keep_extra: Dict[str, List[np.ndarray]] = {}
    total_read = 0
    kept = 0
    truncated = False

    with laspy.open(str(p)) as reader:
        dims = {d.name for d in reader.header.point_format.dimensions}
        want = [n for n in ("intensity", "classification", "red", "green", "blue")
                if with_extra and n in dims]
        for chunk in reader.chunk_iterator(CHUNK):
            total_read += len(chunk)
            xyz = np.column_stack([
                np.asarray(chunk.x, dtype=np.float64),
                np.asarray(chunk.y, dtype=np.float64),
                np.asarray(chunk.z, dtype=np.float64),
            ])
            mask = _chunk_filter(xyz, bbox)
            if classification and "classification" in dims:
                cls = np.asarray(chunk.classification)
                mask &= np.isin(cls, np.asarray(classification))
            idx = np.flatnonzero(mask)
            if every_nth > 1:
                idx = idx[::every_nth]
            if idx.size == 0:
                continue

            if max_points is not None and kept + idx.size > max_points:
                idx = idx[: max(0, max_points - kept)]
                truncated = True
            if idx.size:
                keep_xyz.append(xyz[idx])
                for n in want:
                    keep_extra.setdefault(n, []).append(np.asarray(getattr(chunk, n))[idx])
                kept += idx.size
            if truncated:
                break

    pts = np.vstack(keep_xyz) if keep_xyz else np.empty((0, 3), dtype=np.float64)
    extra = {k: np.concatenate(v) for k, v in keep_extra.items()}
    info = {
        "points_read": total_read,
        "points_kept": int(len(pts)),
        "truncated": truncated,
        "filters": {"bbox": bbox, "classification": classification, "every_nth": every_nth},
    }
    if truncated:
        info["warning"] = (
            f"Đã dừng ở {max_points} điểm — file còn dữ liệu chưa đọc. Kết quả thống kê "
            "bên dưới CHỈ tính trên phần đã đọc, không đại diện cho toàn bộ file. "
            "Hãy thu hẹp bbox, tăng every_nth, hoặc nâng max_points."
        )
    return pts, extra, info


# ============================================================================
# Thống kê
# ============================================================================

def describe(values: np.ndarray, tolerance: Optional[float] = None) -> Dict[str, Any]:
    """Bộ chỉ số chuẩn dùng trong báo cáo độ chính xác Scan-to-BIM."""
    if values.size == 0:
        return {"count": 0, "note": "Không có điểm nào thoả điều kiện lọc."}
    a = np.abs(values)
    out: Dict[str, Any] = {
        "count": int(values.size),
        "mean": float(np.mean(values)),
        "median": float(np.median(values)),
        "std": float(np.std(values, ddof=1)) if values.size > 1 else 0.0,
        "rms": float(np.sqrt(np.mean(values ** 2))),
        "min": float(np.min(values)),
        "max": float(np.max(values)),
        "mean_abs": float(np.mean(a)),
        "max_abs": float(np.max(a)),
        "p68_abs": float(np.percentile(a, 68.27)),
        "p95_abs": float(np.percentile(a, 95)),
        "p99_abs": float(np.percentile(a, 99)),
    }
    if tolerance is not None and tolerance > 0:
        within = int(np.count_nonzero(a <= tolerance))
        out["tolerance"] = tolerance
        out["within_tolerance"] = within
        out["within_tolerance_pct"] = round(100.0 * within / values.size, 3)
        out["outside_tolerance"] = int(values.size - within)
    return out


def histogram(values: np.ndarray, bins: int = 20) -> List[Dict[str, Any]]:
    if values.size == 0:
        return []
    counts, edges = np.histogram(values, bins=bins)
    return [
        {"from": float(edges[i]), "to": float(edges[i + 1]),
         "count": int(counts[i]), "pct": round(100.0 * counts[i] / values.size, 3)}
        for i in range(len(counts))
    ]


# ============================================================================
# Đọc lưới tam giác từ BIM
# ============================================================================

def load_mesh(path: str | Path) -> Tuple[np.ndarray, np.ndarray]:
    """
    Đọc lưới tam giác từ OBJ / STL (nhị phân và text) / PLY (ascii, little-endian).

    Đây là các định dạng mà Revit, Navisworks và Forma đều xuất được, nên nó là
    cầu nối giữa mô hình BIM và point cloud mà không cần thư viện CAD nào.
    """
    p = Path(path)
    if not p.is_file():
        raise RecapError(f"Không tìm thấy file lưới: {p}")
    ext = p.suffix.lower()
    if ext == ".obj":
        verts, faces = _load_obj(p)
    elif ext == ".stl":
        verts, faces = _load_stl(p)
    elif ext == ".ply":
        verts, faces = _load_ply(p)
    else:
        raise RecapError(
            f"Định dạng lưới '{ext}' chưa hỗ trợ. Hãy xuất mô hình BIM ra .obj, .stl "
            "hoặc .ply (Revit, Navisworks và Forma đều xuất được các định dạng này)."
        )
    if len(faces) == 0:
        raise RecapError(f"'{p.name}' không chứa tam giác nào.")
    if faces.max() >= len(verts):
        raise RecapError(
            f"'{p.name}' hỏng: chỉ số đỉnh {int(faces.max())} vượt quá số đỉnh {len(verts)}."
        )
    return verts, faces


def _triangulate(idx: List[int]) -> List[Tuple[int, int, int]]:
    """Quạt tam giác cho mặt nhiều cạnh — đúng với mặt phẳng lồi, là trường hợp của BIM."""
    return [(idx[0], idx[i], idx[i + 1]) for i in range(1, len(idx) - 1)]


def _load_obj(p: Path) -> Tuple[np.ndarray, np.ndarray]:
    verts: List[Tuple[float, float, float]] = []
    faces: List[Tuple[int, int, int]] = []
    with p.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if line.startswith("v "):
                parts = line.split()
                verts.append((float(parts[1]), float(parts[2]), float(parts[3])))
            elif line.startswith("f "):
                idx = []
                for tok in line.split()[1:]:
                    n = int(tok.split("/")[0])
                    idx.append(n - 1 if n > 0 else len(verts) + n)   # OBJ cho phép chỉ số âm
                if len(idx) >= 3:
                    faces.extend(_triangulate(idx))
    return np.asarray(verts, dtype=np.float64), np.asarray(faces, dtype=np.int64)


def _load_stl(p: Path) -> Tuple[np.ndarray, np.ndarray]:
    data = p.read_bytes()
    is_ascii = data[:5].lower() == b"solid" and b"facet normal" in data[:2048]
    if is_ascii:
        verts = []
        for line in data.decode("utf-8", "replace").splitlines():
            s = line.strip()
            if s.startswith("vertex"):
                parts = s.split()
                verts.append((float(parts[1]), float(parts[2]), float(parts[3])))
        v = np.asarray(verts, dtype=np.float64)
    else:
        count = int(np.frombuffer(data, dtype="<u4", count=1, offset=80)[0])
        expected = 84 + count * 50
        if len(data) < expected:
            raise RecapError(
                f"'{p.name}' khai báo {count} tam giác nhưng file chỉ có {len(data)} byte "
                f"(cần {expected}). File bị cắt cụt."
            )
        rec = np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("attr", "<u2")])
        tris = np.frombuffer(data, dtype=rec, count=count, offset=84)
        v = tris["v"].reshape(-1, 3).astype(np.float64)
    if len(v) % 3 != 0:
        raise RecapError(f"'{p.name}' có số đỉnh {len(v)} không chia hết cho 3.")
    f = np.arange(len(v), dtype=np.int64).reshape(-1, 3)
    return v, f


def _load_ply(p: Path) -> Tuple[np.ndarray, np.ndarray]:
    with p.open("rb") as fh:
        if fh.readline().strip() != b"ply":
            raise RecapError(f"'{p.name}' không có chữ ký PLY.")
        fmt = ""
        n_vert = n_face = 0
        current = None
        vert_props: List[str] = []
        while True:
            line = fh.readline()
            if not line:
                raise RecapError(f"'{p.name}': header PLY không có dòng end_header.")
            tok = line.split()
            if not tok:
                continue
            if tok[0] == b"format":
                fmt = tok[1].decode()
            elif tok[0] == b"element":
                current = tok[1].decode()
                if current == "vertex":
                    n_vert = int(tok[2])
                elif current == "face":
                    n_face = int(tok[2])
            elif tok[0] == b"property" and current == "vertex" and tok[1] != b"list":
                vert_props.append(tok[2].decode())
            elif tok[0] == b"end_header":
                break
        if fmt not in ("ascii", "binary_little_endian"):
            raise RecapError(f"'{p.name}': định dạng PLY '{fmt}' chưa hỗ trợ.")

        if fmt == "ascii":
            verts = [tuple(map(float, fh.readline().split()[:3])) for _ in range(n_vert)]
            faces: List[Tuple[int, int, int]] = []
            for _ in range(n_face):
                parts = list(map(int, fh.readline().split()))
                if parts and parts[0] >= 3:
                    faces.extend(_triangulate(parts[1: 1 + parts[0]]))
            return np.asarray(verts, dtype=np.float64), np.asarray(faces, dtype=np.int64)

        # binary_little_endian: chỉ hỗ trợ trường hợp phổ biến float32 cho x/y/z
        stride = len(vert_props) * 4
        raw = fh.read(n_vert * stride)
        arr = np.frombuffer(raw, dtype="<f4").reshape(n_vert, len(vert_props))
        ix = [vert_props.index(c) for c in ("x", "y", "z")]
        verts_arr = arr[:, ix].astype(np.float64)
        faces = []
        for _ in range(n_face):
            cnt = int(np.frombuffer(fh.read(1), dtype="<u1")[0])
            idx = np.frombuffer(fh.read(cnt * 4), dtype="<i4").tolist()
            if cnt >= 3:
                faces.extend(_triangulate(idx))
        return verts_arr, np.asarray(faces, dtype=np.int64)


# ============================================================================
# Khoảng cách điểm tới tam giác
# ============================================================================

def _closest_point_on_triangles(p: np.ndarray, a: np.ndarray, b: np.ndarray,
                                c: np.ndarray) -> np.ndarray:
    """
    Điểm gần nhất trên tam giác, thuật toán Ericson, vector hoá hoàn toàn.

    Xử lý đủ 7 vùng Voronoi (3 đỉnh, 3 cạnh, 1 mặt trong) — bỏ sót vùng nào sẽ
    cho sai số âm thầm ở đúng những chỗ mép mô hình, nơi Scan-to-BIM quan tâm nhất.
    """
    ab, ac = b - a, c - a
    ap = p - a
    d1 = np.einsum("ij,ij->i", ab, ap)
    d2 = np.einsum("ij,ij->i", ac, ap)
    bp = p - b
    d3 = np.einsum("ij,ij->i", ab, bp)
    d4 = np.einsum("ij,ij->i", ac, bp)
    cp = p - c
    d5 = np.einsum("ij,ij->i", ab, cp)
    d6 = np.einsum("ij,ij->i", ac, cp)

    result = np.empty_like(p)
    done = np.zeros(len(p), dtype=bool)

    def assign(mask: np.ndarray, value: np.ndarray) -> None:
        m = mask & ~done
        if m.any():
            result[m] = value[m] if value.ndim == 2 else value
            done[m] = True

    assign((d1 <= 0) & (d2 <= 0), a)                                   # đỉnh A
    assign((d3 >= 0) & (d4 <= d3), b)                                  # đỉnh B
    assign((d6 >= 0) & (d5 <= d6), c)                                  # đỉnh C

    vc = d1 * d4 - d3 * d2
    with np.errstate(divide="ignore", invalid="ignore"):
        t = np.where((d1 - d3) != 0, d1 / (d1 - d3), 0.0)
    assign((vc <= 0) & (d1 >= 0) & (d3 <= 0), a + ab * t[:, None])     # cạnh AB

    vb = d5 * d2 - d1 * d6
    with np.errstate(divide="ignore", invalid="ignore"):
        t = np.where((d2 - d6) != 0, d2 / (d2 - d6), 0.0)
    assign((vb <= 0) & (d2 >= 0) & (d6 <= 0), a + ac * t[:, None])     # cạnh AC

    va = d3 * d6 - d5 * d4
    denom_bc = (d4 - d3) + (d5 - d6)
    with np.errstate(divide="ignore", invalid="ignore"):
        t = np.where(denom_bc != 0, (d4 - d3) / denom_bc, 0.0)
    assign((va <= 0) & ((d4 - d3) >= 0) & ((d5 - d6) >= 0),
           b + (c - b) * t[:, None])                                    # cạnh BC

    denom = va + vb + vc
    with np.errstate(divide="ignore", invalid="ignore"):
        inv = np.where(denom != 0, 1.0 / denom, 0.0)
    v, w = vb * inv, vc * inv
    assign(np.ones(len(p), dtype=bool), a + ab * v[:, None] + ac * w[:, None])   # mặt trong
    return result


def point_to_mesh(points: np.ndarray, verts: np.ndarray, faces: np.ndarray,
                  k: int = 8, signed: bool = True) -> Dict[str, np.ndarray]:
    """
    Khoảng cách từ mỗi điểm tới lưới tam giác gần nhất.

    Cách làm: dựng KD-tree trên TRỌNG TÂM tam giác, lấy ``k`` ứng viên gần nhất
    rồi tính khoảng cách chính xác tới từng ứng viên.

    Đây là phép xấp xỉ, và nó có một CHỨNG CHỈ ĐÚNG đi kèm: gọi ``d`` là khoảng
    cách nhỏ nhất tìm được, ``r_k`` là khoảng cách tới trọng tâm thứ k, và ``R``
    là bán kính ngoại tiếp lớn nhất trong lưới. Nếu ``r_k >= d + R`` thì không
    tam giác nào ngoài tập ứng viên có thể gần hơn, nên kết quả là CHÍNH XÁC.
    Số điểm không thoả bất đẳng thức đó được đếm và trả về trong ``uncertain`` —
    một xấp xỉ không tự kiểm tra được thì không dùng cho nghiên cứu được.
    """
    _require_scipy()
    if len(points) == 0:
        return {"distance": np.empty(0), "signed": np.empty(0),
                "face_index": np.empty(0, dtype=np.int64), "uncertain": np.empty(0, dtype=bool)}

    tri = verts[faces]                                    # (F, 3, 3)
    centroids = tri.mean(axis=1)
    circum = np.linalg.norm(tri - centroids[:, None, :], axis=2).max()
    tree = cKDTree(centroids)
    k_eff = int(min(max(k, 1), len(faces)))

    n_pts = len(points)
    best = np.full(n_pts, np.inf)
    best_face = np.zeros(n_pts, dtype=np.int64)
    best_closest = np.zeros((n_pts, 3))
    r_k = np.zeros(n_pts)

    for start in range(0, n_pts, CHUNK):
        sl = slice(start, min(start + CHUNK, n_pts))
        pts = points[sl]
        dist_c, idx_c = tree.query(pts, k=k_eff)
        if k_eff == 1:
            dist_c = dist_c[:, None]
            idx_c = idx_c[:, None]
        r_k[sl] = dist_c[:, -1]

        m = len(pts)
        loc_best = np.full(m, np.inf)
        loc_face = np.zeros(m, dtype=np.int64)
        loc_closest = np.zeros((m, 3))
        for j in range(k_eff):
            fi = idx_c[:, j]
            t = tri[fi]
            cp = _closest_point_on_triangles(pts, t[:, 0], t[:, 1], t[:, 2])
            d = np.linalg.norm(pts - cp, axis=1)
            better = d < loc_best
            loc_best[better] = d[better]
            loc_face[better] = fi[better]
            loc_closest[better] = cp[better]
        best[sl] = loc_best
        best_face[sl] = loc_face
        best_closest[sl] = loc_closest

    if k_eff >= len(faces):
        # Đã duyệt TOÀN BỘ tam giác của lưới, nên kết quả đúng theo vét cạn —
        # bất đẳng thức bán kính không còn ý nghĩa gì ở đây.
        uncertain = np.zeros(n_pts, dtype=bool)
    else:
        uncertain = r_k < (best + circum)

    signed_dist = best
    if signed:
        t = verts[faces[best_face]]
        n = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
        norm = np.linalg.norm(n, axis=1)
        norm[norm == 0] = 1.0
        n /= norm[:, None]
        sign = np.sign(np.einsum("ij,ij->i", points - best_closest, n))
        sign[sign == 0] = 1.0
        signed_dist = best * sign

    return {
        "distance": best,
        "signed": signed_dist,
        "face_index": best_face,
        "closest": best_closest,
        "uncertain": uncertain,
        "max_circumradius": float(circum),
        "exhaustive": bool(k_eff >= len(faces)),
    }


def boxes_separation(a_min: np.ndarray, a_max: np.ndarray,
                     b_min: np.ndarray, b_max: np.ndarray) -> Dict[str, Any]:
    """
    Đo khoảng hở giữa hai hộp bao, để phát hiện lệch hệ toạ độ.

    Không dùng phép kiểm tra giao nhau thuần tuý: một lưới BIM phẳng (mặt tường,
    bản mặt cầu) có bề dày ĐÚNG BẰNG 0 theo phương pháp tuyến, nên đám mây thực
    đo — vốn luôn lệch khỏi thiết kế vài milimét — sẽ không bao giờ "giao" với nó.
    Phép kiểm tra đúng là so khoảng hở với KÍCH THƯỚC của dữ liệu: hở vài centimét
    trên công trình dài 100 m là sai số thi công, hở 1000 m là sai hệ toạ độ.
    """
    gap = np.maximum(np.maximum(a_min - b_max, b_min - a_max), 0.0)
    max_gap = float(gap.max())
    scale = float(max(np.linalg.norm(a_max - a_min), np.linalg.norm(b_max - b_min), 1e-9))
    ratio = max_gap / scale
    return {
        "gap_per_axis": [round(float(v), 6) for v in gap],
        "max_gap": round(max_gap, 6),
        "data_scale": round(scale, 6),
        "gap_ratio": round(ratio, 6),
        "aligned": ratio <= 0.1,
    }


# ============================================================================
# Khớp mặt phẳng
# ============================================================================

def fit_plane(points: np.ndarray) -> Dict[str, Any]:
    """Khớp mặt phẳng bình phương tối thiểu bằng PCA. Trả về pháp tuyến và độ lệch."""
    if len(points) < 3:
        raise RecapError(f"Cần ít nhất 3 điểm để khớp mặt phẳng, chỉ có {len(points)}.")
    centroid = points.mean(axis=0)
    u, s, vt = np.linalg.svd(points - centroid, full_matrices=False)
    normal = vt[2]
    if normal[2] < 0:
        normal = -normal                       # quy ước hướng lên, để so sánh được giữa các lần chạy
    residual = (points - centroid) @ normal

    planarity = float(s[2] / s[0]) if s[0] > 0 else 0.0
    tilt = math.degrees(math.acos(min(1.0, abs(float(normal[2])))))
    return {
        "centroid": [float(v) for v in centroid],
        "normal": [float(v) for v in normal],
        "plane_d": float(-normal @ centroid),
        "residuals": residual,
        "planarity_ratio": round(planarity, 6),
        "tilt_from_horizontal_deg": round(tilt, 4),
        "tilt_from_vertical_deg": round(90.0 - tilt, 4),
        "singular_values": [float(v) for v in s],
    }


# ============================================================================
# Mặt cắt
# ============================================================================

def slab_section(points: np.ndarray, origin: List[float], normal: List[float],
                 half_thickness: float) -> Dict[str, Any]:
    """
    Cắt một lát mỏng bằng mặt phẳng và chiếu về toạ độ 2D trong mặt phẳng đó.

    Trục trong mặt phẳng được chọn tất định: ``u`` là hướng nằm ngang vuông góc
    với pháp tuyến, ``v`` là hướng còn lại. Với mặt cắt ngang thẳng đứng, ``u``
    là khoảng cách ngang (offset) và ``v`` gần trùng cao độ — đúng quy ước đọc
    bản vẽ mặt cắt công trình giao thông.
    """
    n = np.asarray(normal, dtype=float)
    ln = np.linalg.norm(n)
    if ln == 0:
        raise RecapError("Vector pháp tuyến không được bằng 0.")
    n /= ln
    o = np.asarray(origin, dtype=float)
    if half_thickness <= 0:
        raise RecapError("half_thickness phải lớn hơn 0.")

    signed = (points - o) @ n
    mask = np.abs(signed) <= half_thickness
    sel = points[mask]

    up = np.array([0.0, 0.0, 1.0])
    if abs(n @ up) > 0.99:                      # mặt cắt gần nằm ngang: đổi trục tham chiếu
        up = np.array([0.0, 1.0, 0.0])
    u = np.cross(up, n)
    u /= np.linalg.norm(u)
    v = np.cross(n, u)

    rel = sel - o
    return {
        "points": sel,
        "u": rel @ u,
        "v": rel @ v,
        "offset_from_plane": signed[mask],
        "axis_u": [float(x) for x in u],
        "axis_v": [float(x) for x in v],
        "normal": [float(x) for x in n],
        "origin": [float(x) for x in o],
        "selected": int(mask.sum()),
        "total": int(len(points)),
    }


# ============================================================================
# Lưới cao độ
# ============================================================================

def elevation_grid(points: np.ndarray, cell_size: float,
                   statistic: str = "mean") -> Dict[str, Any]:
    """Gộp điểm vào lưới ô vuông theo mặt bằng và tổng hợp cao độ từng ô."""
    if cell_size <= 0:
        raise RecapError("cell_size phải lớn hơn 0.")
    if len(points) == 0:
        raise RecapError("Không có điểm nào để dựng lưới.")
    allowed = {"mean", "min", "max", "count", "std", "range"}
    if statistic not in allowed:
        raise RecapError(f"statistic '{statistic}' không hợp lệ. Chọn: {', '.join(sorted(allowed))}.")

    mn = points[:, :2].min(axis=0)
    ix = np.floor((points[:, 0] - mn[0]) / cell_size).astype(np.int64)
    iy = np.floor((points[:, 1] - mn[1]) / cell_size).astype(np.int64)
    nx, ny = int(ix.max()) + 1, int(iy.max()) + 1
    if nx * ny > 4_000_000:
        raise RecapError(
            f"cell_size={cell_size} tạo ra lưới {nx}x{ny} ô (quá lớn). Hãy tăng cell_size "
            "hoặc thu hẹp bbox."
        )

    flat = iy * nx + ix
    order = np.argsort(flat, kind="stable")
    flat_s, z_s = flat[order], points[order, 2]
    uniq, starts = np.unique(flat_s, return_index=True)
    splits = np.split(z_s, starts[1:])

    cells = []
    for code, zs in zip(uniq, splits):
        cy, cx = divmod(int(code), nx)
        if statistic == "mean":
            val = float(zs.mean())
        elif statistic == "min":
            val = float(zs.min())
        elif statistic == "max":
            val = float(zs.max())
        elif statistic == "count":
            val = int(zs.size)
        elif statistic == "std":
            val = float(zs.std(ddof=1)) if zs.size > 1 else 0.0
        else:
            val = float(zs.max() - zs.min())
        cells.append({
            "x": round(float(mn[0] + (cx + 0.5) * cell_size), 6),
            "y": round(float(mn[1] + (cy + 0.5) * cell_size), 6),
            "value": val,
            "count": int(zs.size),
        })
    return {"cells": cells, "grid_size": [nx, ny], "cell_size": cell_size,
            "statistic": statistic, "origin": [float(mn[0]), float(mn[1])]}


# ============================================================================
# Ghi kết quả
# ============================================================================

def write_csv(path: str | Path, header: List[str], rows) -> Dict[str, Any]:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with p.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        for row in rows:
            w.writerow(row)
            n += 1
    return {"file": str(p), "rows": n, "size_bytes": p.stat().st_size}


def write_las(path: str | Path, points: np.ndarray, source_header=None,
              extra: Optional[Dict[str, np.ndarray]] = None) -> Dict[str, Any]:
    """Ghi tập điểm ra file LAS/LAZ mới, giữ nguyên scale/offset của file nguồn."""
    _require_laspy()
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if source_header is not None:
        header = laspy.LasHeader(version=source_header.version,
                                 point_format=source_header.point_format)
        header.scales = source_header.scales
        header.offsets = source_header.offsets
    else:
        header = laspy.LasHeader(version="1.4", point_format=3)
        header.scales = np.array([0.001, 0.001, 0.001])
        header.offsets = points.min(axis=0) if len(points) else np.zeros(3)

    las = laspy.LasData(header)
    las.x, las.y, las.z = points[:, 0], points[:, 1], points[:, 2]
    for name, values in (extra or {}).items():
        try:
            setattr(las, name, values)
        except (ValueError, AttributeError):
            pass                                  # định dạng điểm không có trường này
    las.write(str(p))
    return {"file": str(p), "point_count": int(len(points)), "size_bytes": p.stat().st_size}
