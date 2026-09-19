# -*- coding: utf-8 -*-
"""
Điều khiển ReCap ở chế độ headless qua ``decap.exe``.
=====================================================

``decap.exe`` nằm ngay cạnh ``ReCap.exe`` trong thư mục cài đặt và là engine
dòng lệnh đầy đủ: import scan, lập chỉ mục, giảm mật độ (decimation), cắt theo
khoảng cách, gộp scan, chuẩn hoá cường độ, đổi hệ toạ độ. Nó KHÔNG cần mở giao
diện ReCap, và dùng đúng license đang cài qua cờ ``--importWithLicense``.

Import point cloud là việc chạy hàng phút tới hàng giờ. Vì vậy mọi lệnh ở đây
chạy NỀN: tool trả về ngay một ``job_id``, toàn bộ stdout/stderr được ghi thẳng
ra file log, và tiến trình được hỏi lại bằng ``job_status``. Không bao giờ chặn
phiên làm việc, và không bao giờ cắt bớt output — cắt output là mất luôn khả
năng chẩn đoán khi job hỏng giữa chừng.
"""

from __future__ import annotations

import ctypes
import os
import re
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from .rcp import RecapError

# Thư mục cài đặt có thể khác nhau giữa các bản; dò theo thứ tự rồi mới tới PATH.
_SEARCH_ROOTS = [
    Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Autodesk",
    Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Autodesk",
]

# Bảng mã đơn vị của cờ --inputUnitType, lấy nguyên văn từ `decap.exe --help`.
UNIT_TYPES = {"mm": 0, "cm": 1, "m": 2, "in": 3, "ft": 4, "usft": 5}

# Định dạng scan ReCap nhận vào (theo tài liệu sản phẩm và codec .rcip đi kèm).
SUPPORTED_INPUT_EXT = {
    ".las", ".laz", ".e57", ".pts", ".ptx", ".ptg", ".xyz", ".rcs", ".rcp",
    ".fls", ".fws", ".zfs", ".zfprj", ".xyb", ".cl3", ".clr", ".txt", ".asc",
    ".pcg", ".isproj", ".cwf", ".rds", ".3dd", ".dp",
}


def find_recap_install() -> Dict[str, Any]:
    """Tìm thư mục cài ReCap và hai executable quan trọng."""
    override = os.environ.get("RECAP_HOME")
    candidates: List[Path] = [Path(override)] if override else []
    for root in _SEARCH_ROOTS:
        if root.is_dir():
            candidates.extend(sorted(c for c in root.iterdir()
                                     if c.is_dir() and "recap" in c.name.lower()))

    for folder in candidates:
        decap = folder / "decap.exe"
        recap = folder / "ReCap.exe"
        if decap.is_file():
            return {
                "found": True,
                "install_dir": str(folder),
                "decap_exe": str(decap),
                "recap_exe": str(recap) if recap.is_file() else None,
                "version": _file_version(recap if recap.is_file() else decap),
            }
    return {
        "found": False,
        "install_dir": None,
        "decap_exe": None,
        "recap_exe": None,
        "version": None,
        "searched": [str(c) for c in candidates] or [str(r) for r in _SEARCH_ROOTS],
    }


class _VSFixedFileInfo(ctypes.Structure):
    """Cấu trúc VS_FIXEDFILEINFO của Windows — nơi chứa số hiệu phiên bản file."""
    _fields_ = [
        ("dwSignature", ctypes.c_uint32), ("dwStrucVersion", ctypes.c_uint32),
        ("dwFileVersionMS", ctypes.c_uint32), ("dwFileVersionLS", ctypes.c_uint32),
        ("dwProductVersionMS", ctypes.c_uint32), ("dwProductVersionLS", ctypes.c_uint32),
        ("dwFileFlagsMask", ctypes.c_uint32), ("dwFileFlags", ctypes.c_uint32),
        ("dwFileOS", ctypes.c_uint32), ("dwFileType", ctypes.c_uint32),
        ("dwFileSubtype", ctypes.c_uint32),
        ("dwFileDateMS", ctypes.c_uint32), ("dwFileDateLS", ctypes.c_uint32),
    ]


