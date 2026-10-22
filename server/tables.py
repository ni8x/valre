"""Parquet access: schema discovery + row-group aware paging."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

MAIN_TABLES = [
    "fields",
    "movement",
    "actors",
    "net_guids",
    "events",
    "partials",
]
CHECKPOINT_TABLES = [
    "checkpoint_fields",
    "checkpoint_actors",
    "checkpoint_net_guids",
    "checkpoint_blocks",
    "checkpoint_guid_entries",
    "checkpoint_export_groups",
    "checkpoint_export_fields",
]
ALL_TABLES = MAIN_TABLES + CHECKPOINT_TABLES

TABLE_INFO: dict[str, dict[str, str]] = {
    "fields": {
        "label": "Fields",
        "desc": "Every replicated property / RPC parameter, time-ordered. The master stream.",
    },
    "movement": {
        "label": "Movement",
        "desc": "Per-tick character transforms at 128 Hz: position, rotation, velocity.",
    },
    "actors": {
        "label": "Actors",
        "desc": "Actor open / close / dormant lifecycle with spawn transforms.",
    },
    "net_guids": {
        "label": "Net GUIDs",
        "desc": "NetGUID -> object path map plus containment chain.",
    },
    "events": {
        "label": "Events",
        "desc": "Server timeline: deaths, ults, round starts, spike plants/defuses/explosions.",
    },
    "partials": {
        "label": "Partials",
        "desc": "Rejected / fragmented bunch fragments retained for diagnostics.",
    },
    "checkpoint_fields": {
        "label": "CKPT Fields",
        "desc": "Properties captured in checkpoint snapshots.",
    },
    "checkpoint_actors": {
        "label": "CKPT Actors",
        "desc": "Actor lifecycle seen inside checkpoints.",
    },
    "checkpoint_net_guids": {
        "label": "CKPT GUIDs",
        "desc": "NetGUID map derived from checkpoints.",
    },
    "checkpoint_blocks": {
        "label": "CKPT Blocks",
        "desc": "Checkpoint content blocks with decode status.",
    },
    "checkpoint_guid_entries": {
        "label": "CKPT GUID Entries",
        "desc": "Raw guid-entry rows from checkpoint package maps.",
    },
    "checkpoint_export_groups": {
        "label": "CKPT Groups",
        "desc": "Export-group cache state at checkpoint time.",
    },
    "checkpoint_export_fields": {
        "label": "CKPT Export Fields",
        "desc": "Export field cache state at checkpoint time.",
    },
}

_HIDE = {"raw_payload", "raw_bits", "raw_data"}


def arrow_to_simple(dt: pa.DataType) -> str:
    if pa.types.is_integer(dt):
        return "int"
    if pa.types.is_floating(dt):
        return "float"
    if pa.types.is_boolean(dt):
        return "bool"
    if pa.types.is_binary(dt) or pa.types.is_large_binary(dt):
        return "bytes"
    if pa.types.is_string(dt) or pa.types.is_large_string(dt):
        return "string"
    if pa.types.is_timestamp(dt):
        return "time"
    if pa.types.is_dictionary(dt):
        return arrow_to_simple(dt.value_type)
    return str(dt)


class TableReader:
    """Lazily opens a parquet file and serves schema + paged rows."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._pf: pq.ParquetFile | None = None

    @property
    def pf(self) -> pq.ParquetFile:
        if self._pf is None:
            self._pf = pq.ParquetFile(self.path)
        return self._pf

    @property
    def num_rows(self) -> int:
        return self.pf.metadata.num_rows

    def columns(self) -> list[dict[str, Any]]:
        schema = self.pf.schema_arrow
        out = []
        for i, field in enumerate(schema):
            out.append(
                {
                    "name": field.name,
                    "type": arrow_to_simple(field.type),
                    "hidden": field.name in _HIDE,
                }
            )
        return out

    def _rows_from_table(self, table: pa.Table) -> list[dict[str, Any]]:
        cols = [c for c in table.column_names if c not in _HIDE]
        slim = table.select(cols)
        return slim.to_pylist()

    def _sorted(self, table: pa.Table, sort: str, descending: bool) -> pa.Table:
        """Sort a table by one column. Dictionary columns must be decoded first:
        pyarrow has no sort kernel for dictionary-encoded arrays."""
        if sort not in table.column_names:
            return table
        field = table.schema.field(sort)
        if pa.types.is_dictionary(field.type):
            table = table.set_column(
                table.schema.get_field_index(sort), sort, table.column(sort).cast(pa.string())
            )
        order = "descending" if descending else "ascending"
        idx = pc.sort_indices(table, sort_keys=[(sort, order)])
        return table.take(idx)

    def page(
        self,
        offset: int = 0,
        limit: int = 100,
        filters: Iterable[tuple[str, str, Any]] | None = None,
        sort: str | None = None,
        descending: bool = False,
    ) -> dict[str, Any]:
        filters = list(filters or [])
        sortable = sort is not None and sort in self.pf.schema_arrow.names

        if filters or sortable:
            table = pq.read_table(self.path)
            if filters:
                mask = None
                for col, op, val in filters:
                    if col not in table.column_names:
                        continue
                    arr = table.column(col)
                    col_t = table.schema.field(col).type
                    if pa.types.is_dictionary(col_t):
                        arr = arr.cast(pa.string())
                    if op == "eq":
                        m = pc.equal(arr, val)
                    elif op == "ne":
                        m = pc.not_equal(arr, val)
                    elif op == "contains":
                        m = pc.match_substring(arr.cast(pa.string()), str(val))
                    elif op == "gt":
                        m = pc.greater(arr, val)
                    elif op == "gte":
                        m = pc.greater_equal(arr, val)
                    elif op == "lt":
                        m = pc.less(arr, val)
                    elif op == "lte":
                        m = pc.less_equal(arr, val)
                    else:
                        continue
                    m = pc.fill_null(m, False)
                    mask = m if mask is None else pc.and_(mask, m)
                if mask is not None:
                    table = table.filter(mask)
            if sortable:
                table = self._sorted(table, sort, descending)
            window = table.slice(offset, limit)
            return {
                "rows": self._rows_from_table(window),
                "offset": offset,
                "limit": limit,
                "total": table.num_rows,
                "columns": self.columns(),
            }

        # Fast path: contiguous slice using row groups.
        total = self.num_rows
        need = limit
        skip = offset
        chunks: list[pa.Table] = []
        for rg in range(self.pf.num_row_groups):
            if need <= 0:
                break
            meta = self.pf.metadata.row_group(rg)
            n = meta.num_rows
            if skip >= n:
                skip -= n
                continue
            piece = self.pf.read_row_group(rg)
            if skip:
                piece = piece.slice(skip)
                skip = 0
            if piece.num_rows > need:
                piece = piece.slice(0, need)
            chunks.append(piece)
            need -= piece.num_rows
        table = pa.concat_tables(chunks) if chunks else self.pf.schema_arrow.empty_table()
        return {
            "rows": self._rows_from_table(table),
            "offset": offset,
            "limit": limit,
            "total": total,
            "columns": self.columns(),
        }

    def distinct(self, column: str, limit: int = 500) -> list[Any]:
        if column not in self.pf.schema_arrow.names:
            return []
        table = pq.read_table(self.path, columns=[column])
        arr = table.column(column)
        if pa.types.is_dictionary(arr.type):
            arr = arr.cast(pa.string())
        vals = pc.unique(arr).to_pylist()
        vals = [v for v in vals if v is not None]
        try:
            vals.sort()
        except TypeError:
            vals.sort(key=str)
        return vals[:limit]
