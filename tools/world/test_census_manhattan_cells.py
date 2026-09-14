#!/usr/bin/env python3
"""Small regression tests for resumable Cell-census metadata merging."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from census_manhattan_cells import _merge_records, _new_inventory  # noqa: E402


class ManhattanCellCensusTests(unittest.TestCase):
    def test_merge_is_numeric_sorted_and_replaces_resumed_cell(self):
        inventory = _new_inventory("https://viewer.example", "/game/cells.rcf", 10)
        _merge_records(inventory, [
            {"cell_index": 10, "status": "placeholder"},
            {"cell_index": 2, "status": "scanned", "geometry_count": 3},
        ])
        _merge_records(inventory, [
            {"cell_index": 10, "status": "scanned", "geometry_count": 5},
            {"cell_index": 1, "status": "scan_error"},
        ])
        self.assertEqual([record["cell_index"] for record in inventory["records"]], [1, 2, 10])
        self.assertEqual(inventory["records"][-1]["geometry_count"], 5)


if __name__ == "__main__":
    unittest.main()
