# Phân tích benchmark

Ma trận gồm 720 lượt đo: 30 lượt cho mỗi RTT, tình huống và chế độ. Backend và proxy dùng Python/FastAPI. RTT được chia đều trước request và response.

| RTT | Server validation median (ms) | Local validation median (ms) | Request server/local |
|---:|---:|---:|---:|
| 0 | 15.9 | 0.1 | 30 / 0 |
| 200 | 220.2 | 0.1 | 30 / 0 |
| 500 | 527.9 | 0.1 | 30 / 0 |
| 1000 | 1026.2 | 0.1 | 30 / 0 |

Với lỗi định dạng, local validation phản hồi trong thời gian xử lý JavaScript và không gửi request. Server validation cần một round-trip nên thời gian tăng theo RTT. Dữ liệu hợp lệ hoặc email trùng vẫn cần server trong cả hai chế độ.

## Liên hệ 8 giả định sai

- **Latency is zero:** kiểm chứng trực tiếp bằng chênh lệch khi RTT tăng.
- **Bandwidth is infinite / Transport cost is zero:** local validation tránh request và body không cần thiết cho lỗi định dạng.
- **The network is reliable:** UI xử lý lỗi kết nối; local validation vẫn phát hiện lỗi định dạng nhưng không đảm bảo đăng ký được khi mạng hỏng.
- **The network is secure / homogeneous / topology does not change / one administrator:** không được giải quyết bởi validation; cần cơ chế riêng.

Đây là mô phỏng localhost, không đại diện cho packet loss, jitter, TLS handshake hoặc WAN bandwidth. Byte đo là body, không gồm header/TLS và chi phí tải JavaScript ban đầu. feedback_ms đo đến cập nhật DOM, chưa tính lúc trình duyệt hoàn tất paint.
