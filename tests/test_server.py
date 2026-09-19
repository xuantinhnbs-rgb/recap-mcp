# -*- coding: utf-8 -*-
"""
Kiểm thử lớp tool MCP, gồm một kịch bản Scan-to-BIM đầu-cuối có đáp số biết trước.

Kịch bản dựng một mặt tường chắn "thiết kế" (lưới BIM) và một đám mây điểm
"thực đo" lệch khỏi thiết kế theo một quy luật tính được bằng tay. Nhờ vậy mọi
con số mà compare_to_bim_mesh trả về đều đối chiếu được với giá trị lý thuyết —
kể cả tỉ lệ phần trăm điểm trong dung sai.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from recap_mcp import server as srv

SAMPLE = Path(r"C:\ProgramData\Autodesk\Autodesk ReCap\Sample\AutodeskReCapSampleProject.rcp")


def write_las(path: Path, points: np.ndarray) -> None:
    import laspy
    header = laspy.LasHeader(version="1.4", point_format=3)
    header.scales = np.array([0.0001, 0.0001, 0.0001])
    header.offsets = points.min(axis=0)
    las = laspy.LasData(header)
    las.x, las.y, las.z = points[:, 0], points[:, 1], points[:, 2]
    las.write(str(path))


# Mặt tường chắn thiết kế: mặt phẳng x = 0, dài 20 m theo y, cao 6 m theo z.
# Thứ tự đỉnh chọn sao cho pháp tuyến hướng +x (ra phía ngoài tường).
WALL_OBJ = """v 0 0 0
v 0 20 0
v 0 20 6
v 0 0 6
f 1 2 3
f 1 3 4
"""


class TestToolContract(unittest.TestCase):
    """Mọi tool phải trả về dict có khoá 'ok', kể cả khi đầu vào sai."""

    def test_success_shape(self):
        r = srv.check_recap_installation()
        self.assertIn("ok", r)
        self.assertIs(type(r), dict)

    def test_errors_never_raise(self):
        cases = [
            lambda: srv.read_project(r"C:\khong-ton-tai.rcp"),
            lambda: srv.read_rcs_header(r"C:\khong-ton-tai.rcs"),
            lambda: srv.inspect_point_cloud(r"C:\khong-ton-tai.las"),
            lambda: srv.list_scans(r"C:\khong-ton-tai.rcp"),
            lambda: srv.find_projects(r"C:\thu-muc-khong-ton-tai"),
            lambda: srv.get_job_status("khong-co-job-nay"),
            lambda: srv.cancel_job("khong-co-job-nay"),
            lambda: srv.import_scans("C:\\", "x", scans=[], control_file="y"),
            lambda: srv.voxel_downsample("a.las", "b.las", voxel_size=-1),
        ]
        for i, call in enumerate(cases):
            with self.subTest(case=i):
                r = call()
                self.assertFalse(r["ok"], "Đầu vào sai phải cho ok=False")
                self.assertTrue(r["error"].strip(), "Thông điệp lỗi không được rỗng")

    def test_import_validates_before_launching(self):
        """Tham số sai phải bị chặn NGAY, không được để decap.exe phát hiện sau vài giờ."""
        with tempfile.TemporaryDirectory() as d:
            r = srv.import_scans(d, "du/an", scans=[r"C:\a.las"])
            self.assertFalse(r["ok"])
            self.assertIn("Tên project", r["error"])

            r = srv.import_scans(d, "duan", scans=[r"C:\khong-ton-tai.las"])
            self.assertFalse(r["ok"])
            self.assertIn("Không tìm thấy các file scan", r["error"])

            r = srv.import_scans(d, "duan", scans=[r"C:\a.las"], target_coordinate_system="EPSG:4326")
            self.assertFalse(r["ok"])
            self.assertIn("current_coordinate_system", r["error"])


@unittest.skipUnless(SAMPLE.is_file(), "Không có project mẫu của ReCap.")
class TestProjectTools(unittest.TestCase):

    def test_read_project(self):
        r = srv.read_project(str(SAMPLE))
        self.assertTrue(r["ok"])
        self.assertEqual(r["scan_count"], 2)

    def test_list_scans_with_headers(self):
        r = srv.list_scans(str(SAMPLE), include_rcs_header=True)
        self.assertTrue(r["ok"])
        for s in r["scans"]:
            self.assertEqual(s["header_num_points"], s["num_points"],
                             "Header .rcs và manifest phải khớp số điểm")
            self.assertNotIn("point_count_mismatch", s)

    def test_registration_summary(self):
        r = srv.get_scan_registration(str(SAMPLE))
        self.assertTrue(r["ok"])
        self.assertEqual(r["level_summary"].get("FINE_ALIGNED"), 2)
        self.assertEqual(r["unregistered_scans"], [])

    def test_unknown_scan_name_lists_valid_ones(self):
        r = srv.get_scan_registration(str(SAMPLE), scan_name="khong-co")
        self.assertFalse(r["ok"])
        self.assertIn("techshop_012", r["error"])

    def test_measurements_and_regions(self):
        m = srv.get_measurements(str(SAMPLE))
        self.assertTrue(m["ok"])
        self.assertGreater(m["count"], 0)
        g = srv.get_regions(str(SAMPLE))
        self.assertTrue(g["ok"])
        self.assertGreater(g["count"], 0)

    def test_extract_preview(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "preview.jpg"
            r = srv.extract_project_preview(str(SAMPLE), str(out))
            self.assertTrue(r["ok"])
            self.assertTrue(out.is_file())
            self.assertGreater(out.stat().st_size, 0)


class TestScanToBimEndToEnd(unittest.TestCase):
    """
    Tường chắn nghiêng ra ngoài theo quy luật x = 0.015 + 0.002*z.

    Vì mặt thiết kế là x = 0 với pháp tuyến +x, độ lệch có dấu của mỗi điểm đúng
    bằng toạ độ x của nó. Từ đó suy ra được bằng tay:
        trung bình = 0.015 + 0.002 * E[z],  z ~ đều trên [0.5, 5.5] nên E[z] = 3
                   = 0.021
        trong dung sai 0.02  <=>  0.015 + 0.002*z <= 0.02  <=>  z <= 2.5
                   => tỉ lệ = (2.5 - 0.5) / 5 = 40%
    """

    LEAN_BASE = 0.015
    LEAN_RATE = 0.002
    Z_LO, Z_HI = 0.5, 5.5

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = Path(cls.tmp.name)
        cls.mesh = d / "tuong_thiet_ke.obj"
        cls.mesh.write_text(WALL_OBJ, encoding="utf-8")

        rng = np.random.default_rng(2026)
        n = 120_000
        y = rng.uniform(1.0, 19.0, n)
        z = rng.uniform(cls.Z_LO, cls.Z_HI, n)
        x = cls.LEAN_BASE + cls.LEAN_RATE * z
        cls.points = np.column_stack([x, y, z])
        cls.las = d / "tuong_thuc_do.las"
        write_las(cls.las, cls.points)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_deviation_matches_hand_calculation(self):
        r = srv.compare_to_bim_mesh(str(self.las), str(self.mesh), tolerance=0.02)
        self.assertTrue(r["ok"], r.get("error"))
        self.assertTrue(r["bounds_overlap"])
        self.assertNotIn("warning", r)

        dev = r["deviation"]
        expected_mean = self.LEAN_BASE + self.LEAN_RATE * (self.Z_LO + self.Z_HI) / 2
        self.assertAlmostEqual(dev["mean"], expected_mean, delta=1e-4)
        self.assertAlmostEqual(dev["min"], self.LEAN_BASE + self.LEAN_RATE * self.Z_LO,
                               delta=1e-4)
        self.assertAlmostEqual(dev["max"], self.LEAN_BASE + self.LEAN_RATE * self.Z_HI,
                               delta=1e-4)
        self.assertGreater(dev["mean"], 0, "Tường nghiêng ra ngoài phải cho dấu dương")

    def test_within_tolerance_percentage_matches_theory(self):
        r = srv.compare_to_bim_mesh(str(self.las), str(self.mesh), tolerance=0.02)
        z_at_tol = (0.02 - self.LEAN_BASE) / self.LEAN_RATE
        expected_pct = 100.0 * (z_at_tol - self.Z_LO) / (self.Z_HI - self.Z_LO)
        self.assertAlmostEqual(r["deviation"]["within_tolerance_pct"], expected_pct, delta=0.6)

    def test_result_is_certified_exact(self):
        """Với lưới 2 tam giác lớn, thuật toán phải chứng minh được là đã đúng."""
        r = srv.compare_to_bim_mesh(str(self.las), str(self.mesh), candidates=2)
        self.assertEqual(r["uncertain_points"], 0)
        self.assertNotIn("accuracy_warning", r)

    def test_coordinate_system_mismatch_is_flagged_loudly(self):
        """Lệch hệ toạ độ là lỗi phổ biến nhất của Scan-to-BIM — phải báo, không im."""
        shifted = self.points + np.array([1000.0, 0.0, 0.0])
        far = Path(self.tmp.name) / "lech_he_toa_do.las"
        write_las(far, shifted)
        r = srv.compare_to_bim_mesh(str(far), str(self.mesh), tolerance=0.02)
        self.assertTrue(r["ok"])
        self.assertFalse(r["bounds_overlap"])
        self.assertIn("warning", r)
        self.assertIn("hệ toạ độ", r["warning"])

    def test_csv_export_row_count(self):
        out = Path(self.tmp.name) / "do_lech.csv"
        r = srv.compare_to_bim_mesh(str(self.las), str(self.mesh), tolerance=0.02,
                                    every_nth=10, output_csv=str(out))
        self.assertTrue(r["ok"])
        self.assertEqual(r["csv"]["rows"], r["points_compared"])
        self.assertTrue(out.is_file())
        header = out.read_text(encoding="utf-8").splitlines()[0]
        self.assertEqual(header,
                         "x,y,z,signed_deviation,abs_distance,face_index,uncertain")

    def test_fit_plane_recovers_the_lean_angle(self):
        """Khớp mặt phẳng phải cho ra đúng độ nghiêng đã dựng: arctan(0.002)."""
        r = srv.fit_plane_to_region(
            str(self.las),
            bbox={"min": [-1, 0, 0], "max": [1, 20, 6]},
            tolerance=0.002,
        )
        self.assertTrue(r["ok"], r.get("error"))
        expected_deg = np.degrees(np.arctan(self.LEAN_RATE))
        self.assertAlmostEqual(r["plane"]["tilt_from_vertical_deg"], expected_deg, delta=0.01)
        self.assertEqual(r["planarity_verdict"], "phẳng")
        self.assertLess(r["deviation"]["rms"], 1e-3)


class TestSectionAndGridTools(unittest.TestCase):
    """Mặt cắt ngang và lưới cao độ trên một mặt cầu tổng hợp có kích thước biết trước."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        rng = np.random.default_rng(99)
        n = 200_000
        # Bản mặt cầu rộng 12 m, dài 100 m dọc trục x, dốc dọc 2%.
        x = rng.uniform(0, 100, n)
        y = rng.uniform(-6, 6, n)
        z = 10.0 + 0.02 * x
        cls.points = np.column_stack([x, y, z])
        cls.las = Path(cls.tmp.name) / "ban_mat_cau.las"
        write_las(cls.las, cls.points)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_cross_section_width_matches_deck(self):
        r = srv.extract_cross_section(
            str(self.las), alignment_start=[0, 0, 10], alignment_end=[100, 0, 12],
            station=50.0, half_thickness=0.25, lateral_reach=20.0,
        )
        self.assertTrue(r["ok"], r.get("error"))
        # 200k điểm trải đều trên 100 m, lát dày 0,5 m => kỳ vọng 1000 điểm.
        # Đây là quá trình ngẫu nhiên nên độ lệch chuẩn ~ sqrt(1000) ~ 32;
        # khoảng 850..1150 là ±4,7 sigma, chặt vừa đủ mà không hỏng ngẫu nhiên.
        self.assertGreater(r["points_in_section"], 850)
        self.assertLess(r["points_in_section"], 1150)
        self.assertAlmostEqual(r["width"], 12.0, delta=0.1,
                               msg="Bề rộng mặt cắt phải bằng bề rộng bản mặt cầu")

    def test_station_outside_alignment_is_rejected(self):
        r = srv.extract_cross_section(
            str(self.las), alignment_start=[0, 0, 10], alignment_end=[100, 0, 12],
            station=500.0,
        )
        self.assertFalse(r["ok"])
        self.assertIn("ngoài tuyến", r["error"])

    def test_elevation_grid_recovers_longitudinal_slope(self):
        """Lưới cao độ phải tái hiện được độ dốc dọc 2% đã dựng."""
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "luoi.csv"
            r = srv.elevation_grid_report(str(self.las), cell_size=5.0,
                                          statistic="mean", output_csv=str(out))
            self.assertTrue(r["ok"], r.get("error"))
            rows = out.read_text(encoding="utf-8").splitlines()[1:]
            data = np.array([[float(v) for v in row.split(",")[:3]] for row in rows])
            slope = np.polyfit(data[:, 0], data[:, 2], 1)[0]
            self.assertAlmostEqual(slope, 0.02, delta=1e-4)

    def test_voxel_downsample_reduces_and_preserves_extent(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "thua.las"
            r = srv.voxel_downsample(str(self.las), str(out), voxel_size=1.0)
            self.assertTrue(r["ok"], r.get("error"))
            self.assertLess(r["output_points"], r["input_points"])
            self.assertGreater(r["reduction_pct"], 50.0)
            after = srv.inspect_point_cloud(str(out))
            self.assertAlmostEqual(after["bounds"]["max"][0], 100.0, delta=1.5)

    def test_crop_writes_only_requested_box(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "cat.las"
            bbox = {"min": [20.0, -3.0, 0.0], "max": [40.0, 3.0, 100.0]}
            r = srv.crop_point_cloud(str(self.las), str(out), bbox=bbox)
            self.assertTrue(r["ok"], r.get("error"))
            after = srv.inspect_point_cloud(str(out))
            self.assertGreaterEqual(after["bounds"]["min"][0], 20.0 - 1e-3)
            self.assertLessEqual(after["bounds"]["max"][0], 40.0 + 1e-3)


class TestCompareClouds(unittest.TestCase):

    def test_detects_known_settlement(self):
        """Lún đều 18 mm giữa hai đợt quét phải được đo lại đúng 18 mm."""
        with tempfile.TemporaryDirectory() as d:
            rng = np.random.default_rng(4)
            n = 50_000
            base = np.column_stack([rng.uniform(0, 30, n), rng.uniform(0, 30, n),
                                    np.full(n, 5.0)])
            settlement = 0.018
            a, b = Path(d) / "dot1.las", Path(d) / "dot2.las"
            write_las(a, base)
            write_las(b, base - np.array([0.0, 0.0, settlement]))
            r = srv.compare_point_clouds(str(a), str(b), tolerance=0.025)
            self.assertTrue(r["ok"], r.get("error"))
            self.assertAlmostEqual(r["distance"]["mean"], settlement, delta=2e-4)
            self.assertEqual(r["distance"]["within_tolerance_pct"], 100.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
