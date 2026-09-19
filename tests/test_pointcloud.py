# -*- coding: utf-8 -*-
"""
Kiểm thử lớp phân tích point cloud.

Nguyên tắc của bộ test này: mọi hình học dùng để kiểm tra đều có ĐÁP SỐ GIẢI TÍCH
tính được bằng tay. Một phép đo hình học chỉ "chạy không lỗi" thì chưa chứng minh
được gì — sai số âm thầm trong khoảng cách điểm-tam giác sẽ đi thẳng vào kết quả
nghiên cứu mà không có dấu hiệu nào.
"""

from __future__ import annotations

import math
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from recap_mcp import pointcloud as pc
from recap_mcp.rcp import RecapError


def write_test_las(path: Path, points: np.ndarray) -> None:
    import laspy
    header = laspy.LasHeader(version="1.4", point_format=3)
    header.scales = np.array([0.0001, 0.0001, 0.0001])
    header.offsets = points.min(axis=0)
    las = laspy.LasData(header)
    las.x, las.y, las.z = points[:, 0], points[:, 1], points[:, 2]
    las.write(str(path))


# Lưới vuông phẳng [0,10] x [0,10] nằm trên mặt z = 0, pháp tuyến +z.
FLAT_OBJ = """v 0 0 0
v 10 0 0
v 10 10 0
v 0 10 0
f 1 2 3
f 1 3 4
"""


