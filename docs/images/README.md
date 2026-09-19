# Ảnh minh chứng — cách chúng được tạo ra

Mọi ảnh trong thư mục này đều lấy từ phần mềm thật hoặc từ một lần chạy thật, ở
trạng thái do chính MCP server trong repo này tạo ra. Không có ảnh nào là dựng
lại, ghép hay vẽ minh hoạ.

Hai script trong [`scripts/`](../../scripts/) làm việc này và chạy lại được:

| Script | Việc |
|---|---|
| [`capture-window.ps1`](../../scripts/capture-window.ps1) | Chụp một cửa sổ ứng dụng ra PNG, kể cả khung nhìn 3D tăng tốc phần cứng |
| [`redact-image.ps1`](../../scripts/redact-image.ps1) | Che vùng chữ nhận diện và cắt ảnh trước khi đưa lên repo công khai |

---

## `recap-sample-preview.png`

**Nội dung:** ảnh render point cloud của project mẫu đi kèm bản cài ReCap — một
xưởng cơ khí, đúng như tên hai trạm quét `techshop_012` và `techshop_013`.

Đây **không phải ảnh chụp màn hình**. Nó là ảnh xem trước mà ReCap nhúng sẵn bên
trong file `.rcp`, và được chính server rút ra bằng một lời gọi tool — không có
cửa sổ ReCap nào mở trong suốt quá trình:

```python
extract_project_preview(
    rcp_path    = r"C:\ProgramData\Autodesk\Autodesk ReCap\Sample\AutodeskReCapSampleProject.rcp",
    output_path = r"<thư mục tạm>\sample-screenshot.bmp",
    entry       = "rs_screenshot__.bmp")
```

Các con số trong chú thích ở README đến từ ba tool khác đọc **cùng file đó**:
`read_project`, `list_scans`, `get_scan_registration` — 2 trạm quét, 8 772 682
điểm, cả hai `FINE_ALIGNED`, hộp bao 59,86 × 59,82 × 5,79 m, 3 phép đo, 1 vùng
tên `Floor`.

Ảnh gốc là BMP 1184 × 882 (3,0 MB). Thu về 1024 px và giảm còn bảng 256 màu để
file nằm trong khoảng 400 KB, cho cùng cỡ với ảnh minh chứng của các repo anh em:

```python
from PIL import Image
im = Image.open("sample-screenshot.bmp").convert("RGB")
im = im.resize((1024, int(im.height * 1024 / im.width)), Image.LANCZOS)
im.quantize(colors=256, dither=Image.FLOYDSTEINBERG).save(
    "docs/images/recap-sample-preview.png", optimize=True)
```

**Vì sao dùng project mẫu của Autodesk chứ không dùng dữ liệu quét thật.** Dữ
liệu quét thực địa là dữ liệu của một công trình có thật: ảnh chụp cửa sổ ReCap
đang mở nó sẽ mang theo tên dự án, toạ độ trắc địa trên thanh trạng thái, và tên
tài khoản Autodesk đang đăng nhập ở góc trên. Project mẫu thì đi kèm mọi bản cài
ReCap, nên người đọc chạy đúng lệnh trên và so được kết quả.

---

## `rcs-header-crosscheck.png`

**Nội dung:** `pytest tests/test_rcp.py -v` chạy trên project mẫu thật, liệt kê
từng phép đối chiếu theo tên.

Đây là vật chứng cho câu khẳng định trung tâm của README: thân file `.rcs` không
giải mã được, **nhưng header thì đọc được và khớp với manifest XML**. Bộ test mở
hai nguồn đó một cách độc lập rồi bắt chúng phải nhất trí về số điểm, GUID và ma
trận đăng ký — và kiểm tra ma trận xoay có trực chuẩn thật không.

```powershell
pytest tests/test_rcp.py -v
```

---

## `install-check-and-tests.png`

**Nội dung:** ba lệnh mà CI chạy — `install.py --check` nạp thử server và đếm
được 25 tool, `ruff` sạch, toàn bộ bộ test xanh.

```powershell
python install.py --check | Select-String '\[OK\]|\[!\]|\[X\]'
ruff check .
pytest
```

`install.py` in ra các tiêu đề bước cách nhau bằng đường kẻ, dài hơn một màn hình
24 dòng. Bộ lọc chỉ giữ lại các dòng trạng thái để cả ba lệnh cùng nằm trong một
khung hình; chạy `python install.py --check` trần cho ra đúng những dòng đó kèm
tiêu đề bước.

