# Lab 2PC Simulator

Mô phỏng Two-Phase Commit bằng **Python 3.11+, bốn tiến trình và TCP socket thật**.
Mỗi node có WAL riêng, ghi đồng bộ bằng `flush()` và `os.fsync()`; khởi động lại
phục hồi cả giao dịch và dữ liệu key-value. Không có thư viện runtime bên ngoài.

## Chạy nhanh

Mở PowerShell tại thư mục dự án:

```powershell
python --version
python -m twopc demo --scenario all-commit
python -m twopc demo --scenario vote-abort
python -m twopc demo --scenario coordinator-wait-crash
python -m unittest discover -s tests -v
```

Mỗi demo tự khởi động COORD, P1, P2, P3; chọn bốn cổng khả dụng; xác nhận kết quả;
đóng các tiến trình khi xong. WAL, cấu hình và trace được giữ trong một thư mục
mới dưới `runs/`. Demo trả mã thoát khác 0 nếu kết quả không đạt.

```powershell
python -m twopc demo --scenario all --quiet
python -m twopc demo --scenario participant-ready-crash --output-dir runs/demo-recovery
```

`--output-dir` phải mới hoặc rỗng, để không ghi đè bằng chứng của lần chạy cũ.
Trace được trình bày riêng từng node; thứ tự giữa các nhóm không phải thứ tự thời gian toàn cục.

## Chạy thủ công trong năm terminal

Chạy từ cùng thư mục dự án. Bốn terminal đầu chạy server:

```powershell
# Terminal 1
python -m twopc participant --id P1 --config config.json
# Terminal 2
python -m twopc participant --id P2 --config config.json
# Terminal 3
python -m twopc participant --id P3 --config config.json
# Terminal 4
python -m twopc coordinator --config config.json
```

Terminal 5 gửi yêu cầu và đọc kết quả:

```powershell
python -m twopc submit --tx-id 101 --file transaction.json
python -m twopc status --tx-id 101
python -m twopc status --node P1 --tx-id 101
python -m twopc get --node P1 --key x
python -m twopc get --node P2 --key x
python -m twopc get --node P3 --key x
```

Kết quả commit: P1 có `x=10`, P2 có `x=20`, P3 có `x=30`;
coordinator báo `decision=GLOBAL_COMMIT`, `completed=true`.

`submit` chờ tối đa 15 giây để đủ ACK. Dùng `--wait 0` để chỉ nhận trạng thái
chấp nhận yêu cầu, hoặc `--wait 30` để tăng thời gian chờ. Hết thời gian chờ
**không** có nghĩa giao dịch abort: dùng `status` với cùng `tx_id`.

Mã thoát client: `0` là yêu cầu thành công (hoặc giao dịch đã hoàn tất, gồm cả abort),
`1` là lỗi, `2` là chưa xác nhận/chưa hoàn tất trong thời hạn. `--wait 0` có thể
trả `0` khi trạng thái còn `WAIT`; luôn đọc trường `completed`.

Gửi lại cùng ID và cùng payload trả giao dịch hiện có. Muốn giao dịch mới, dùng ID mới.
Cùng ID và payload khác bị từ chối. Coordinator chỉ nhận giao dịch mới sau khi giao dịch trước đủ ACK.

## Mô phỏng lỗi và phục hồi

Dừng server tương ứng bằng `Ctrl+C` trước khi đổi tham số chạy thủ công. Dùng ID
giao dịch mới cho lần demo mới; giữ nguyên WAL khi kiểm tra recovery.

Để P3 bỏ phiếu abort vì khóa xung đột, khởi động P3 với:

```powershell
python -m twopc participant --id P3 --lock-key x
```

Để coordinator chết khi tất cả participant đã `READY`, dùng:

```powershell
python -m twopc coordinator --failpoint before_decision --fault-tx 201
python -m twopc submit --tx-id 201 --file transaction.json --wait 0
```

Sau crash, `status --node P1 --tx-id 201` vẫn báo `READY` và giữ khóa `x`;
`get` vẫn trả giá trị committed trước giao dịch. Khởi động lại coordinator bằng lệnh
bình thường, **giữ nguyên cấu hình và thư mục `data`**:

```powershell
python -m twopc coordinator --config config.json
```

Coordinator tìm thấy `START_2PC` nhưng chưa có quyết định sẽ ghi `GLOBAL_ABORT` rồi gửi lại.
Nếu WAL đã có `GLOBAL_COMMIT`, coordinator gửi lại commit. Không thêm failpoint
vào lệnh restart, tránh tạo lại lỗi cho giao dịch đang phục hồi.

