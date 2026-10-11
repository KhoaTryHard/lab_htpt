"""Configuration shared by CLI, servers and the process test harness."""

import json
from dataclasses import dataclass
from pathlib import Path

PARTICIPANTS = ("P1", "P2", "P3")


@dataclass(frozen=True)
class Config:
    addresses: dict
    data_dir: Path
    initial_values: dict
    request_timeout: float = 2.0
    vote_timeout: float = 5.0
    retry_interval: float = 1.0

    @classmethod
    def load(cls, path="config.json"):
        path = Path(path).resolve()
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("Configuration must be a JSON object")
        addresses = raw.get("addresses")
        if not isinstance(addresses, dict) or set(addresses) != {"COORD", *PARTICIPANTS}:
            raise ValueError("addresses must contain exactly COORD, P1, P2, P3")
        endpoints = set()
        for address in addresses.values():
            if (not isinstance(address, dict) or not isinstance(address.get("host"), str)
                    or not address["host"] or type(address.get("port")) is not int
                    or not 1 <= address["port"] <= 65535):
                raise ValueError("Invalid host/port")
            endpoints.add((address["host"], address["port"]))
        if len(endpoints) != 4:
            raise ValueError("Each node needs a distinct socket address")
        if not isinstance(raw.get("data_dir", "data"), str):
            raise ValueError("data_dir must be a path string")
        initial = raw.get("initial_values", {"x": 0})
        if not isinstance(initial, dict) or any(
            not isinstance(k, str) or not k or type(v) is not int
            for k, v in initial.items()
        ):
            raise ValueError("initial_values must map nonempty strings to integers")
        timing = {}
        for name, default in (("request_timeout", 2.0), ("vote_timeout", 5.0),
                              ("retry_interval", 1.0)):
            value = raw.get(name, default)
            if type(value) not in (int, float) or not 0 < value <= 3600:
                raise ValueError(f"Invalid {name}")
            timing[name] = float(value)
        return cls(addresses, (path.parent / raw.get("data_dir", "data")).resolve(),
                   initial, **timing)

    def address(self, node):
        item = self.addresses[node]
        return item["host"], item["port"]
