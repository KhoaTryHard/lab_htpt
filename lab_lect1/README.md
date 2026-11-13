# Practical Lab: Architecting Latency Hiding

Lab trong `Lect01_Overview.pdf` so sánh cùng một form đăng ký ở hai chế độ:
**A · Server validation** gửi dữ liệu đến server rồi nhận lỗi; **B · Local validation**
kiểm tra định dạng bằng JavaScript ngay trong trình duyệt trước khi gửi.
Server luôn kiểm tra lại dữ liệu và quyết định lỗi nghiệp vụ như email đã tồn tại.

## Tech stack và cấu trúc

- **Python 3.11+, FastAPI, Uvicorn:** API đăng ký và phục vụ HTML/CSS/JavaScript.
- **HTTPX, asyncio:** proxy HTTP mô phỏng RTT, chia đều độ trễ trước request và response.
- **pytest:** test validation, API, proxy và vòng đời tiến trình.
- **Playwright Python:** benchmark qua trình duyệt thật.
- **Playwright Test (Node.js):** test E2E và bảng điều khiển Playwright UI.

`latency_lab/` chứa server, proxy, store, validation, trình chạy demo, benchmark và
phần sinh báo cáo. `public/` là giao diện; `shared/validation.mjs` là validation
được tải xuống trình duyệt. `test/` chứa pytest, dữ liệu đối chiếu Python/JavaScript
và test E2E. Các bản server/proxy/benchmark Node.js đã được thay bằng Python.

## Cài đặt trên Windows / PowerShell