Các failpoint crash kết thúc tiến trình ngay bằng `os._exit(86)`. Các điểm `omit_vote`
và `drop_ack` chỉ làm mất phản hồi; chúng không kết thúc tiến trình.

| Vai trò | `--failpoint` | Thời điểm/hành vi |
|---|---|---|
| COORD | `after_start` | Sau `START_2PC` bền vững, trước prepare |
| COORD | `before_decision` | Sau đủ ba phiếu commit, trước log quyết định |
| COORD | `after_decision` | Sau log quyết định, trước gửi quyết định |
| COORD | `after_first_decision` | Sau lượt gửi quyết định tới P1, trước gửi tới P2/P3 |
| Participant | `after_init` | Sau log INIT, trước bỏ phiếu |
| Participant | `after_prepare` | Sau log VOTE_COMMIT, trước trả phiếu |
| Participant | `after_decision` | Sau log quyết định, trước áp dụng dữ liệu/ACK |
| Participant | `omit_vote` | Bỏ tất cả phản hồi vote cho giao dịch được chọn |
| Participant | `drop_ack` | Bỏ ACK đầu tiên sau khi đã xử lý quyết định |

`--fault-tx` giới hạn lỗi cho một ID; nếu bỏ tùy chọn này, failpoint áp dụng cho mọi
giao dịch phù hợp. Dùng `--failpoint` nhiều lần nếu cần. `--lock-key` mô phỏng tài
nguyên bị một tác vụ bên ngoài giữ; danh sách `locks` trong STATUS chỉ liệt kê khóa giao dịch thật.

## Các demo có sẵn

| Kịch bản | Kết quả |
|---|---|
| `all-commit` | Đủ ba phiếu commit; dữ liệu 10/20/30 |
| `vote-abort` | P3 xung đột khóa; dữ liệu 0/0/0 |
| `vote-timeout` | P3 không trả vote; coordinator abort khi hết hạn |
| `participant-ready-crash` | P1 crash trước trả vote; restart từ READY, nhận abort |
| `coordinator-wait-crash` | Ba node giữ READY khi coordinator chết; recovery abort |
| `coordinator-commit-crash` | Quyết định commit đã bền vững; recovery commit |
| `coordinator-partial-crash` | Coordinator crash sau lượt gửi tới P1; recovery gửi lại |
| `participant-decision-crash` | P1 replay commit sau crash trước áp dụng dữ liệu |
| `lost-ack` | ACK đầu tiên bị mất; retry vẫn hoàn tất |
| `restart-all` | Commit, abort, restart cả cụm, rồi chạy giao dịch mới |

## Cấu hình và cấu trúc

`config.json` đặt địa chỉ COORD/P1/P2/P3, `data_dir`, dữ liệu ban đầu và các timeout.
Đường dẫn dữ liệu tương đối được tính từ vị trí file cấu hình. Mặc định dùng localhost
9000–9003, request timeout 2 giây, hạn bỏ phiếu chung 5 giây và retry mỗi 1 giây.
Demo/test dùng thời gian ngắn hơn trong cấu hình riêng.

Mỗi WAL bắt đầu bằng META ghi danh tính, membership và dữ liệu ban đầu. Không đổi
`initial_values` khi dùng WAL cũ. Để thực hiện một lần chạy sạch, chọn `data_dir` mới
trong cấu hình mới; không xóa WAL đang phục vụ một lần recovery.

```text
twopc/
  config.py, protocol.py     cấu hình, validation và TCP JSON framing
  wal.py, state.py           stable log, state machine và replay
  nodes.py                  coordinator và participant
  harness.py, cli.py         quản lý tiến trình, kịch bản và CLI
tests/                      unit, TCP framing và integration test
docs/design-report.md       báo cáo thiết kế, dẫn nguồn và rubric
docs/sample-traces.md       trace minh họa commit, abort, recovery
config.json                 cấu hình chạy thủ công
transaction.json            giao dịch mẫu
```

Xem [báo cáo thiết kế](docs/design-report.md) và [trace mẫu](docs/sample-traces.md).
Giao dịch chỉ dùng SET giá trị nguyên, ít nhất một thao tác cho mỗi participant;
mỗi key chỉ xuất hiện một lần trong nhóm của một node. `GET` đọc dữ liệu committed
tại một node; không cung cấp snapshot nhất quán xuyên cả cụm.

Mô hình lỗi là process crash/recovery trên ổ đĩa còn dữ liệu. Một instance cho mỗi
danh tính node; các node thuộc cùng một môi trường lab tin cậy. Recovery hỏi
coordinator, không có bầu coordinator mới. Khi coordinator chưa phục hồi, READY
có thể bị chặn vô thời hạn: đây là thuộc tính cần thể hiện của 2PC.
