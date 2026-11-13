import copy

from tests import unittest

import jmespath
from jmespath import exceptions


# Orders intentionally cover every "missing detail" shape:
#   O1 has two line items, O2 has an empty list, O3 has no items field at
#   all, O4 has an explicit null and O5 has a single line item.
ORDERS = [
    {"order_id": "O1", "customer": "alice", "items": [
        {"sku": "A", "qty": 2}, {"sku": "B", "qty": 1}]},
    {"order_id": "O2", "customer": "bob", "items": []},
    {"order_id": "O3", "customer": "carol"},
    {"order_id": "O4", "customer": "dave", "items": None},
    {"order_id": "O5", "customer": "erin", "items": [
        {"sku": "C", "qty": 9}]},
]


class TestFlatMap(unittest.TestCase):
    def setUp(self):
        self.orders = copy.deepcopy(ORDERS)

    def test_two_details_produce_two_rows_without_padding(self):
        result = jmespath.search("flat_map(@, &items)", self.orders)
        rows_for_o1 = [row for row in result if row["order_id"] == "O1"]
        self.assertEqual(len(rows_for_o1), 2)
        self.assertEqual(
            [row["sku"] for row in rows_for_o1], ["A", "B"])
        # Two details mean two rows; a single global null must not be
        # appended to force the count back to the number of orders.
        self.assertEqual(len(result), 2 + 1 + 1 + 1 + 1)
        self.assertNotIn(None, result)

    def test_empty_detail_is_kept_and_marked(self):
        result = jmespath.search("flat_map(@, &items)", self.orders)
        row = next(row for row in result if row["order_id"] == "O2")
        self.assertEqual(row["_status"], "empty")
        # The parent path survives on the placeholder row.
        self.assertEqual(row["customer"], "bob")

    def test_missing_field_is_marked_distinctly(self):
        result = jmespath.search("flat_map(@, &items)", self.orders)
        row = next(row for row in result if row["order_id"] == "O3")
        self.assertEqual(row["_status"], "missing")
        self.assertEqual(row["_field"], "items")

    def test_explicit_null_is_marked_distinctly_from_missing(self):
        result = jmespath.search("flat_map(@, &items)", self.orders)
        row = next(row for row in result if row["order_id"] == "O4")
        self.assertEqual(row["_status"], "null")
        self.assertEqual(row["_field"], "items")
        statuses = {row["order_id"]: row.get("_status")
                    for row in result}
        self.assertNotEqual(statuses["O3"], statuses["O4"])

    def test_every_parent_is_represented(self):
        result = jmespath.search("flat_map(@, &items)", self.orders)
        # Unlike a projection, no order disappears from the report.
        self.assertEqual(
            sorted(row["order_id"] for row in result
                   if row.get("_status") is not None),
            ["O2", "O3", "O4"])

    def test_each_row_keeps_parent_path(self):
        result = jmespath.search("flat_map(@, &items)", self.orders)
        for row in result:
            self.assertIn("order_id", row)
            self.assertIn("customer", row)

    def test_nested_detail_keeps_parent_path(self):
        shipments = [{
            "order_id": "N1",
            "shipments": [
                {"ship_id": "S1", "lines": [{"sku": "X"}]},
                {"ship_id": "S2", "lines": [{"sku": "Y"}]},
            ],
        }]
        result = jmespath.search("flat_map(@, &shipments)", shipments)
        self.assertEqual(len(result), 2)
        self.assertEqual(
            [row["ship_id"] for row in result], ["S1", "S2"])
        for row in result:
            self.assertEqual(row["order_id"], "N1")

    def test_object_detail_merges_into_parent_row(self):
        data = [{"order_id": "P1", "meta": {"region": "cn"}}]
        result = jmespath.search("flat_map(@, &meta)", data)
        self.assertEqual(result, [
            {"order_id": "P1", "meta": {"region": "cn"},
             "region": "cn"}])

    def test_empty_object_detail_is_marked_empty(self):
        data = [{"order_id": "E1", "meta": {}}]
        result = jmespath.search("flat_map(@, &meta)", data)
        self.assertEqual(result[0]["_status"], "empty")

    def test_scalar_detail_is_nested_under_detail_key(self):
        data = [{"order_id": "S1", "flag": "on"}]
        result = jmespath.search("flat_map(@, &flag)", data)
        self.assertEqual(result[0]["_detail"], "on")
        self.assertEqual(result[0]["order_id"], "S1")

    def test_non_object_parent_is_kept_under_parent_key(self):
        result = jmespath.search("flat_map(@, &missing)", [1, 2])
        self.assertEqual(
            result,
            [{"_parent": 1, "_status": "missing", "_field": "missing"},
             {"_parent": 2, "_status": "missing", "_field": "missing"}])

    def test_null_parent_element_is_not_dropped(self):
        result = jmespath.search(
            "flat_map(@, &items)", [None, {"order_id": "X"}])
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["_status"], "missing")
        self.assertEqual(result[1]["_status"], "missing")

    def test_input_document_is_not_mutated(self):
        snapshot = copy.deepcopy(self.orders)
        jmespath.search("flat_map(@, &items)", self.orders)
        self.assertEqual(self.orders, snapshot)

    def test_marker_rows_work_with_downstream_filters(self):
        # The quality-report query: surface only orders lacking details.
        result = jmespath.search(
            "flat_map(@, &items)[?_status != `null`]"
            ".{order: order_id, reason: _status}",
            self.orders)
        self.assertEqual(
            result,
            [{"order": "O2", "reason": "empty"},
             {"order": "O3", "reason": "missing"},
             {"order": "O4", "reason": "null"}])

    def test_requires_array_first_argument(self):
        with self.assertRaises(exceptions.JMESPathTypeError):
            jmespath.search("flat_map(@, &items)", {"not": "an array"})

    def test_requires_expref_second_argument(self):
        with self.assertRaises(exceptions.JMESPathTypeError):
            jmespath.search("flat_map(@, items)", self.orders)

    def test_arity_is_validated(self):
        with self.assertRaises(exceptions.ArityError):
            jmespath.search("flat_map(@)", self.orders)

    def test_empty_array_produces_empty_array(self):
        self.assertEqual(jmespath.search("flat_map(@, &items)", []), [])

    def test_null_element_inside_detail_list_is_kept(self):
        data = [{"order_id": "Z1", "items": [None, {"sku": "M"}]}]
        result = jmespath.search("flat_map(@, &items)", data)
        self.assertEqual(len(result), 2)
        self.assertIsNone(result[0]["_detail"])
        self.assertEqual(result[1]["sku"], "M")
        for row in result:
            self.assertEqual(row["order_id"], "Z1")

    def test_scalar_list_expands_to_one_row_per_value(self):
        data = [{"order_id": "T1", "tags": ["x", "y", "z"]}]
        result = jmespath.search("flat_map(@, &tags)", data)
        self.assertEqual([row["_detail"] for row in result],
                         ["x", "y", "z"])

    def test_false_and_zero_details_are_not_dropped_or_marked_empty(self):
        data = [{"order_id": "B1", "flag": False},
                {"order_id": "B2", "count": 0}]
        result = jmespath.search("flat_map(@, &flag)", data[:1])
        self.assertNotIn("_status", result[0])
        self.assertFalse(result[0]["_detail"])
        result = jmespath.search("flat_map(@, &count)", data[1:])
        self.assertEqual(result[0]["_detail"], 0)

    def test_chained_nested_expansion_keeps_every_parent_path(self):
        data = [{"order_id": "O1", "shipments": [
            {"ship_id": "S1", "lines": [{"sku": "X"}, {"sku": "Y"}]},
            {"ship_id": "S2", "lines": []},
            {"ship_id": "S3"},
        ]}]
        result = jmespath.search(
            "flat_map(flat_map(@, &shipments), &lines)", data)
        self.assertEqual(len(result), 4)
        # The two concrete lines produce two rows each carrying both
        # the order and shipment path.
        self.assertEqual(
            [row["sku"] for row in result if "sku" in row], ["X", "Y"])
        for row in result:
            self.assertEqual(row["order_id"], "O1")
            self.assertEqual(row["ship_id"], row["ship_id"])
        # Empty and missing nested details stay queryable for the report.
        problems = jmespath.search(
            "flat_map(flat_map(@, &shipments), &lines)"
            "[?_status != `null`].[ship_id, _status]", data)
        self.assertEqual(problems, [["S2", "empty"], ["S3", "missing"]])