Cần Python 3.11 trở lên, [uv](https://docs.astral.sh/uv/getting-started/installation/)
và Node.js 20 trở lên. Mở terminal tại thư mục `lab_lect1`:

```powershell
uv sync
npm ci
uv run python -m playwright install chromium
npx playwright install chromium
```

`uv sync` tạo môi trường `.venv` từ `pyproject.toml` và `uv.lock`, gồm dependency test.
Không cần activate `.venv` khi dùng `uv run`. Hai lệnh cài Chromium phục vụ hai bản
Playwright Python/Node.js, có thể dùng các phiên bản trình duyệt khác nhau.

## Chạy demo

```powershell
uv run python -m latency_lab.demo
```

Mở <http://127.0.0.1:3000/?rtt=500>. Nhấn `Ctrl+C` để dừng cả server và proxy.
Demo đợi hai dịch vụ sẵn sàng và báo lỗi nếu cổng đã được sử dụng.

| Dịch vụ | Cổng mặc định |
|---|---:|
| Proxy / giao diện demo | 3000 |
| Server đăng ký | 3001 |
| Proxy / server benchmark | 3100 / 3101 |
| Proxy / server E2E | 3200 / 3201 |

Có thể đặt `SERVER_PORT`, `PROXY_PORT`, `RTT_MS` trước khi chạy demo:

```powershell
$env:RTT_MS = '500'
uv run python -m latency_lab.demo
```

Khi URL không có `?rtt`, giao diện lấy RTT mặc định từ proxy qua `/api/lab-config`.
Nếu có `?rtt=1000`, giá trị trên URL ghi đè mặc định bằng header `x-lab-rtt`.
RTT áp dụng cho `POST /api/register`; tải trang và health check không bị trì hoãn.
RTT phải hữu hạn và không âm; header không hợp lệ được proxy trả 400.

Chạy từng dịch vụ riêng ở hai terminal bằng `uv run python -m latency_lab.server`
và `uv run python -m latency_lab.proxy`. Truy cập trực tiếp server sẽ bỏ qua proxy RTT.

## Kiểm thử thủ công trên giao diện

Mở URL demo với `?rtt=1000`. Nhập họ tên `Nguyễn Văn A`, mật khẩu và xác nhận
`password1`; đổi email theo các trường hợp sau. Trong DevTools (`F12`), chọn
**Network → Fetch/XHR** và xóa danh sách request trước mỗi lần thử.

| Email / tình huống | Chế độ A | Chế độ B |
|---|---|---|
| `broken` | Chờ khoảng 1 giây, request 422 | Báo lỗi ngay, không gửi request |
| Email mới hợp lệ | Chờ khoảng 1 giây, request 201 | Chờ khoảng 1 giây, request 201 |
| `taken@example.test` | Request 409, báo trùng | Request 409, báo trùng |

Dùng email mới cho mỗi lượt đăng ký thành công; gửi lại email đó sẽ báo trùng.
Dữ liệu lưu trong RAM và được khởi tạo lại khi server khởi động. Email mẫu luôn tồn tại.

## Test tự động và Playwright UI

```powershell
uv run pytest
npm run test:e2e
npm run test:ui
```

Playwright tự khởi động Python server/proxy ở cổng **3200/3201**, nên không cần
chạy demo trước. Test tự đợi dịch vụ sẵn sàng, kể cả khi bấm chạy sớm trong UI.
Trong Playwright UI, nhấn **Run all** hoặc chọn từng test để
xem thao tác, request và kết quả. Nếu cổng này bị chiếm, dừng phiên test cũ trước.

pytest cần Node.js để chạy đối chiếu validation Python/JavaScript trên cùng dữ liệu.
E2E kiểm tra hai chế độ với dữ liệu sai/hợp lệ/trùng, gửi lại form, lỗi mạng và độ trễ:
RTT 500 ms thì server-invalid ít nhất 450 ms; client-invalid dưới 250 ms, 0 request.

## Benchmark và báo cáo

```powershell
uv run python -m latency_lab.benchmark
```

Benchmark tự khởi động và dừng dịch vụ ở **3100/3101**. Ma trận mặc định:
4 RTT (0/200/500/1000 ms) × 3 tình huống (sai định dạng/hợp lệ/trùng email)
× 2 chế độ × 30 lượt = **720 lượt**. Kho đăng ký được reset trước mỗi lượt.
Nếu outcome, status hoặc số request sai, benchmark dừng và báo lỗi.

Kết quả UTF-8 nằm trong `reports/benchmark.csv`, `reports/summary.json`,
`reports/analysis.md` và `reports/report.html`. Mở HTML trực tiếp bằng trình duyệt
để xem 3 nhóm biểu đồ và 24 dòng tổng hợp. HTML được sinh với cấu trúc xuống dòng,
thụt lề; không cần format lại sau mỗi benchmark.

Chạy smoke 48 lượt vào thư mục riêng bằng PowerShell:

```powershell
$env:BENCH_ITERATIONS = '2'
$env:BENCH_OUTPUT_DIR = '.forgewright/benchmark-smoke'
uv run python -m latency_lab.benchmark
Remove-Item Env:BENCH_ITERATIONS
Remove-Item Env:BENCH_OUTPUT_DIR
```

## API và cấu hình dữ liệu

- `GET /api/health`: `{ "ok": true, "registrations": 1 }` khi mới khởi động.
- `POST /api/register`: JSON `fullName`, `email`, `password`, `confirmPassword`.
  Trả **201** và `registrationId`, **422 / VALIDATION_ERROR** kèm lỗi theo trường,
  hoặc **409 / EMAIL_EXISTS** nếu email đã tồn tại. Body giới hạn 16 KiB.
- `POST /api/test/reset`: chỉ bật khi `ALLOW_TEST_RESET=1`; tắt thì trả 404.
  Demo/benchmark/E2E bật reset để phục vụ thực hành. Chạy server riêng mặc định tắt.
- `GET /api/lab-config`: proxy trả `{ "rttMs": 0 }` theo `RTT_MS`.

Server chạy một worker để kho RAM nhất quán. Validation hai phía giữ quy tắc tên
2–80, mật khẩu 8–64, email tối đa 254 đơn vị UTF-16 và xác nhận mật khẩu trùng.
Không lưu mật khẩu; đây là demo đăng ký, không phải hệ thống đăng nhập.

## Diễn giải kết quả

Với lỗi định dạng, local validation có `http_request_count = 0` và phản hồi gần như
không đổi khi RTT tăng. Dữ liệu hợp lệ hoặc email trùng vẫn cần server trong cả hai
chế độ. Điều này minh họa giả định sai **latency is zero**, và lợi ích giảm request
liên quan đến **bandwidth is infinite / transport cost is zero**. Local validation
không giải quyết reliability, security, heterogeneity, topology hay administrative domains.

`feedback_ms` được đo trong frontend từ lúc bắt đầu xử lý submit đến cập nhật DOM,
chưa tính hoàn tất paint. Median/P95 dùng nearest-rank như bản Node.js cũ.
Byte đo là body, không gồm header/TLS và chi phí tải JavaScript ban đầu.
Mô phỏng trên một máy không đại diện cho packet loss, jitter hoặc WAN thật.

Các lệnh quen thuộc `npm start`, `npm run start:proxy`, `npm run demo`, `npm test`
và `npm run bench` vẫn được giữ để gọi sang Python qua `uv`.
