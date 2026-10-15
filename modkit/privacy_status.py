"""Public, value-free privacy status for the local MAX Mod panel.

This module never opens profiles or credentials. A status is constructed only
from a verified binary manifest and sanitized observations. Disabled components
and observed calls are intentionally separate quantities.
"""
from __future__ import annotations

from collections import Counter
import ipaddress
import threading
import time

from privacy_labels import (FIELD_TITLES, TRACER_TITLES, REQUEST_TITLES,
                            UNKNOWN_REQUEST_DETAIL, KNOWN_HEADER_DETAIL, request_labels)


MAX_REQUEST_VARIANTS = 100
MAX_REQUEST_GROUPS = 256
MAX_DESTINATIONS = 100
MAX_PACKET_BYTES = 64 * 1024 * 1024
MAX_SAFE_INTEGER = 2 ** 53 - 1
DIAGNOSTIC_PORT = 18973
ERRORS = frozenset({"attach_failed", "hook_failed", "metadata_rejected", "process_exited",
                    "runtime_snapshot_timeout", "runtime_snapshot_unavailable", "runtime_session_lost",
                    "runtime_transport_failed", "runtime_retention_ack_failed", "runtime_ghost_ack_failed", "runtime_notification_ack_failed",
                    "runtime_schema_failed", "runtime_worker_failed"})

RULES = (
    ("tracer_main", "Отчёты о сбоях: приложение", "Отключены 11 используемых входов Tracer; подсчёт вызовов главного приложения ещё не подключён.",
     "tracernative.dll", "no_tracer_"),
    ("tracer_service", "Отчёты о сбоях: служба звонков", "Отключены 13 используемых входов Tracer; счётчик охватывает службу звонков.",
     "max-service/tracernative.dll", "no_tracer_"),
    ("ui_statistics", "Статистика интерфейса", "Отключены создание сессии статистики и отправка очереди.",
     "core.dll", "no_analytics_"),
    ("mytracker", "Аналитика MyTracker", "Отключено создание модуля MyTracker в службе звонков. Отправка событий до патча отдельно не подтверждалась; сетевого счётчика для него нет.",
     "max-service/max-service.exe", "no_mytracker_stats_init"),
    ("log_masking", "Маскировка журналов", "Настройка маскировки включена в приложении и службе.",
     "config.dll", "mask_logs"),
    ("error_logs", "Журналы в отчётах", "Автоматическое приложение журналов отключено.",
     "config.dll", "no_error_logs_to_crash_server"),
)
EXPECTED_COUNTS = {"tracer_main": 11, "tracer_service": 13, "ui_statistics": 2,
                   "mytracker": 1, "log_masking": 1, "error_logs": 1}
RULE_STATUS_TEXT = {
    "tracer_main": "Вызовы SDK отключены",
    "tracer_service": "Вызовы SDK отключены",
    "ui_statistics": "Создание и отправка статистики отключены",
    "mytracker": "Инициализация отключена",
    "log_masking": "Маскировка включена",
    "error_logs": "Автоматическое приложение журналов отключено",
}
NOTES = [
    "Счётчик показывает обращения к отключённому SDK после подключения диагностики, а не все заблокированные сетевые запросы.",
    "IP соединения виден серверу; удалить его из полезной нагрузки недостаточно. Для сокрытия адреса нужен VPN или прокси.",
    "Содержимое сообщений, коды, токены и значения полей в журнал не сохраняются.",
    "Поля входа и синхронизации не удаляются без проверки их обязательности.",
    "Счётчик Tracer охватывает службу звонков. В главном приложении SDK отключён патчами, но точка подсчёта его вызовов ещё не установлена.",
    "Сетевой список включает TCP-адреса, существовавшие при подключении, и наблюдённые попытки connect; число не означает успешные соединения или трекеры.",
    "Пустой список полей означает, что содержимое пакета не изучалось. Это не означает, что полей в нём нет.",
    "Внешние назначения — адреса вне loopback, включая локальную сеть. Их принадлежность к трекерам не установлена.",
    "В списке сохраняются до 256 типов запросов и до 100 адресов назначения. Не вошедшие в список пакеты учитываются в итогах отдельно; число типов может быть неполным.",
]


