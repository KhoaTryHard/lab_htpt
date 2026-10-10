# lab_htpt — Thực hành Hệ thống phân tán

Repo tổng hợp các bài thực hành môn Hệ thống phân tán. Mỗi lab có thư mục riêng, tài liệu và cách chạy độc lập. Lịch sử commit của các repo `lab_lect1`, `lab_lect2`, `lab_lect3` được giữ lại khi gom vào repo này.

## Danh sách lab

| Thư mục | Chủ đề | Nội dung hiện có |
| --- | --- | --- |
| [lab_lect1](lab_lect1/README.md) | Latency hiding | Ứng dụng đăng ký client/server, proxy mô phỏng độ trễ, benchmark và báo cáo |
| [lab_lect2](lab_lect2/README.md) | Distributed communication and naming | Mini-DHT theo Chord, registry, messaging client và Docker Compose |
| [lab_lect3](lab_lect3/README.md) | Time and synchronization | Mô phỏng Lamport clock và vector clock |
| [lab_lect4](lab_lect4/README.md) | Processes and coordination | Ring leader election, kiểm thử và báo cáo kiến trúc |
| [lab_lect5](lab_lect5/) | Transactions and fault tolerance | Chỉ có tài liệu PDF; chưa có mã triển khai |

Slide bài giảng và sách tham khảo được giữ trong từng thư mục lab. Báo cáo lab 1 nằm ở `lab_lect1/reports/`; báo cáo lab 4 nằm ở `lab_lect4/output/pdf/` và bản nguồn ở `lab_lect4/docs/`.

## Chuẩn bị

- Git để clone repo.
- Node.js 24 LTS và npm cho lab 1; chạy `npm ci` để cài đúng dependency trong lockfile.
- Python 3.12 cho kiểm thử lab 2–4. Lab 2 được thiết kế cho Python 3.11 trở lên; mã mô phỏng của các lab này dùng thư viện chuẩn.
- Docker và Docker Compose nếu chạy demo cụm DHT của lab 2.
- Chromium của Playwright để chạy kiểm thử giao diện lab 1.
- Dependency trong `lab_lect4/requirements-report.txt` chỉ cần khi tạo lại báo cáo PDF; xem README của lab 4 để biết yêu cầu font.

Các lệnh dưới đây dùng PowerShell trên Windows và bắt đầu từ thư mục gốc repo:

```powershell
git clone https://github.com/KhoaTryHard/lab_htpt.git
cd lab_htpt
```

## Chạy và kiểm thử

### Lab 1 — Latency hiding

```powershell
Push-Location lab_lect1
npm ci
npm test
npx playwright install chromium
npm run demo
```

Mở <http://127.0.0.1:3000>. Khi demo đang chạy, mở terminal PowerShell thứ hai tại `lab_lect1` và chạy `npm run test:e2e`. Dừng demo bằng `Ctrl+C`, rồi chạy `Pop-Location` ở terminal đầu để quay về thư mục gốc.

`npm run bench` tạo lại kết quả benchmark và ghi đè báo cáo trong `reports/`; xem [hướng dẫn lab 1](lab_lect1/README.md) trước khi chạy.

### Lab 2 — Mini-DHT

```powershell
Push-Location lab_lect2
$previousPythonPath = $env:PYTHONPATH
try {
    $env:PYTHONPATH = "src"
    py -3.12 -m unittest discover -s tests -v
} finally {
    $env:PYTHONPATH = $previousPythonPath
    Pop-Location
}
```

Để chạy cụm Docker, vào `lab_lect2`, chạy `docker compose up -d --build` và làm theo [hướng dẫn lab 2](lab_lect2/README.md) để đợi vòng ổn định, tra cứu và gửi tin. Dừng cụm bằng `docker compose down` trong cùng thư mục.

### Lab 3 — Đồng hồ logic

```powershell
Push-Location lab_lect3
py -3.12 -m unittest discover -s tests -v
py -3.12 -m logical_clock_simulator --mode demo --output-dir output-demo
Pop-Location
```

### Lab 4 — Ring leader election

```powershell
Push-Location lab_lect4
py -3.12 -m unittest discover -s tests -v
py -3.12 -m leader_election --base-port 55000 --log-dir logs
Pop-Location
```

Demo dùng cổng localhost 55000–55004 và tự kết thúc sau khi xác minh bầu được P4 khi P5 dừng. Xem [hướng dẫn lab 4](lab_lect4/README.md) để theo dõi trace, chạy stress test và tạo lại báo cáo.

### Lab 5 — Tài liệu

Đọc slide `lab_lect5/Lect05_transactions_and_fault_tolerance.pdf`. Lab này chưa có chương trình hoặc bộ kiểm thử.

## Kiểm tra đã ghi nhận khi gom repo

Ngày 11/10/2026, các bộ kiểm thử hiện có gồm 2 test lab 1, 15 test lab 2, 10 test lab 3 và 20 test lab 4 đều pass. Lab 2 có thể in lỗi nền `asyncio` và cảnh báo socket chưa đóng trên Python 3.12 dù bộ test kết thúc với `OK`; hiện tượng này đã xuất hiện trước khi gom repo.

## Thêm lab và lưu thay đổi

Thêm lab mới thành thư mục `lab_lect6/`, `lab_lect7/`… cùng README hướng dẫn chạy, rồi cập nhật bảng mục lục ở trên. Các thư mục lab là thư mục thông thường, dùng chung Git ở cấp gốc.

```powershell
git status
git add lab_lect6 README.md
git commit -m "feat: add lab_lect6"
git push origin main
```

Dependency, cache, dữ liệu chạy thử, log và file tạm được bỏ qua bởi `.gitignore` gốc và quy tắc riêng của từng lab. Tài liệu PDF và báo cáo hoàn chỉnh được lưu trong repo.