def _file_version(exe: Path) -> Optional[str]:
    """
    Đọc FileVersion của một file .exe bằng API version.dll của Windows.

    Cố tình KHÔNG gọi PowerShell: MCP client có thể khởi chạy server với PATH đã
    rút gọn, khi đó powershell.exe không tìm thấy và số hiệu phiên bản im lặng
    trở thành None. Gọi thẳng API hệ điều hành thì không phụ thuộc PATH, và cũng
    không tốn thời gian khởi tạo một tiến trình mới.
    """
    try:
        ver = ctypes.WinDLL("version.dll")
    except (OSError, AttributeError):
        return None                               # không phải Windows

    path = str(exe)
    ver.GetFileVersionInfoSizeW.restype = ctypes.c_uint32
    size = ver.GetFileVersionInfoSizeW(ctypes.c_wchar_p(path), None)
    if not size:
        return None

    buf = ctypes.create_string_buffer(size)
    if not ver.GetFileVersionInfoW(ctypes.c_wchar_p(path), 0, size, buf):
        return None

    block = ctypes.c_void_p()
    length = ctypes.c_uint32()
    if not ver.VerQueryValueW(buf, ctypes.c_wchar_p("\\"),
                              ctypes.byref(block), ctypes.byref(length)):
        return None
    if not block or length.value < ctypes.sizeof(_VSFixedFileInfo):
        return None

    info = ctypes.cast(block, ctypes.POINTER(_VSFixedFileInfo)).contents
    if info.dwSignature != 0xFEEF04BD:
        return None
    ms, ls = info.dwFileVersionMS, info.dwFileVersionLS
    return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"


def decap_path() -> Path:
    info = find_recap_install()
    if not info["found"]:
        raise RecapError(
            "Không tìm thấy decap.exe. Kiểm tra Autodesk ReCap đã cài chưa, hoặc đặt "
            "biến môi trường RECAP_HOME trỏ tới thư mục chứa decap.exe. "
            f"Đã tìm ở: {', '.join(info.get('searched', []))}"
        )
    return Path(info["decap_exe"])


# ============================================================================
# Chạy đồng bộ (chỉ cho lệnh ngắn)
# ============================================================================

def run_sync(args: List[str], timeout: int = 120) -> Dict[str, Any]:
    """Chạy decap.exe và chờ kết quả. Chỉ dùng cho --version / --checkLicense."""
    exe = decap_path()
    try:
        proc = subprocess.run([str(exe), *args], capture_output=True, text=True,
                              timeout=timeout, cwd=str(exe.parent))
    except subprocess.TimeoutExpired:
        raise RecapError(
            f"decap.exe không phản hồi sau {timeout}s với lệnh '{' '.join(args)}'. "
            "Nếu đây là lệnh import, hãy dùng tool chạy nền thay vì lệnh đồng bộ."
        ) from None
    output = (proc.stdout or "") + (proc.stderr or "")
    return {
        "command": [str(exe), *args],
        "returncode": proc.returncode,
        "output": output.strip(),
        "messages": _parse_messages(output),
    }


_MSG_RE = re.compile(r"\[([A-Z])\]\[(\d+)\]\[([^\]]*)\]")


def _parse_messages(text: str) -> List[Dict[str, str]]:
    """decap.exe in thông điệp dạng ``[M][2049][License Check OK]`` — bóc ra cho dễ đọc."""
    return [{"severity": s, "code": c, "message": m} for s, c, m in _MSG_RE.findall(text or "")]


# ============================================================================
# Chạy nền có theo dõi
# ============================================================================

class _Job:
    __slots__ = ("id", "command", "log_path", "started_at", "finished_at",
                 "returncode", "process", "kind", "expected_output")

    def __init__(self, job_id: str, command: List[str], log_path: Path, kind: str,
                 expected_output: Optional[str]):
        self.id = job_id
        self.command = command
        self.log_path = log_path
        self.kind = kind
        self.expected_output = expected_output
        self.started_at = time.time()
        self.finished_at: Optional[float] = None
        self.returncode: Optional[int] = None
        self.process: Optional[subprocess.Popen] = None


_JOBS: Dict[str, _Job] = {}
_JOBS_LOCK = threading.Lock()


