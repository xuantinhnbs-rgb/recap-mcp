# Autodesk ReCap MCP Server

Server MCP cho Autodesk ReCap, hướng tới nghiên cứu **Scan-to-BIM giám sát hạ tầng
giao thông**: đọc project ReCap, chạy engine xử lý ở chế độ headless, và đo sai lệch
hình học giữa point cloud thực đo với mô hình BIM thiết kế.

Đã kiểm chứng trên **Autodesk ReCap 26.1.1.221**, Windows 10, Python 3.14.

---

## ReCap khác các phần mềm Autodesk còn lại ở chỗ nào

AutoCAD, Revit, Navisworks đều có COM/.NET API để script điều khiển. **ReCap không
có.** Server này vì vậy đi ba đường khác nhau, và mỗi đường có giới hạn riêng cần
biết trước khi thiết kế quy trình nghiên cứu:

| Lớp | Cơ chế | Làm được | Không làm được |
|---|---|---|---|
| **Đọc project** | `.rcp` là file ZIP chứa XML manifest | Scan, ma trận + chất lượng đăng ký, phép đo 3D, phân vùng, hệ toạ độ, hộp bao | Không sửa được project |
| **Xử lý headless** | `decap.exe` đi kèm bản cài | Import, giảm mật độ, cắt khoảng cách, gộp scan, đổi hệ toạ độ | Không xuất được point cloud ra định dạng khác |
| **Phân tích điểm** | `laspy` đọc file LAS/LAZ **gốc** | Mặt cắt, khớp mặt phẳng, so sánh với BIM, lưới cao độ, so sánh hai đợt quét | Không đọc được điểm từ `.rcs` |

### Ba ngõ cụt đã dò và loại trừ

Ghi lại để người sau không mất công dò lại:

1. **Không có COM/automation API.** ReCap chưa từng công bố, và không có type library
   nào trong thư mục cài đặt.
2. **SDK trong `AdskRealityStudioHLAPI.dll` không bọc được.** DLL này export 744
   symbol nhưng toàn bộ là tên C++ mangled của phương thức lớp (`??0...@@QEAA@...`),
   không có một entry point C ABI nào. Không header, không ABI ổn định ⇒ `ctypes`
   không dùng được.
3. **Thân file `.rcs` không giải mã được.** Magic là `ADOCT` (octree của Autodesk),
   định dạng đóng. **Header thì đọc được** và đã kiểm chứng đối chiếu với manifest:
   số điểm khớp chính xác, GUID khớp, ma trận đăng ký khớp.

### Hệ quả cho quy trình nghiên cứu

Vì không đọc được toạ độ điểm từ `.rcs`, hãy **giữ song song hai bộ dữ liệu**:

```
  File quét gốc (.las/.laz)  ──┬──►  ReCap  ──►  .rcp  ──►  đăng ký, lập chỉ mục, xem
                               │                            (tool đọc project)
                               └──────────────────────────►  tính toán định lượng
                                                             (tool phân tích điểm)
```

Đây không phải cách làm tạm bợ: ReCap làm rất tốt việc đăng ký trạm quét và dựng
octree để xem mượt, còn file LAS/LAZ gốc mới là nguồn toạ độ dùng cho số liệu bài báo.

---

## Cài đặt

```bash
git clone https://github.com/xuantinhnbs-rgb/recap-mcp.git
cd recap-mcp
pip install -r requirements.txt
```

Đăng ký trong `.mcp.json` của Claude Code (thay `<ĐƯỜNG_DẪN_REPO>` bằng nơi bạn vừa
clone về, và `<PYTHON>` bằng trình thông dịch Python 3.12+ trên máy bạn):

```json
{
  "mcpServers": {
    "recap": {
      "command": "<PYTHON>",
      "args": ["<ĐƯỜNG_DẪN_REPO>/recap_server.py"],
      "cwd": "<ĐƯỜNG_DẪN_REPO>",
      "env": { "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8" }
    }
  }
}
```

`PYTHONIOENCODING` không phải tuỳ chọn trang trí: README và thông điệp lỗi của máy chủ
này viết bằng tiếng Việt, thiếu nó thì trên Windows tiến trình sẽ chết vì lỗi mã hoá
cp1252 chứ không phải vì logic sai.

