# -*- coding: utf-8 -*-
"""
Kiểm thử phần đọc định dạng ReCap, chạy trên project mẫu đi kèm bản cài.

Các giá trị mong đợi dưới đây lấy từ chính manifest của project mẫu, nên test này
phát hiện được cả lỗi parse lẫn thay đổi định dạng ở bản ReCap mới.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from recap_mcp import rcp as rc
from recap_mcp.rcp import RecapError

SAMPLE = Path(r"C:\ProgramData\Autodesk\Autodesk ReCap\Sample\AutodeskReCapSampleProject.rcp")
HAS_SAMPLE = SAMPLE.is_file()
SKIP = "Không tìm thấy project mẫu của ReCap trên máy này."


@unittest.skipUnless(HAS_SAMPLE, SKIP)
class TestParseRcp(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.data = rc.parse_rcp(SAMPLE)

    def test_project_identity(self):
        self.assertEqual(self.data["project_name"], "AutodeskReCapSampleProject")
        self.assertEqual(self.data["schema_version"], "1.0")
        self.assertEqual(self.data["metadata"]["saved_by_application"], "ReCap")

    def test_scan_inventory(self):
        self.assertEqual(self.data["scan_count"], 2)
        names = sorted(s["name"] for s in self.data["scans"])
        self.assertEqual(names, ["techshop_012", "techshop_013"])
        self.assertEqual(self.data["total_points"], 4425891 + 4346791)

    def test_scan_attributes(self):
        s = next(s for s in self.data["scans"] if s["name"] == "techshop_012")
        self.assertEqual(s["num_points"], 4425891)
        self.assertTrue(s["has_rgb"])
        self.assertTrue(s["has_intensity"])
        self.assertEqual(s["range_image"], {"width": 4096, "height": 1707})
        self.assertTrue(s["rcs_exists"], "Đường dẫn .rcs phải giải từ RelativePath, "
                                         "không dùng Path tuyệt đối của máy tạo file")

    def test_registration_is_joined_from_three_places(self):
        """Ma trận và chất lượng đăng ký nằm rải ở 3 chỗ khác nhau trong manifest."""
        s = next(s for s in self.data["scans"] if s["name"] == "techshop_012")
        reg = s["registration"]
        self.assertTrue(reg["is_registered"])
        self.assertEqual(reg["level"], "FINE_ALIGNED")
        self.assertEqual(reg["status"], "OK")
        self.assertIn("rotation_matrix", reg)

        m = reg["matrix4x4"]
        self.assertEqual(len(m), 4)
        self.assertEqual(m[3], [0.0, 0.0, 0.0, 1.0])
        # Cột tịnh tiến phải khớp với Translation của node.
        for i in range(3):
            self.assertAlmostEqual(m[i][3], s["translation"][i], places=6)

    def test_rotation_matrix_is_orthonormal(self):
        """Ma trận xoay đọc từ manifest phải trực chuẩn — nếu không là đã ghép sai ô."""
        for s in self.data["scans"]:
            r = s["registration"].get("rotation_matrix")
            if not r:
                continue
            for row in r:
                norm = sum(v * v for v in row) ** 0.5
                self.assertAlmostEqual(norm, 1.0, places=5,
                                       msg=f"Hàng không chuẩn hoá ở scan {s['name']}")
            dot = sum(r[0][i] * r[1][i] for i in range(3))
            self.assertAlmostEqual(dot, 0.0, places=5,
                                   msg=f"Hai hàng không trực giao ở scan {s['name']}")

    def test_measurements(self):
        kinds = {m["kind"] for m in self.data["measurements"]}
        self.assertIn("alDistance", kinds)
        self.assertIn("alNote", kinds)
        dist = next(m for m in self.data["measurements"] if m["kind"] == "alDistance")
        self.assertEqual(len(dist["picks"]), 2)
        # Khoảng cách được tính lại từ hai điểm pick, nên phải nhất quán với chúng.
        a, b = dist["picks"]
        expected = sum((b[i] - a[i]) ** 2 for i in range(3)) ** 0.5
        self.assertAlmostEqual(dist["distance"], expected, places=6)

    def test_regions(self):
        names = [r["name"] for r in self.data["regions"]]
        self.assertIn("Floor", names)
        floor = next(r for r in self.data["regions"] if r["name"] == "Floor")
        self.assertEqual(floor["color_rgb"], [0.0, 1.0, 0.0])

    def test_bounds_are_not_inverted(self):
        b = self.data["bounds"]
        self.assertIsNotNone(b, "Manifest có <map><bounds> nên phải đọc ra được")
        for i in range(3):
            self.assertLessEqual(b["min"][i], b["max"][i])


@unittest.skipUnless(HAS_SAMPLE, SKIP)
class TestRcsHeader(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.project = rc.parse_rcp(SAMPLE, include_geometry=False)

    def test_header_matches_manifest(self):
        """Đối chiếu chéo: header nhị phân và manifest XML phải nói cùng một chuyện."""
        for s in self.project["scans"]:
            with self.subTest(scan=s["name"]):
                h = rc.read_rcs_header(s["rcs_path"])
                self.assertEqual(h["num_points"], s["num_points"])
                self.assertEqual(h["guid"], s["id"])
                for i in range(3):
                    self.assertAlmostEqual(h["translation"][i], s["translation"][i], places=6)

    def test_octree_box_contains_data_box(self):
        for s in self.project["scans"]:
            h = rc.read_rcs_header(s["rcs_path"])
            for i in range(3):
                self.assertLessEqual(h["octree_bounds"]["min"][i], h["data_bounds"]["min"][i])
                self.assertGreaterEqual(h["octree_bounds"]["max"][i], h["data_bounds"]["max"][i])

    def test_rejects_non_rcs(self):
        with self.assertRaises(RecapError) as ctx:
            rc.read_rcs_header(SAMPLE)          # .rcp không phải .rcs
        self.assertIn("magic", str(ctx.exception).lower())


class TestErrorMessages(unittest.TestCase):
    """Lỗi phải nói được phải làm gì tiếp theo, không chỉ nói cái gì hỏng."""

    def test_missing_file(self):
        with self.assertRaises(RecapError) as ctx:
            rc.parse_rcp(r"C:\khong-ton-tai\abc.rcp")
        self.assertIn("Không tìm thấy", str(ctx.exception))

    def test_not_a_zip(self, ):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".rcp", delete=False) as fh:
            fh.write(b"day khong phai file zip")
            tmp = fh.name
        try:
            with self.assertRaises(RecapError) as ctx:
                rc.parse_rcp(tmp)
            self.assertIn("read_rcs_header", str(ctx.exception))
        finally:
            Path(tmp).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
