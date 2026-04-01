import csv
from pathlib import Path


class CsvLogger:
    def __init__(self, filepath: str, headers: list[str]):
        self.path = Path(filepath)
        self.path.parent.mkdir(parents=True, exist_ok=True)

        if not self.path.exists():
            with self.path.open("w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(headers)

    def append(self, row: list) -> None:
        with self.path.open("a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(row)