Biến môi trường tuỳ chọn:

- `RECAP_HOME` — thư mục cài ReCap, nếu không nằm ở `C:\Program Files\Autodesk\...`
- `RECAP_MCP_LOG_DIR` — nơi ghi log của các job `decap.exe` chạy nền

---

## Quy trình Scan-to-BIM: đo sai lệch một tường chắn

```
1. inspect_point_cloud("tuong_chan_thuc_do.las")
      → biết hộp bao, số điểm, hệ toạ độ, các trường dữ liệu có sẵn

2. crop_point_cloud(..., bbox={...}, output="doan_K12.las")
      → tách riêng đoạn cần xét, khỏi phải xử lý cả tuyến

3. compare_to_bim_mesh(
       points_path = "doan_K12.las",
       mesh_path   = "tuong_thiet_ke.obj",   ← xuất từ Revit/Navisworks
       tolerance   = 0.02,                    ← dung sai 20 mm
       output_csv  = "do_lech_K12.csv")
      → mean, std, RMS, P95, max, % điểm trong dung sai, histogram

4. fit_plane_to_region(..., bbox={...})
      → độ nghiêng thực tế của mặt tường so với phương thẳng đứng

5. extract_cross_section(..., station=..., output_csv="mat_cat.csv")
      → mặt cắt ngang tại từng lý trình để vẽ biểu đồ đối chiếu
```

### Hai điều dễ sai nhất, và server chặn chúng thế nào

**Lệch hệ toạ độ.** Đây là lỗi phổ biến nhất của Scan-to-BIM: mô hình BIM ở toạ độ
công trình còn point cloud ở toạ độ VN-2000, kết quả ra sai lệch hàng trăm mét nhưng
vẫn là những con số trông hợp lệ. `compare_to_bim_mesh` đo khoảng hở giữa hai hộp bao,
so với kích thước dữ liệu, và cảnh báo kèm tên trục bị lệch. Nó **không** dùng phép
kiểm tra giao nhau đơn thuần — lưới BIM của một mặt tường có bề dày đúng bằng 0, nên
phép kiểm tra đó sẽ báo động giả cho mọi trường hợp đúng.

**Xấp xỉ âm thầm sai.** Khoảng cách điểm-tới-lưới dùng KD-tree trên trọng tâm tam giác
rồi xét `candidates` ứng viên gần nhất — đây là xấp xỉ. Server kèm theo một **chứng
chỉ toán học**: gọi `d` là khoảng cách nhỏ nhất tìm được, `r_k` là khoảng cách tới
trọng tâm thứ k, `R` là bán kính ngoại tiếp lớn nhất của lưới; nếu `r_k ≥ d + R` thì
không tam giác nào ngoài tập ứng viên có thể gần hơn, nên kết quả **chính xác tuyệt
đối**. Số điểm không thoả được đếm và trả về trong `uncertain_points`. Con số đó lớn
thì tăng `candidates` rồi so hai kết quả.

---

## Danh sách tool (25)

### Môi trường
| Tool | Công dụng |
|---|---|
| `check_recap_installation` | Bản ReCap, đường dẫn executable, license, bảng khả năng — **gọi đầu tiên** |
| `check_recap_license` | Hỏi riêng trạng thái license |
| `open_project_in_recap` | Mở project bằng giao diện ReCap để xem |

### Đọc project
| Tool | Công dụng |
|---|---|
| `find_projects` | Quét thư mục tìm `.rcp` / `.rcs` |
| `read_project` | Toàn bộ manifest |
| `list_scans` | Danh sách trạm quét, số điểm, thuộc tính |
| `get_scan_registration` | **Ma trận 4×4 + chất lượng đăng ký từng trạm** |
| `get_measurements` | Phép đo và ghi chú kèm toạ độ 3D |
| `get_regions` | Layer phân vùng, limit box |
| `read_rcs_header` | Hộp bao thật, số điểm, vị trí đăng ký của một scan |
| `extract_project_preview` | Rút ảnh xem trước nhúng trong `.rcp` |