Đường dẫn trong ảnh là `X:\` vì repo được ánh xạ qua một ổ ảo trước khi chụp, để
ảnh không mang theo tên người dùng của máy:

```powershell
subst X: <thư mục repo>
# chạy 3 lệnh trong X:\ rồi chụp
subst X: /d
```

**Cửa sổ tự chụp chính nó.** Script chạy ba lệnh rồi gọi `capture-window.ps1` với
`-ProcessId $PID` ở dòng cuối, thay vì để một tiến trình bên ngoài chụp nó. Lý do
nằm ở cách chụp: ảnh được copy từ vùng màn hình mà cửa sổ đang chiếm, nên cửa sổ
phải thật sự ở tiền cảnh. Tiến trình bên ngoài phải giành tiền cảnh, và Windows
thường từ chối — đã đo được: lời gọi thất bại, script vẫn chụp, và file PNG chứa
một ứng dụng khác hẳn. Chạy từ bên trong thì cửa sổ đã ở tiền cảnh sẵn.

Ngoài ra `capture-window.ps1` bây giờ **kiểm chứng** cửa sổ đã lên tiền cảnh
trước khi copy pixel, và báo lỗi nếu không — xem quy tắc 5 ở cuối file này.

Lưu ý khi chạy lại: `pyproject.toml` đã có `addopts = "-q --strict-markers"`, nên
gõ thêm `pytest -q` thành `-qq` và pytest sẽ nuốt luôn dòng tổng kết
`N passed`. Cứ chạy `pytest` trần.

**Con số test trong ảnh là ảnh chụp tại một thời điểm.** Tài liệu cố tình không
nhắc lại con số đó ở bất kỳ đâu khác: grep và test giữ đồng bộ được mọi bản sao
của một dữ kiện, trừ bản nằm trong pixel. Thêm test thì ảnh này lỗi thời — chụp
lại nếu muốn, nhưng không có câu văn nào mâu thuẫn với nó cả.

Số **tool** thì ngược lại: nó xuất hiện cả trong ảnh lẫn trong hai README, nên nó
được một phép thử canh giữ. `test_tool_contracts.py` đếm decorator `@mcp.tool`
bằng AST, đối chiếu với số tool server nạp được và với con số ghi trong cả hai
README; lệch một đơn vị là bộ test đỏ.

---

## Quy tắc khi thêm ảnh mới

1. **Lấy từ ứng dụng thật hoặc từ một lần chạy thật**, ở trạng thái do MCP server
   tạo ra.
2. **Ghi lại chuỗi lệnh** đã dùng, vào chính file này, đủ để người khác dựng lại.
3. **Chụp SAU CÙNG**, sau khi mọi thay đổi mã nguồn đã xong. Ảnh là bản sao duy
   nhất của một dữ kiện mà `grep` không sửa được và không test nào đọc được, nên
   chụp giữa chừng là tự tạo ra một bản sao lỗi thời — mà lại là bản người đọc
   tin nhất.
4. **Rà thông tin nhận diện trước khi commit** — tên người dùng trong đường dẫn,
   tên dự án của khách hàng, toạ độ trắc địa trên thanh trạng thái, tài khoản
   đăng nhập ở góc cửa sổ, số bản quyền. Ảnh đã commit thì nằm vĩnh viễn trong
   lịch sử git; xoá file ở commit sau không gỡ được nó ra.
5. **Ảnh chụp sai cửa sổ trông y hệt ảnh chụp đúng.** Đây là rủi ro lớn nhất của
   cả quy trình, và nó đã xảy ra hai lần trong lúc dựng chính những ảnh trên:

   - *Chọn nhầm phiên.* Hai cửa sổ ReCap có cùng tên tiến trình và cùng tiêu đề,
     `-ProcessName` không tách được chúng, và script cũ chỉ in cảnh báo rồi lấy
     cửa sổ đầu tiên — là phiên đang mở dữ liệu của một công trình thật, kèm tên
     tài khoản đăng nhập. Nay nhiều ứng viên là **lỗi**, kèm danh sách PID; dùng
     `-ProcessId` để chỉ đích danh.
   - *Cửa sổ không lên được tiền cảnh.* Ảnh là bản copy vùng màn hình, nên khi
     `SetForegroundWindow` bị Windows từ chối — chuyện rất thường — script cũ vẫn
     chụp, và thu được ảnh của bất cứ cửa sổ nào đang nằm trên. Nay script kiểm
     chứng `GetForegroundWindow()` trước khi copy pixel, và dừng lại nếu sai.

   Điểm chung của hai trường hợp: ảnh vẫn đúng kích thước, vẫn không trống, nên
   mọi tín hiệu "thành công" đều đúng. Chỉ có mở ảnh ra nhìn mới biết. **Hãy mở
   từng ảnh ra nhìn trước khi commit.**
6. **Che bằng cách tô đè, không làm mờ.** Làm mờ vẫn có thể đảo ngược một phần.
   Cả một dải giao diện cần biến mất thì cắt (`-Crop`) chứ đừng tô, vì một mảng
   trống lớn trông rõ là đã bị sửa.
