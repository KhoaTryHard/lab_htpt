# Trace mẫu cho phần trình bày lab

Các đoạn dưới đây rút gọn các dòng mà chương trình tạo ra. Thứ tự thực tế giữa P1,
P2 và P3 có thể khác vì giao tiếp đồng thời. WAL riêng của mỗi node mới là bằng
chứng thứ tự durable tại node đó; stdout không thay thế WAL.

## Commit

```powershell
python -m twopc demo --scenario all-commit
```

```text
[COORD] Tx 101 Started -> Log START_2PC (fsync)
[COORD] Tx 101 Multicast VOTE_REQUEST
[P1] Tx 101 Log VOTE_COMMIT (fsync)
[P2] Tx 101 Log VOTE_COMMIT (fsync)
[P3] Tx 101 Log VOTE_COMMIT (fsync)
[COORD] Tx 101 Recv VOTE_COMMIT from P1
[COORD] Tx 101 Recv VOTE_COMMIT from P2
[COORD] Tx 101 Recv VOTE_COMMIT from P3
[COORD] Tx 101 Log GLOBAL_COMMIT (fsync); reason=all_three_voted_commit
[COORD] Tx 101 Multicast GLOBAL_COMMIT
[P1] Tx 101 Recv GLOBAL_COMMIT -> Apply SET -> Reply ACK
[P2] Tx 101 Recv GLOBAL_COMMIT -> Apply SET -> Reply ACK
[P3] Tx 101 Recv GLOBAL_COMMIT -> Apply SET -> Reply ACK
[COORD] Tx 101 Log END_2PC (fsync) -> COMMIT completed, all ACKs
```

Kiểm chứng cuối: GET P1/P2/P3 trả 10/20/30, không còn khóa giao dịch.

## Abort do khóa xung đột (slide 54)

```powershell
python -m twopc demo --scenario vote-abort
```

```text
[COORD] Tx 101 Started -> Log START_2PC (fsync)
[COORD] Tx 101 Multicast VOTE_REQUEST
[P1] Tx 101 Log VOTE_COMMIT (fsync)
[P2] Tx 101 Log VOTE_COMMIT (fsync)
[P3] Tx 101 Lock Conflict! -> VOTE_ABORT
[P3] Tx 101 Log VOTE_ABORT (fsync)
[P3] Tx 101 Reply VOTE_ABORT
[COORD] Tx 101 Recv VOTE_ABORT from P3
[COORD] Tx 101 Log GLOBAL_ABORT (fsync); reason=vote_abort:P3
[COORD] Tx 101 Multicast GLOBAL_ABORT
[P1] Tx 101 Recv GLOBAL_ABORT -> Rollback -> Reply ACK
[P2] Tx 101 Recv GLOBAL_ABORT -> Rollback -> Reply ACK
[P3] Tx 101 Recv GLOBAL_ABORT -> Rollback -> Reply ACK
[COORD] Tx 101 Log END_2PC (fsync) -> ABORT completed, all ACKs
```

Kiểm chứng cuối: GET vẫn trả 0/0/0. P3 có `injected_conflicts=["x"]` để giải thích
nguyên nhân vote abort; đây không phải khóa của giao dịch 101 còn sót lại.

## Blocking và reboot coordinator

```powershell
python -m twopc demo --scenario coordinator-wait-crash
```

```text
[COORD] Tx 101 Recv VOTE_COMMIT from P1
[COORD] Tx 101 Recv VOTE_COMMIT from P2
[COORD] Tx 101 Recv VOTE_COMMIT from P3
[COORD] Tx 101 FAILPOINT before_decision -> process crash (exit 86)
```

Trong thời gian coordinator chết, harness kiểm tra liên tục cả ba node:

```text
state = READY
decision = null
locks = {"x": "101"}
GET x = 0
```

Sau restart bằng WAL cũ:

```text
[COORD] Tx 101 Log GLOBAL_ABORT (fsync); reason=recovery_without_decision
[COORD] Tx 101 RECOVER ABORT; retransmit durable decision
[COORD] Tx 101 Multicast GLOBAL_ABORT
[P1] Tx 101 Recv GLOBAL_ABORT -> Rollback -> Reply ACK
[P2] Tx 101 Recv GLOBAL_ABORT -> Rollback -> Reply ACK
[P3] Tx 101 Recv GLOBAL_ABORT -> Rollback -> Reply ACK
[COORD] Tx 101 Log END_2PC (fsync) -> ABORT completed, all ACKs
```

Nếu chuyển sang demo `coordinator-commit-crash`, WAL đã có GLOBAL_COMMIT trước
crash; recovery phải commit và giá trị cuối là 10/20/30. Đây là hai ca quan trọng
để chứng minh chương trình phân biệt **chưa quyết định** với **đã quyết định nhưng chưa phát**.
