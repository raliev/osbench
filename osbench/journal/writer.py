"""Buffered writing of the event journal to parquet in chunks."""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from .schema import ARROW_SCHEMA, COLUMN_NAMES, JType

_NA = {
    "t": 0, "type": 0, "order_id": -1, "line_id": -1, "sku": -1, "warehouse": -1,
    "parcel": -1, "qty": -1, "cost": float("nan"), "promised_at": -1, "planned_at": -1,
    "delivered_at": -1, "policy_time_ms": float("nan"),
}


class JournalWriter:
    """Accumulates rows by column and writes a row group when the buffer fills."""

    def __init__(self, path: Path, flush_rows: int = 200_000):
        self.path = Path(path)
        self.flush_rows = flush_rows
        self._cols: dict[str, list] = {c: [] for c in COLUMN_NAMES}
        self._n = 0
        self._writer: pq.ParquetWriter | None = None

    def log(self, t: int, type_: JType, **fields) -> None:
        row = dict(_NA)
        row["t"] = int(t)
        row["type"] = int(type_)
        for k, v in fields.items():
            row[k] = v
        for c in COLUMN_NAMES:
            self._cols[c].append(row[c])
        self._n += 1
        if self._n >= self.flush_rows:
            self._flush()

    def _flush(self) -> None:
        if self._n == 0:
            return
        batch = pa.table({c: self._cols[c] for c in COLUMN_NAMES}, schema=ARROW_SCHEMA)
        if self._writer is None:
            self._writer = pq.ParquetWriter(self.path, ARROW_SCHEMA, compression="zstd")
        self._writer.write_table(batch)
        self._cols = {c: [] for c in COLUMN_NAMES}
        self._n = 0

    def close(self) -> None:
        self._flush()
        if self._writer is not None:
            self._writer.close()
            self._writer = None
        elif not self.path.exists():
            # Empty journal — create the file with the schema.
            pq.write_table(pa.table({c: [] for c in COLUMN_NAMES}, schema=ARROW_SCHEMA), self.path)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