### Xử lý headless
| Tool | Công dụng |
|---|---|
| `import_scans` | Import scan thành project mới — **chạy nền, trả `job_id`** |
| `get_job_status` | Tiến độ, đuôi log, và **kiểm tra file đích có thật không** |
| `list_jobs` / `cancel_job` | Quản lý job |

### Phân tích point cloud
| Tool | Công dụng |
|---|---|
| `inspect_point_cloud` | Header LAS/LAZ, không nạp điểm |
| `sample_points` | Mẫu ngẫu nhiên để xem nhanh |
| `crop_point_cloud` | Cắt vùng ra file mới |
| `voxel_downsample` | Làm thưa đều theo lưới voxel |
| `extract_cross_section` | **Mặt cắt ngang tại một lý trình trên tim tuyến** |
| `extract_section_by_plane` | Mặt cắt theo mặt phẳng bất kỳ |
| `fit_plane_to_region` | Độ phẳng, độ nghiêng, độ lệch của một mặt |
| `compare_to_bim_mesh` | **So sánh point cloud với lưới BIM** (.obj/.stl/.ply) |
| `compare_point_clouds` | So hai đợt quét — đo lún, đo biến dạng |
| `elevation_grid_report` | Lưới cao độ: bản đồ mặt đường, bản đồ lún |

Mọi tool trả về JSON có khoá `ok`. Lỗi cho `{"ok": false, "error": "..."}` kèm hướng
khắc phục, không bao giờ ném ngoại lệ ra ngoài.

---

## Kiểm thử

```bash
python -m unittest tests.test_rcp tests.test_pointcloud tests.test_server   # 57 test
python tests/test_mcp.py                                                    # giao thức MCP
```

Nguyên tắc của bộ test: **mọi hình học dùng để kiểm tra đều có đáp số giải tích**.

- `test_rcp.py` chạy trên project mẫu thật đi kèm bản cài ReCap; đối chiếu chéo header
  nhị phân `.rcs` với manifest XML, và kiểm tra ma trận xoay có trực chuẩn không.
- `test_pointcloud.py` kiểm khoảng cách điểm-tam giác ở cả ba vùng Voronoi (mặt, cạnh,
  đỉnh) bằng giá trị tính tay; khớp mặt phẳng vào mặt nghiêng 30° và vào nhiễu có độ
  lệch chuẩn biết trước.
- `test_server.py` dựng một tường chắn nghiêng theo quy luật `x = 0.015 + 0.002·z`, nên
  sai lệch trung bình, min, max và **tỉ lệ phần trăm trong dung sai** đều suy ra được
  bằng tay và đối chiếu được từng con số.

---

## Cảnh báo: `decimation_mm` bị bỏ qua lặng lẽ với đầu vào `.rcs`

Đã đo được trên ReCap 26.1.1.221: import một file `.rcs` **đã lập chỉ mục** với
`decimation_mm=50`, `decap.exe` trả mã 0, tạo ra `.rcp` bình thường, log không cảnh
báo gì — nhưng số điểm ở đầu ra bằng **đúng** đầu vào, tức cờ không có tác dụng.

Đặt `decimation_mm` khi nguồn là dữ liệu **thô** (`.las`, `.laz`, `.e57`, `.pts`).
Và dù nguồn là gì, luôn kiểm chứng bằng cách so số điểm trước/sau:

```
truoc = inspect_point_cloud("nguon.las")["point_count"]
...
sau   = list_scans("<project moi>.rcp")["total_points"]
```

Mã thoát 0 và file đích tồn tại **không** chứng minh cờ xử lý đã có hiệu lực — đó là
ba mức kiểm tra khác nhau, và chỉ mức thứ ba mới nói được điều bạn cần.

---

## Ghi chú về đơn vị

- Toạ độ giữ nguyên đơn vị của file nguồn. LAS/LAZ hạ tầng giao thông thường là **mét**.
- Cờ `decimation_mm` của `decap.exe` tính bằng **milimét** — đây là ngoại lệ duy nhất.
- Enum `UnitType` trong manifest `.rcp` **không được Autodesk công bố**. Server trả về
  giá trị thô kèm nhãn suy đoán và một cảnh báo; hãy đối chiếu với kích thước thật
  trong `bounds` trước khi dùng cho tính toán khoa học.
