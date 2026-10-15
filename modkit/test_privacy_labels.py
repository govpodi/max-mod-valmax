"""New native-proven titles retain the existing metadata-only status contract."""
import json
import unittest

from network_metadata import validate_metadata
from privacy_labels import (KNOWN_HEADER_DETAIL, UNKNOWN_REQUEST_DETAIL,
                            request_labels)
from privacy_status import MAX_REQUEST_GROUPS, Observation


NEW_TITLES = {
    32: ("Информация о пользователе", "Контакты"),
    50: ("Состояние прочтения чата", "Чаты"),
    65: ("Статус «печатает»", "Чаты"),
    214: ("Отметка просмотра истории", "Истории"),
}


def header(opcode, **extra):
    return {"protocol": "msgpack", "opcode": opcode, "bytes": 40,
            "fields": [], **extra}


class NativeRequestLabelTests(unittest.TestCase):
    def test_new_types_are_header_only_with_no_body_or_blocking_claim(self):
        observation = Observation()
        for opcode in (*NEW_TITLES, 65000):
            observation.packet(header(opcode, token="PRIVATE_VALUE"), count=2)
        snapshot = observation.snapshot()
        groups = {row["opcode"]: row for row in snapshot["requestGroups"]}
        unknown = groups[65000]
        for opcode, expected in NEW_TITLES.items():
            row = groups[opcode]
            self.assertEqual((row["title"], row["category"]), expected)
            self.assertEqual(set(row), set(unknown))
            self.assertEqual(row["bodyState"], "header_only")
            self.assertEqual(row["detail"], KNOWN_HEADER_DETAIL)
            self.assertEqual(row["fields"], [])
            self.assertEqual(row["fieldTitles"], [])
            self.assertEqual((row["count"], row["bytes"]), (2, 80))
        self.assertEqual(unknown["detail"], UNKNOWN_REQUEST_DETAIL)
        self.assertEqual(snapshot["dashboard"]["parsedPackets"], 0)
        self.assertEqual(snapshot["dashboard"]["observedPackets"], 10)
        self.assertNotIn("PRIVATE_VALUE", json.dumps(snapshot))

    def test_new_names_do_not_expand_payload_schema_or_numeric_bounds(self):
        observation = Observation()
        for opcode in NEW_TITLES:
            value = header(opcode, fields=["userAgent"])
            self.assertIsNone(validate_metadata(value))
            observation.packet(value)
        for invalid in (True, -1, 65536, "32"):
            value = header(invalid)
            self.assertIsNone(validate_metadata(value))
            observation.packet(value)
        snapshot = observation.snapshot()
        self.assertEqual(snapshot["requestGroups"], [])
        self.assertEqual(snapshot["dashboard"]["observedPackets"], 0)

    def test_unreviewed_neighbors_keep_unknown_labels(self):
        for opcode in (0, 31, 33, 49, 51, 64, 66, 213, 215, 65000, 65535):
            with self.subTest(opcode=opcode):
                self.assertEqual(request_labels(opcode),
                                 (f"Запрос №{opcode}", "Назначение не подтверждено"))
        self.assertEqual(request_labels(6), ("Инициализация сессии", "Сессия"))
        self.assertEqual(request_labels(35), ("Статус контактов", "Контакты"))
        self.assertEqual(request_labels(208), ("Список историй", "Истории"))

    def test_titles_do_not_bypass_group_limit_or_drop_total_counts(self):
        observation = Observation()
        for opcode in range(1000, 1000 + MAX_REQUEST_GROUPS - 2):
            observation.packet(header(opcode))
        for opcode in NEW_TITLES:
            observation.packet(header(opcode))
        snapshot = observation.snapshot()
        self.assertEqual(len(snapshot["requestGroups"]), MAX_REQUEST_GROUPS)
        self.assertEqual(snapshot["dashboard"]["observedPackets"],
                         MAX_REQUEST_GROUPS + 2)
        self.assertEqual(snapshot["dashboard"]["parsedPackets"], 0)
        rows = {row["opcode"]: row for row in snapshot["requestGroups"]}
        self.assertEqual(rows[32]["title"], NEW_TITLES[32][0])
        self.assertEqual(rows[50]["title"], NEW_TITLES[50][0])
        self.assertNotIn(65, rows)
        self.assertNotIn(214, rows)


if __name__ == "__main__":
    unittest.main()