def rules_from_manifest(manifest: dict) -> list[dict]:
    """Summarize known patches; callers must validate full hashes first."""
    files = {item["relative_path"]: item for item in manifest["files"]}
    rules = []
    for identifier, title, detail, relative, prefix in RULES:
        patches = files.get(relative, {}).get("patches", [])
        count = sum(item["id"].startswith(prefix) for item in patches)
        valid = count == EXPECTED_COUNTS[identifier]
        if identifier in ("log_masking", "error_logs"):
            service = files.get("max-service/config.dll", {}).get("patches", [])
            valid = valid and sum(item["id"].startswith(prefix) for item in service) == 1
        rules.append({"id": identifier, "title": title,
                      "state": "enabled" if valid else "unknown",
                      "statusText": RULE_STATUS_TEXT[identifier] if valid else "Защита не подтверждена",
                      "detail": detail if valid else "Полный набор ожидаемых патчей не подтверждён."})
    return rules


class Observation:
    """Bounded, sanitized counters with no arbitrary payload persistence."""

    def __init__(self):
        self.lock = threading.Lock()
        self.started = time.monotonic()
        self.processes: dict[int, dict] = {}
        self.tracer = Counter()
        self.connections = Counter()
        self.requests = Counter()
        self.request_groups = {}
        self.packet_totals = Counter()
        # A bounded bitset of all possible uint16 opcodes (at most8KiB), so
        # totals and known type counts remain honest beyond256 displayed groups.
        self.opcodes_seen = 0
        self.omitted_header_packets = 0
        self.errors = Counter()
        self.metadata_rejections = Counter()
        self.fatal_frames = []

    def attach(self, pid: int, *, tracer_hooks: int, transport_hooks: int, role: str = "unknown"):
        if (type(tracer_hooks) is not int or type(transport_hooks) is not int
                or not 0 <= tracer_hooks <= 128 or not 0 <= transport_hooks <= 128):
            raise ValueError("Invalid observer hook counts")
        with self.lock:
            self.processes[pid] = {"tracer_hooks": tracer_hooks, "transport_hooks": transport_hooks,
                                   "role": role if role in ("main", "service") else "unknown"}

    def detach(self, pid: int):
        with self.lock:
            self.processes.pop(pid, None)

    def tracer_call(self, symbol: str, *, count: int = 1):
        # Accept only function labels, never SDK arguments or strings.
        if (type(symbol) is str and symbol in TRACER_TITLES
                and type(count) is int and 1 <= count <= 1000000):
            with self.lock:
                self.tracer[symbol] += count

    def connection(self, host: str, port: int, *, count: int = 1):
        if (type(count) is not int or not 1 <= count <= 1000000
                or type(host) is not str or len(host) > 64 or "%" in host):
            return
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            return
        if type(port) is not int or not 1 <= port <= 65535:
            return
        mapped = getattr(address, "ipv4_mapped", None)
        local = address.is_loopback or (mapped is not None and mapped.is_loopback)
        if local and port == DIAGNOSTIC_PORT:
            return  # The panel's own loopback HTTP is not MAX traffic.
        host = str(address)
        with self.lock:
            if len(self.connections) < MAX_DESTINATIONS or (host, port) in self.connections:
                self.connections[host, port] += count

    def packet(self, metadata: dict, *, count: int = 1):
        # The protocol parser supplies only allowlisted field names and opcodes.
        # Do not forward arbitrary dictionaries from a Frida message to the UI.
        from network_metadata import validate_metadata
        if type(count) is not int or not 1 <= count <= 1000000:
            return
        value = validate_metadata(metadata)
        if value is None or not 10 <= value["bytes"] <= MAX_PACKET_BYTES:
            return
        self.packet_group({"metadata": value, "count": count,
                           "bytesTotal": value["bytes"] * count,
                           "minBytes": value["bytes"], "maxBytes": value["bytes"]})

    def packet_group(self, item: object):
        """Consume a bounded header batch without accepting arbitrary values.

        All valid packets contribute to global totals even after the displayed
        opcode-group limit. Legacy size/field variants remain bounded to100.
        """
        from network_metadata import validate_metadata
        if type(item) is not dict:
            return
        value = validate_metadata(item.get("metadata"))
        count, total = item.get("count"), item.get("bytesTotal")
        minimum, maximum = item.get("minBytes"), item.get("maxBytes")
        if (value is None or type(count) is not int or not 1 <= count <= 1000000
                or type(minimum) is not int or type(maximum) is not int
                or not 10 <= minimum <= maximum <= MAX_PACKET_BYTES
                or value["bytes"] != minimum or type(total) is not int
                or not minimum * count <= total <= maximum * count):
            return
        opcode, fields = value["opcode"], value["fields"]
        parsed = opcode == 6 and bool(fields)
        key = (value["protocol"], opcode, tuple(fields), minimum)
        with self.lock:
            self.packet_totals["observedPackets"] += count
            self.packet_totals["observedBytes"] += total
            self.packet_totals["parsedPackets" if parsed else "headerOnlyPackets"] += count
            self.opcodes_seen |= 1 << opcode
            if len(self.requests) < MAX_REQUEST_VARIANTS or key in self.requests:
                # Compatibility histogram: minBytes is representative for a
                # batch. Dashboard totals use the exact separate aggregate.
                self.requests[key] += count
            group = self.request_groups.get(opcode)
            if group is None:
                if len(self.request_groups) >= MAX_REQUEST_GROUPS:
                    self.packet_totals["excludedPackets"] += count
                    self.packet_totals["excludedBytes"] += total
                    return
                group = {"count": 0, "bytes": 0, "minBytes": minimum,
                         "maxBytes": maximum, "fields": set(),
                         "parsedPackets": 0, "headerOnlyPackets": 0}
                self.request_groups[opcode] = group
            group["count"] += count
            group["bytes"] += total
            group["minBytes"] = min(group["minBytes"], minimum)
            group["maxBytes"] = max(group["maxBytes"], maximum)
            group["fields"].update(fields)
            group["parsedPackets" if parsed else "headerOnlyPackets"] += count

    def omitted_headers(self, count: object, bytes_total: object):
        """Count JS-overflow headers; neither opcode nor body was retained."""
        if type(count) is not int or type(bytes_total) is not int:
            return
        if count == 0 and bytes_total == 0:
            return
        if (not 1 <= count <= 1000000000
                or not 10 * count <= bytes_total <= MAX_PACKET_BYTES * count
                or bytes_total > MAX_SAFE_INTEGER):
            return
        with self.lock:
            self.packet_totals["observedPackets"] += count
            self.packet_totals["observedBytes"] += bytes_total
            self.packet_totals["headerOnlyPackets"] += count
            self.packet_totals["excludedPackets"] += count
            self.packet_totals["excludedBytes"] += bytes_total
            self.omitted_header_packets += count

    def _request_groups_snapshot(self) -> list[dict]:
        """Build display rows under self.lock using only static labels."""
        rows = []
        for opcode, group in sorted(self.request_groups.items()):
            fields = sorted(group["fields"])
            title, category = request_labels(opcode)
            parsed = opcode == 6 and group["parsedPackets"] > 0 and not group["headerOnlyPackets"]
            if opcode != 6:
                detail = KNOWN_HEADER_DETAIL if opcode in REQUEST_TITLES else UNKNOWN_REQUEST_DETAIL
            elif parsed:
                detail = "Разобраны только разрешённые имена полей; значения не сохраняются."
            elif group["parsedPackets"]:
                detail = (f"Поля разобраны для {group['parsedPackets']} из {group['count']} пакетов; "
                          "для остальных изучен только заголовок. Значения не сохраняются.")
            else:
                detail = "Наблюдались только заголовки; содержимое не изучалось."
            rows.append({"opcode": opcode, "title": title, "category": category,
                         "count": group["count"], "bytes": group["bytes"],
                         "minBytes": group["minBytes"], "maxBytes": group["maxBytes"],
                         "fields": fields, "fieldTitles": [FIELD_TITLES[field] for field in fields],
                         "bodyState": "parsed" if parsed else "header_only", "detail": detail,
                         "parsedPackets": group["parsedPackets"], "headerOnlyPackets": group["headerOnlyPackets"]})
        return rows

    def metadata_rejection(self, reason: str):
        from network_metadata import DIAGNOSTIC_REASONS
        if type(reason) is not str or reason not in DIAGNOSTIC_REASONS or reason == "ok":
            reason = "layout"
        with self.lock:
            self.errors["metadata_rejected"] += 1
            self.metadata_rejections[reason] += 1

    def error(self, code: str):
        if type(code) is not str or code not in ERRORS:
            code = "hook_failed"
        with self.lock:
            self.errors[code] = min(1000000, self.errors[code] + 1)

    def fatal(self, frames: list):
        import re
        safe = []
        for item in frames[:24]:
            if (type(item) is dict and type(item.get("module")) is str
                    and re.fullmatch(r"[a-zA-Z0-9_.-]{1,64}", item["module"])
                    and type(item.get("rva")) is int and 0 <= item["rva"] < 2 ** 32):
                safe.append({"module": item["module"], "rva": item["rva"]})
        with self.lock:
            self.fatal_frames = safe

    def snapshot(self) -> dict:
        with self.lock:
            tracer_hooks = sum(item["tracer_hooks"] for item in self.processes.values())
            transport_hooks = sum(item["transport_hooks"] for item in self.processes.values())
            connections = []
            for (host, port), count in self.connections.items():
                address = ipaddress.ip_address(host)
                mapped = getattr(address, "ipv4_mapped", None)
                local = address.is_loopback or (mapped is not None and mapped.is_loopback)
                connections.append({"host": host, "port": port, "count": count,
                                    "scope": "local" if local else "external",
                                    "title": "Локальное назначение" if local else "Внешнее назначение"})
            dashboard = {key: self.packet_totals[key] for key in
                         ("observedPackets", "observedBytes", "parsedPackets", "headerOnlyPackets",
                          "excludedPackets", "excludedBytes")}
            dashboard.update({"requestTypes": self.opcodes_seen.bit_count(),
                              "requestTypesComplete": not bool(self.omitted_header_packets),
                              "excludedRequestTypes": self.opcodes_seen.bit_count() - len(self.request_groups),
                              "externalDestinations": sum(item["scope"] == "external" for item in connections),
                              "localDestinations": sum(item["scope"] == "local" for item in connections)})
            return {
                "observation": {"active": bool(self.processes),
                                "seconds": int(time.monotonic() - self.started),
                                "blockedCalls": sum(self.tracer.values()) if tracer_hooks else None,
                                "tracerHooks": tracer_hooks,
                                "transportHooks": transport_hooks,
                                "processes": len(self.processes),
                                "coverage": "С момента подключения: вызовы Tracer службы звонков, TCP-назначения и заголовки исходящих пакетов приложения. Имена полей проверяются только в инициализации сессии (тип 6); значения не сохраняются. Подсчёт вызовов Tracer главного приложения пока недоступен.",
                                "coverageByProcess": [dict(item) for item in self.processes.values()]},
                "dashboard": dashboard,
                "requestGroups": self._request_groups_snapshot(),
                "tracerCalls": [{"function": key, "title": TRACER_TITLES[key], "count": value} for key, value in self.tracer.items()],
                "connections": connections,
                "requests": [{"protocol": protocol, "opcode": opcode, "fields": list(fields),
                              "bytes": size, "count": count}
                             for (protocol, opcode, fields, size), count in self.requests.items()],
                "errors": dict(self.errors),
                "metadataRejections": dict(self.metadata_rejections),
                "fatalFrames": list(self.fatal_frames),
            }