class TestPointToMesh(unittest.TestCase):
    """Đối chiếu với khoảng cách tính tay cho cả 3 vùng Voronoi: mặt, cạnh, đỉnh."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.obj = Path(cls.tmp.name) / "flat.obj"
        cls.obj.write_text(FLAT_OBJ, encoding="utf-8")
        cls.verts, cls.faces = pc.load_mesh(cls.obj)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_mesh_loaded(self):
        self.assertEqual(len(self.verts), 4)
        self.assertEqual(len(self.faces), 2)

    def test_distance_above_face_equals_height(self):
        """Vùng MẶT: điểm ngay trên tấm phẳng, khoảng cách đúng bằng cao độ."""
        pts = np.array([[5.0, 5.0, 2.0], [1.0, 9.0, 0.35], [9.5, 0.5, 1.25]])
        res = pc.point_to_mesh(pts, self.verts, self.faces)
        np.testing.assert_allclose(res["distance"], [2.0, 0.35, 1.25], atol=1e-9)

    def test_distance_beyond_edge(self):
        """Vùng CẠNH: điểm ngoài mép x=10, khoảng cách là khoảng ngang tới mép."""
        pts = np.array([[15.0, 5.0, 0.0], [13.0, 5.0, 4.0]])
        res = pc.point_to_mesh(pts, self.verts, self.faces)
        expected = [5.0, math.hypot(3.0, 4.0)]
        np.testing.assert_allclose(res["distance"], expected, atol=1e-9)

    def test_distance_beyond_corner(self):
        """Vùng ĐỈNH: điểm chéo ngoài góc, khoảng cách là tới chính đỉnh góc."""
        pts = np.array([[15.0, 15.0, 0.0], [-3.0, -4.0, 12.0]])
        res = pc.point_to_mesh(pts, self.verts, self.faces)
        expected = [math.hypot(5.0, 5.0), math.sqrt(9 + 16 + 144)]
        np.testing.assert_allclose(res["distance"], expected, atol=1e-9)

    def test_sign_follows_mesh_normal(self):
        pts = np.array([[5.0, 5.0, 2.0], [5.0, 5.0, -2.0]])
        res = pc.point_to_mesh(pts, self.verts, self.faces, signed=True)
        self.assertGreater(res["signed"][0], 0, "Điểm phía +z phải mang dấu dương")
        self.assertLess(res["signed"][1], 0, "Điểm phía -z phải mang dấu âm")
        np.testing.assert_allclose(np.abs(res["signed"]), [2.0, 2.0], atol=1e-9)

    def test_certificate_flags_when_candidates_insufficient(self):
        """Với k=1 trên lưới 2 tam giác, thuật toán phải TỰ BÁO là chưa chắc chắn."""
        pts = np.array([[5.0, 5.0, 1.0]])
        res = pc.point_to_mesh(pts, self.verts, self.faces, k=1)
        self.assertTrue(bool(res["uncertain"][0]),
                        "k=1 không đủ để chứng minh đã tìm đúng tam giác gần nhất")
        res_full = pc.point_to_mesh(pts, self.verts, self.faces, k=2)
        np.testing.assert_allclose(res_full["distance"], [1.0], atol=1e-9)


class TestFitPlane(unittest.TestCase):

    def test_recovers_known_tilted_plane(self):
        """Mặt nghiêng 30° quanh trục x: pháp tuyến và góc nghiêng phải khớp."""
        rng = np.random.default_rng(42)
        u = rng.uniform(-5, 5, 4000)
        v = rng.uniform(-5, 5, 4000)
        angle = math.radians(30.0)
        pts = np.column_stack([u, v * math.cos(angle), v * math.sin(angle)])

        plane = pc.fit_plane(pts)
        self.assertAlmostEqual(plane["tilt_from_horizontal_deg"], 30.0, places=4)
        self.assertLess(abs(float(np.abs(plane["residuals"]).max())), 1e-9,
                        "Mặt phẳng hoàn hảo thì độ lệch phải bằng 0")

    def test_rms_matches_injected_noise(self):
        """Thêm nhiễu có độ lệch chuẩn biết trước, RMS khớp phải trả lại đúng nó."""
        rng = np.random.default_rng(7)
        n = 200_000
        sigma = 0.004
        pts = np.column_stack([
            rng.uniform(0, 20, n), rng.uniform(0, 20, n), rng.normal(0, sigma, n),
        ])
        plane = pc.fit_plane(pts)
        stats = pc.describe(plane["residuals"], tolerance=0.01)
        self.assertAlmostEqual(stats["rms"], sigma, delta=sigma * 0.05)
        # Ngưỡng 2.5 sigma của phân phối chuẩn bao khoảng 98.76% số điểm.
        self.assertAlmostEqual(stats["within_tolerance_pct"], 98.76, delta=0.5)

    def test_vertical_wall_detected(self):
        """Tường chắn thẳng đứng: tilt_from_vertical_deg phải xấp xỉ 0."""
        rng = np.random.default_rng(3)
        n = 5000
        pts = np.column_stack([rng.uniform(0, 30, n), np.zeros(n), rng.uniform(0, 8, n)])
        plane = pc.fit_plane(pts)
        self.assertAlmostEqual(plane["tilt_from_vertical_deg"], 0.0, places=4)

    def test_too_few_points(self):
        with self.assertRaises(RecapError):
            pc.fit_plane(np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]]))


class TestSlabSection(unittest.TestCase):

    def test_selects_only_slab(self):
        """Lát dày 0.1 quanh x=5 phải lấy đúng các điểm có |x-5| <= 0.05."""
        xs = np.arange(0.0, 10.0, 0.01)
        pts = np.column_stack([xs, np.zeros_like(xs), np.zeros_like(xs)])
        sec = pc.slab_section(pts, [5.0, 0.0, 0.0], [1.0, 0.0, 0.0], 0.05)
        expected = int(np.count_nonzero(np.abs(xs - 5.0) <= 0.05 + 1e-12))
        self.assertEqual(sec["selected"], expected)
        self.assertTrue(np.all(np.abs(sec["offset_from_plane"]) <= 0.05 + 1e-12))

    def test_axes_are_orthonormal(self):
        pts = np.zeros((1, 3))
        for normal in ([1, 0, 0], [0, 1, 0], [0, 0, 1], [1, 1, 1], [0.3, -0.7, 0.2]):
            sec = pc.slab_section(pts, [0, 0, 0], normal, 1.0)
            u = np.array(sec["axis_u"]); v = np.array(sec["axis_v"]); n = np.array(sec["normal"])
            for name, vec in (("u", u), ("v", v), ("n", n)):
                self.assertAlmostEqual(float(np.linalg.norm(vec)), 1.0, places=9,
                                       msg=f"Trục {name} chưa chuẩn hoá với normal={normal}")
            self.assertAlmostEqual(float(u @ v), 0.0, places=9)
            self.assertAlmostEqual(float(u @ n), 0.0, places=9)
            self.assertAlmostEqual(float(v @ n), 0.0, places=9)

    def test_rejects_zero_normal(self):
        with self.assertRaises(RecapError):
            pc.slab_section(np.zeros((1, 3)), [0, 0, 0], [0, 0, 0], 1.0)


class TestElevationGrid(unittest.TestCase):

    def test_known_cell_values(self):
        """Lưới 1 m trên vùng 4x4 m, cao độ = x, nên 'mean' tăng theo cột."""
        g = np.arange(0.25, 4.0, 0.5)
        xx, yy = np.meshgrid(g, g)
        pts = np.column_stack([xx.ravel(), yy.ravel(), xx.ravel()])
        grid = pc.elevation_grid(pts, cell_size=1.0, statistic="mean")
        self.assertEqual(grid["grid_size"], [4, 4])
        self.assertEqual(len(grid["cells"]), 16)

        # Quy ước: gốc lưới đặt tại ĐIỂM NHỎ NHẤT của dữ liệu, không phải tại số
        # tròn. Nên tâm ô đầu tiên là min + cell/2 = 0.25 + 0.5, và tâm ô KHÔNG
        # trùng toạ độ điểm nào. Ghim lại ở đây vì đây là chỗ dễ hiểu nhầm nhất.
        self.assertAlmostEqual(min(c["x"] for c in grid["cells"]), 0.75, places=9)

        for c in grid["cells"]:
            self.assertEqual(c["count"], 4)
            column = int(round(c["x"] - 0.75))           # chỉ số cột suy từ tâm ô
            # Mỗi cột chứa hai giá trị x: 0.25+column và 0.75+column.
            self.assertAlmostEqual(c["value"], 0.5 + column, places=9)

    def test_range_statistic_finds_vertical_object(self):
        """Ô có vật đứng phải lộ ra qua statistic='range'."""
        flat = np.column_stack([np.full(100, 0.5), np.full(100, 0.5), np.zeros(100)])
        pole = np.column_stack([np.full(50, 2.5), np.full(50, 2.5),
                                np.linspace(0, 3.0, 50)])
        grid = pc.elevation_grid(np.vstack([flat, pole]), cell_size=1.0, statistic="range")
        # Gốc lưới = min dữ liệu = 0.5, nên hai ô có tâm tại 1.0 và 3.0.
        values = {round(c["x"], 1): c["value"] for c in grid["cells"]}
        self.assertEqual(sorted(values), [1.0, 3.0])
        self.assertAlmostEqual(values[1.0], 0.0, places=9, msg="Ô nền phẳng: range = 0")
        self.assertAlmostEqual(values[3.0], 3.0, places=9, msg="Ô có cột cao 3 m: range = 3")

    def test_rejects_absurd_cell_size(self):
        pts = np.column_stack([np.array([0.0, 10000.0]), np.array([0.0, 10000.0]),
                               np.zeros(2)])
        with self.assertRaises(RecapError) as ctx:
            pc.elevation_grid(pts, cell_size=0.001, statistic="mean")
        self.assertIn("quá lớn", str(ctx.exception))


class TestLasRoundTrip(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "cloud.las"
        rng = np.random.default_rng(11)
        self.points = np.column_stack([
            rng.uniform(0, 50, 20_000), rng.uniform(0, 20, 20_000),
            rng.uniform(0, 5, 20_000),
        ])
        write_test_las(self.path, self.points)

    def tearDown(self):
        self.tmp.cleanup()

    def test_inspect_reports_true_count_and_bounds(self):
        info = pc.inspect_las(self.path)
        self.assertEqual(info["point_count"], 20_000)
        for i in range(3):
            self.assertAlmostEqual(info["bounds"]["min"][i], self.points[:, i].min(), places=3)
            self.assertAlmostEqual(info["bounds"]["max"][i], self.points[:, i].max(), places=3)

    def test_bbox_filter_matches_numpy(self):
        bbox = {"min": [10.0, 5.0, 1.0], "max": [30.0, 15.0, 4.0]}
        pts, _, info = pc.load_points(self.path, bbox=bbox)
        mn = np.array(bbox["min"]); mx = np.array(bbox["max"])
        expected = int(np.count_nonzero(
            np.all((self.points >= mn) & (self.points <= mx), axis=1)))
        self.assertEqual(info["points_kept"], expected)
        self.assertEqual(len(pts), expected)

    def test_truncation_is_announced_not_silent(self):
        pts, _, info = pc.load_points(self.path, max_points=500)
        self.assertEqual(len(pts), 500)
        self.assertTrue(info["truncated"])
        self.assertIn("warning", info)

    def test_every_nth(self):
        _, _, info = pc.load_points(self.path, every_nth=10)
        self.assertLess(info["points_kept"], 20_000)
        self.assertGreater(info["points_kept"], 1_500)


class TestCompareClouds(unittest.TestCase):

    def test_known_vertical_shift(self):
        """Dời cả đám mây lên 25 mm thì khoảng cách cloud-to-cloud phải ra đúng 25 mm."""
        from scipy.spatial import cKDTree
        rng = np.random.default_rng(5)
        ref = np.column_stack([rng.uniform(0, 10, 30_000), rng.uniform(0, 10, 30_000),
                               np.zeros(30_000)])
        shift = 0.025
        tgt = ref + np.array([0.0, 0.0, shift])
        dist, _ = cKDTree(ref).query(tgt, k=1)
        stats = pc.describe(dist, tolerance=0.05)
        self.assertAlmostEqual(stats["mean"], shift, places=6)
        self.assertAlmostEqual(stats["max"], shift, places=6)
        self.assertEqual(stats["within_tolerance_pct"], 100.0)


class TestDescribe(unittest.TestCase):

    def test_statistics_match_numpy(self):
        rng = np.random.default_rng(1)
        v = rng.normal(0.01, 0.005, 50_000)
        s = pc.describe(v, tolerance=0.02)
        self.assertAlmostEqual(s["mean"], float(np.mean(v)), places=12)
        self.assertAlmostEqual(s["rms"], float(np.sqrt(np.mean(v ** 2))), places=12)
        self.assertAlmostEqual(s["p95_abs"], float(np.percentile(np.abs(v), 95)), places=12)
        self.assertEqual(s["within_tolerance"] + s["outside_tolerance"], s["count"])

    def test_empty_input_is_reported_not_crashed(self):
        s = pc.describe(np.empty(0))
        self.assertEqual(s["count"], 0)
        self.assertIn("note", s)


if __name__ == "__main__":
    unittest.main(verbosity=2)