def _log_dir() -> Path:
    d = Path(os.environ.get("RECAP_MCP_LOG_DIR",
                            Path(os.environ.get("TEMP", ".")) / "recap-mcp-jobs"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def run_async(args: List[str], kind: str, expected_output: Optional[str] = None) -> Dict[str, Any]:
    """
    Khởi chạy decap.exe ở chế độ nền và trả về ngay.

    Toàn bộ output được ghi thẳng ra file log, KHÔNG qua pipe có giới hạn —
    một job import chạy nhiều giờ mà output bị cắt thì khi hỏng sẽ không còn
    gì để lần ra nguyên nhân.
    """
    exe = decap_path()
    job_id = uuid.uuid4().hex[:12]
    log_path = _log_dir() / f"{kind}-{job_id}.log"
    command = [str(exe), *args]

    job = _Job(job_id, command, log_path, kind, expected_output)
    log_handle = log_path.open("w", encoding="utf-8", errors="replace")
    log_handle.write("# " + " ".join(command) + "\n\n")
    log_handle.flush()

    try:
        job.process = subprocess.Popen(
            command, stdout=log_handle, stderr=subprocess.STDOUT,
            cwd=str(exe.parent), text=True,
        )
    except OSError as exc:
        log_handle.close()
        raise RecapError(f"Không khởi chạy được decap.exe: {exc}") from exc

    def _reap(j: _Job, handle) -> None:
        j.process.wait()
        j.returncode = j.process.returncode
        j.finished_at = time.time()
        try:
            handle.close()
        except OSError:
            pass

    threading.Thread(target=_reap, args=(job, log_handle), daemon=True).start()

    with _JOBS_LOCK:
        _JOBS[job_id] = job

    return {
        "job_id": job_id,
        "kind": kind,
        "command": command,
        "log_file": str(log_path),
        "status": "running",
        "hint": f"Hỏi tiến độ bằng get_job_status('{job_id}').",
    }


def job_status(job_id: str, tail_lines: int = 25) -> Dict[str, Any]:
    """Tra trạng thái một job, kèm phần đuôi log để biết nó đang làm gì."""
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
    if job is None:
        known = sorted(_JOBS)
        raise RecapError(
            f"Không có job nào tên '{job_id}'. "
            + (f"Các job đã biết: {', '.join(known)}" if known
               else "Chưa job nào được khởi chạy trong phiên này.")
        )

    running = job.returncode is None
    elapsed = (job.finished_at or time.time()) - job.started_at
    try:
        lines = job.log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        lines = []

    result: Dict[str, Any] = {
        "job_id": job.id,
        "kind": job.kind,
        "status": "running" if running else ("done" if job.returncode == 0 else "failed"),
        "returncode": job.returncode,
        "elapsed_seconds": round(elapsed, 1),
        "log_file": str(job.log_path),
        "log_lines": len(lines),
        "log_tail": lines[-tail_lines:] if tail_lines else [],
        "messages": _parse_messages("\n".join(lines)),
    }

    # Một job kết thúc với mã 0 vẫn chưa chắc đã tạo ra file — luôn kiểm tra hiện vật.
    if job.expected_output:
        out = Path(job.expected_output)
        result["expected_output"] = str(out)
        result["output_exists"] = out.is_file()
        result["output_size_bytes"] = out.stat().st_size if out.is_file() else 0
        if not running and job.returncode == 0 and not out.is_file():
            result["status"] = "failed"
            result["warning"] = (
                "decap.exe báo thành công nhưng file project không tồn tại. "
                "Đọc log_tail để biết lý do — thường là license, đường dẫn, hoặc "
                "định dạng scan đầu vào không đọc được."
            )
    return result


def list_jobs() -> List[Dict[str, Any]]:
    with _JOBS_LOCK:
        jobs = list(_JOBS.values())
    return [{
        "job_id": j.id,
        "kind": j.kind,
        "status": "running" if j.returncode is None else ("done" if j.returncode == 0 else "failed"),
        "elapsed_seconds": round((j.finished_at or time.time()) - j.started_at, 1),
        "log_file": str(j.log_path),
    } for j in sorted(jobs, key=lambda x: x.started_at, reverse=True)]


def cancel_job(job_id: str) -> Dict[str, Any]:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
    if job is None:
        raise RecapError(f"Không có job nào tên '{job_id}'.")
    if job.returncode is not None:
        return {"job_id": job_id, "status": "đã kết thúc từ trước", "returncode": job.returncode}
    job.process.terminate()
    return {
        "job_id": job_id,
        "status": "đã gửi tín hiệu dừng",
        "warning": (
            "Dừng giữa chừng để lại project dở dang trong thư mục đích. Hãy xoá thủ "
            "công thư mục '<tên project> Support' trước khi import lại."
        ),
    }


# ============================================================================
# Dựng lệnh import
# ============================================================================

def build_import_args(
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
) -> Dict[str, Any]:
    """
    Dựng danh sách tham số cho ``decap.exe --importWithLicense`` và kiểm tra đầu vào.

    Mọi lỗi tham số được bắt Ở ĐÂY chứ không để decap.exe phát hiện: một job import
    chỉ báo lỗi sau khi đã đọc xong vài chục GB scan là phản hồi quá muộn.
    """
    out_dir = Path(output_folder)
    if not out_dir.is_dir():
        raise RecapError(f"Thư mục đích không tồn tại: {out_dir}")
    if not project_name or any(c in project_name for c in '\\/:*?"<>|'):
        raise RecapError(
            f"Tên project không hợp lệ: '{project_name}'. Không được rỗng và không "
            'được chứa ký tự \\ / : * ? " < > |'
        )

    if bool(scans) == bool(control_file):
        raise RecapError("Phải cung cấp ĐÚNG MỘT trong hai: danh sách 'scans' hoặc 'control_file'.")

    target_rcp = out_dir / f"{project_name}.rcp"
    if target_rcp.exists():
        raise RecapError(
            f"Đã có project '{target_rcp}'. decap.exe sẽ không ghi đè an toàn — "
            "hãy đổi project_name hoặc xoá project cũ (cả file .rcp và thư mục "
            f"'{project_name} Support') trước."
        )

    args: List[str] = ["--importWithLicense", str(out_dir), project_name]

    if decimation_mm is not None:
        if decimation_mm <= 0:
            raise RecapError("decimation_mm phải lớn hơn 0 (đơn vị: milimét).")
        args += ["--decimation", str(decimation_mm)]
    if min_range is not None:
        args += ["--minRangeClipping", str(min_range)]
    if max_range is not None:
        args += ["--maxRangeClipping", str(max_range)]
    if min_range is not None and max_range is not None and min_range >= max_range:
        raise RecapError(f"min_range ({min_range}) phải nhỏ hơn max_range ({max_range}).")
    if unify:
        args.append("--unify")
    if normalize_intensity is not None:
        args += ["--normalizeIntensity", "1" if normalize_intensity else "0"]
    if input_unit is not None:
        key = str(input_unit).strip().lower()
        if key not in UNIT_TYPES:
            raise RecapError(
                f"input_unit '{input_unit}' không hợp lệ. Chọn một trong: "
                f"{', '.join(UNIT_TYPES)}."
            )
        args += ["--inputUnitType", str(UNIT_TYPES[key])]
    if current_coordinate_system:
        args += ["--currentCoordinateSystem", current_coordinate_system]
    if target_coordinate_system:
        args += ["--targetCoordinateSystem", target_coordinate_system]
    if target_coordinate_system and not current_coordinate_system:
        raise RecapError(
            "Đặt target_coordinate_system mà không đặt current_coordinate_system thì "
            "ReCap không biết dữ liệu gốc đang ở hệ nào để quy đổi."
        )
    if e57_import_images:
        args.append("--e57EnableImageImport")
    if e57_scan_per_image:
        args.append("--e57CreateScanPerImage")
    if e57_match_image_resolution:
        args.append("--e57MatchImageResolution")

    warnings: List[str] = []
    if control_file:
        cf = Path(control_file)
        if not cf.is_file():
            raise RecapError(f"Không tìm thấy control file: {cf}")
        args += ["--controlFile", str(cf)]
        resolved: List[str] = []
    else:
        resolved = []
        missing = []
        for s in scans or []:
            sp = Path(s)
            if not sp.is_file():
                missing.append(str(sp))
                continue
            if sp.suffix.lower() not in SUPPORTED_INPUT_EXT:
                warnings.append(
                    f"'{sp.name}' có đuôi lạ ({sp.suffix}) — ReCap có thể từ chối."
                )
            resolved.append(str(sp.resolve()))
        if missing:
            raise RecapError("Không tìm thấy các file scan: " + "; ".join(missing))
        if not resolved:
            raise RecapError("Danh sách 'scans' rỗng.")
        args += resolved

    if (e57_import_images or e57_scan_per_image or e57_match_image_resolution) and resolved:
        if not any(Path(s).suffix.lower() == ".e57" for s in resolved):
            warnings.append("Đã bật cờ E57 nhưng không có file .e57 nào trong danh sách.")

    total_bytes = sum(Path(s).stat().st_size for s in resolved) if resolved else 0
    return {
        "args": args,
        "expected_output": str(target_rcp),
        "scan_count": len(resolved),
        "input_bytes": total_bytes,
        "warnings": warnings,
    }
