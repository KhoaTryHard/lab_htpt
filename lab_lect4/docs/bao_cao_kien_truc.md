# BÁO CÁO KIẾN TRÚC
Ring Leader Election | Hệ thống phân tán | Python 3.12

## 1. Mục tiêu và yêu cầu
Lab mô phỏng năm tiến trình logic P1-P5 giao tiếp qua socket. Hệ thống bầu node đang hoạt động có ID lớn nhất, phát hiện leader ngừng hoạt động và tự bầu lại. Demo phải chứng minh P5 là leader ban đầu; sau khi thread P5 dừng, P1-P4 chọn P4. Đầu ra gồm mã nguồn, trace console và báo cáo hai trang; rubric lần lượt là thuật toán 40%, phát hiện lỗi 30%, trace 20%, báo cáo 10% [1].

## 2. Kiến trúc thread và socket
Một tiến trình hệ điều hành chứa năm thread không daemon. Mỗi thread sở hữu listener TCP, selector, leader_id, timer heartbeat, bộ đếm cục bộ và dữ liệu các vòng bầu cử. Mã chia thành protocol, transport, node, runner và trace. Node chỉ dùng cấu hình ID/địa chỉ của peer; mọi thông điệp giao thức truyền qua socket. Runner đọc snapshot có khóa để đánh giá, không gán leader hoặc thông báo node chết cho survivor.

```text
Vong logic: P1 -> P2 -> P3 -> P4 -> P5 -> P1
Sau crash: P1 -> P2 -> P3 -> P4 -------> P1
                                bo qua P5
```

TCP bind tại localhost, mặc định cổng 55000-55004. Mỗi lần gửi dùng một kết nối ngắn và một frame JSON kết thúc bằng newline. selectors xử lý connect, đọc/ghi từng phần và timer không chặn. Frame tối đa 8 KiB; frame sai bị ghi INVALID và đóng kết nối, listener tiếp tục chạy. Khóa chỉ bảo vệ snapshot và ghi log; thread của node là nơi duy nhất thay đổi trạng thái giao thức.

## 3. Hai lượt của Ring Election
Thuật toán dùng biến thể danh sách ID trong giáo trình [2], không dùng token mutual exclusion. Origin tạo ELECTION chứa ID của mình. Mỗi receiver thêm ID đúng một lần, rồi chuyển đến successor; nếu socket thất bại hoặc hết hạn, thử thành viên tiếp theo theo chiều vòng. Khi ELECTION trở về origin, origin chọn max(participants), chuyển thành COORDINATOR và cho thông báo đi thêm một vòng. Mỗi node chấp nhận leader và tiếp tục chuyển; origin kết thúc khi thông báo trở về.

ELECTION mang sender_id, origin_id, election_id và participants. COORDINATOR bổ sung leader_id; HEARTBEAT mang ID leader. election_id = (origin_id, local_counter) phân biệt các vòng độc lập, không biểu thị epoch toàn cục. Nhiều initiator được phép chạy đồng thời; node vẫn chuyển COORDINATOR của từng vòng dù leader_id không đổi.

## 4. Phát hiện lỗi và timer
Leader gửi heartbeat trực tiếp mỗi 0,5 giây. Follower chỉ làm mới thời hạn nếu heartbeat đến từ leader đã được chấp nhận; thiếu heartbeat 2 giây thì tự khởi xướng. Deadline connect/write là 0,4 giây tổng cho một lần thử, watchdog bầu cử 5 giây rồi tạo vòng mới. Tất cả dùng time.monotonic(). Các hằng số là mặc định triển khai cho localhost; timeout biểu thị nghi ngờ lỗi, có thể nhầm node chậm hoặc lỗi mạng [3].

<!-- PAGEBREAK -->

## 5. Kịch bản crash và trace thực tế
Sau khi cả năm listener sẵn sàng, P1 khởi xướng bootstrap. Runner chờ mọi node nhận P5 và các vòng khởi động hoàn tất, rồi đặt stop event riêng của P5; P5 đóng socket và thoát thread. CRASH được ghi sau join, không gửi thông điệp rời nhóm hoặc lệnh bầu lại. P1-P4 tự phát hiện thiếu heartbeat; P4 bỏ qua P5 bằng kết quả socket, không bằng registry sống/chết chung.

{{trace}}

Ví dụ vòng do P2 khởi xướng đi P2-P3-P4-P1-P2, thu [2,3,4,1], chọn P4 rồi thông báo thêm một vòng. Bộ ghi log tuần tự hóa từng record; thứ tự các dòng giữa thread có thể khác theo lịch chạy. Mỗi dòng có thời gian tương đối, node, loại sự kiện và ID vòng; JSONL giữ cả nguồn/đích, danh sách ID và timestamp UTC.

## 6. Kiểm thử và kết quả
{{evidence}}

Unittest kiểm tra tích lũy ID/max, origin và vòng đồng thời; TCP thật, bypass P5; frame phân mảnh/sai/quá dài và partial write; watchdog retry; 10 giây hoạt động khỏe không bầu lại; rollback khi cổng bận; ngắt chương trình đóng thread; chạy lại cùng cổng. Kiểm thử bốn initiator đồng bộ hóa tại thời điểm timeout và yêu cầu cả bốn vòng COORDINATOR hoàn tất, mỗi vòng chọn P4.

PASS yêu cầu P5 đã dừng, có timeout tự động, P1-P4 còn chạy, đều nhận P4 và các vòng đang tham gia hoàn tất liên tục ít nhất hai giây, trong giới hạn 15 giây sau crash. Các kiểm thử so sánh sự kiện và quan hệ nhân quả thay vì một thứ tự dòng log cố định.

## 7. Độ phức tạp và giới hạn
Một vòng với m node sống cần m lượt ELECTION và m lượt COORDINATOR, tổng 2m lượt chuyển thành công: 10 khi đủ năm node, 8 sau khi P5 dừng. Không tính heartbeat, gửi thất bại hoặc overhead TCP. Với k vòng độc lập, tổng là 2km; nếu k=m thì O(m²), nên nhận định O(m) khi đồng thời chỉ áp dụng khi k cố định. Danh sách ID tăng theo m; số thông điệp khác tổng lượng dữ liệu.

V1 giả định membership cố định, localhost, P5 crash-stop sau bootstrap và P1-P4 tiếp tục sống/liên lạc trong hai lượt bầu lại. Không đặc tả recovery, partition, thay đổi thành viên hay crash giữa vòng; không bảo đảm leader duy nhất khi mạng phân hoạch. Đây là mô phỏng tiến trình logic bằng thread, không có cách ly địa chỉ bộ nhớ của năm tiến trình hệ điều hành.

## Tài liệu tham khảo
[1] Lect04_processes_and_coordination, slide 25-26 (Ring), 32-33 (lab và rubric).
[2] Tanenbaum & van Steen, Distributed Systems: Principles and Paradigms, 2nd ed., §6.5.1, tr. 266 (trang PDF 285); §3.1 (thread), §4.3.1 (socket).
[3] Cùng giáo trình, §8.2.4, tr. 335-336 (trang PDF 354-355), Failure Detection.
