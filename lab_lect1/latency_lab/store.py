"""In-memory registrations; intentionally reset on every server restart."""


class RegistrationStore:
    def __init__(self) -> None:
        self.records: dict[str, dict[str, str]] = {}
        self.reset()

    def reset(self) -> None:
        self.records.clear()
        self.records["taken@example.test"] = {
            "id": "seed-taken",
            "fullName": "Tài khoản mẫu",
            "email": "taken@example.test",
        }

    def has(self, email: str) -> bool:
        return email in self.records

    def add(self, record: dict[str, str]) -> None:
        self.records[record["email"]] = record

    def size(self) -> int:
        return len(self.records)