class TestMarkMissing(unittest.TestCase):
    def setUp(self):
        self.orders = copy.deepcopy(ORDERS)

    def test_output_is_one_to_one_with_input(self):
        result = jmespath.search("mark_missing(@, 'items')", self.orders)
        self.assertEqual(len(result), len(self.orders))
        self.assertEqual(
            [row["order_id"] for row in result],
            ["O1", "O2", "O3", "O4", "O5"])

    def test_missing_and_null_are_tagged_separately(self):
        result = jmespath.search("mark_missing(@, 'items')", self.orders)
        statuses = {row["order_id"]: row.get("_status")
                    for row in result}
        self.assertIsNone(statuses["O1"])
        self.assertIsNone(statuses["O2"])
        self.assertEqual(statuses["O3"], "missing")
        self.assertEqual(statuses["O4"], "null")
        self.assertIsNone(statuses["O5"])

    def test_populated_rows_keep_their_data(self):
        result = jmespath.search("mark_missing(@, 'items')", self.orders)
        first = result[0]
        self.assertEqual(first["customer"], "alice")
        self.assertEqual(len(first["items"]), 2)

    def test_non_object_elements_are_marked_missing(self):
        result = jmespath.search("mark_missing(@, 'items')", [1, None])
        self.assertEqual(
            [row["_status"] for row in result], ["missing", "missing"])

    def test_input_document_is_not_mutated(self):
        snapshot = copy.deepcopy(self.orders)
        jmespath.search("mark_missing(@, 'items')", self.orders)
        self.assertEqual(self.orders, snapshot)

    def test_requires_string_second_argument(self):
        with self.assertRaises(exceptions.JMESPathTypeError):
            jmespath.search("mark_missing(@, items)", self.orders)

    def test_arity_is_validated(self):
        with self.assertRaises(exceptions.ArityError):
            jmespath.search("mark_missing(@)", self.orders)


class TestProjectionBehaviorUnchanged(unittest.TestCase):
    def test_standard_projection_still_drops_empty_parents(self):
        # Regression guard: the built-in projection semantics are untouched;
        # the preserving behavior only lives behind the new functions.
        result = jmespath.search("[*].items[]", ORDERS)
        # Only O1 (2) and O5 (1) have concrete line items.
        self.assertEqual(len(result), 3)
