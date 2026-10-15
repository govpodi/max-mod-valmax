"""No MAX execution: sanitized status and loopback HTTP contract tests."""
import http.client
import json
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock
from unittest.mock import patch
from types import SimpleNamespace

import frida
from http.server import ThreadingHTTPServer

import build_mod
from privacy_server import PORT, IdleDeadline, LoopbackServer, Monitor, handler_factory
from privacy_status import Observation, rules_from_manifest
from privacy_labels import FIELD_TITLES, TRACER_TITLES, UNKNOWN_REQUEST_DETAIL
from network_metadata import ALLOWED_FIELDS
from privacy_rpc import SnapshotTimeout


class StatusTests(unittest.TestCase):
    def test_complete_manifest_and_missing_patches_are_distinct(self):
        manifest = {"files": build_mod.specifications(["accounts", "privacy", "isolation"])}
        rules = rules_from_manifest(manifest)
        self.assertTrue(all(item["state"] == "enabled" for item in rules))
        manifest["files"] = [item for item in manifest["files"] if item["relative_path"] != "max-service/config.dll"]
        states = {item["id"]: item["state"] for item in rules_from_manifest(manifest)}
        self.assertEqual(states["log_masking"], "unknown")
        self.assertEqual(states["error_logs"], "unknown")

    def test_zero_is_unknown_until_a_tracer_hook_is_installed(self):
        observation = Observation()
        self.assertIsNone(observation.snapshot()["observation"]["blockedCalls"])
        observation.attach(123, tracer_hooks=0, transport_hooks=2)
        self.assertIsNone(observation.snapshot()["observation"]["blockedCalls"])
        observation.attach(123, tracer_hooks=1, transport_hooks=2)
        self.assertEqual(observation.snapshot()["observation"]["blockedCalls"], 0)
        observation.detach(123)
        self.assertIsNone(observation.snapshot()["observation"]["blockedCalls"])

    def test_mytracker_patch_status_does_not_invent_a_traffic_counter(self):
        manifest = {"files": build_mod.specifications(["accounts", "privacy", "isolation"])}
        rules = {item["id"]: item for item in rules_from_manifest(manifest)}
        self.assertEqual(rules["mytracker"]["state"], "enabled")
        self.assertIn("не подтверждалась", rules["mytracker"]["detail"])
        self.assertNotIn("blockedCalls", rules["mytracker"])
        for item in manifest["files"]:
            if item["relative_path"] == "max-service/max-service.exe":
                item["patches"] = [patch for patch in item["patches"] if patch["id"] != "no_mytracker_stats_init"]
        rules = {item["id"]: item for item in rules_from_manifest(manifest)}
        self.assertEqual(rules["mytracker"]["state"], "unknown")

    def test_arbitrary_strings_cannot_enter_public_status(self):
        observation = Observation()
        secret = "TOKEN_AND_MESSAGE_12345"
        observation.tracer_call(secret)
        observation.connection(secret, 443)
        observation.connection("127.0.0.1", secret)
        observation.error(secret)
        self.assertNotIn(secret, json.dumps(observation.snapshot()))

    def test_counter_means_sdk_calls_and_is_bounded_to_known_labels(self):
        observation = Observation()
        observation.attach(123, tracer_hooks=2, transport_hooks=2)
        observation.tracer_call("tracer_upload_async")
        observation.tracer_call("tracer_set_userid")
        observation.connection("1.2.3.4", 443)
        snapshot = observation.snapshot()
        self.assertEqual(snapshot["observation"]["blockedCalls"], 2)
        self.assertEqual(snapshot["connections"], [{"host": "1.2.3.4", "port": 443, "count": 1,
                                                    "scope": "external", "title": "Внешнее назначение"}])
        self.assertNotIn("userid", snapshot)

    def test_packet_batch_counts_are_bounded_and_never_forward_values(self):
        observation = Observation()
        packet = {"protocol": "msgpack", "opcode": 19, "fields": [], "bytes": 80,
                  "token": "PRIVATE_VALUE"}
        observation.packet(packet, count=8)
        for count in (True, 0, -1, 1000001, "PRIVATE_VALUE"):
            observation.packet(packet, count=count)
        snapshot = observation.snapshot()
        self.assertEqual(snapshot["requests"][0]["count"], 8)
        self.assertNotIn("PRIVATE_VALUE", json.dumps(snapshot))

    def test_metadata_reasons_are_fixed_codes_without_arbitrary_input(self):
        observation = Observation()
        observation.metadata_rejection("unknown_key")
        observation.metadata_rejection("PRIVATE_VALUE")
        snapshot = observation.snapshot()
        self.assertEqual(snapshot["errors"], {"metadata_rejected": 2})
        self.assertNotIn("PRIVATE_VALUE", json.dumps(snapshot))


class DashboardTests(unittest.TestCase):
    @staticmethod
    def metadata(opcode=6, size=100, fields=None):
        return {"protocol":"msgpack", "opcode":opcode, "bytes":size,
                "fields":[] if fields is None else fields}

    def test_empty_dashboard_is_zero_with_no_inferred_requests(self):
        value=Observation().snapshot()
        dashboard=value["dashboard"]
        for key in ("observedPackets","observedBytes","requestTypes","parsedPackets",
                    "headerOnlyPackets","externalDestinations","localDestinations",
                    "excludedPackets","excludedBytes","excludedRequestTypes"):
            self.assertEqual(dashboard[key],0)
        self.assertTrue(dashboard["requestTypesComplete"])
        self.assertEqual(value["requestGroups"],[])

    def test_groups_sum_weighted_counts_sizes_and_field_union(self):
        observation=Observation()
        observation.packet(self.metadata(size=100,fields=["userAgent","deviceId"]),count=2)
        observation.packet(self.metadata(size=160,fields=["userAgent","screen"]),count=3)
        observation.packet(self.metadata(opcode=65000,size=40),count=4)
        value=observation.snapshot()
        dashboard=value["dashboard"]
        self.assertEqual(dashboard["observedPackets"],9)
        self.assertEqual(dashboard["observedBytes"],840)
        self.assertEqual(dashboard["requestTypes"],2)
        self.assertEqual(dashboard["parsedPackets"],5)
        self.assertEqual(dashboard["headerOnlyPackets"],4)
        session,unknown=value["requestGroups"]
        self.assertEqual((session["count"],session["bytes"],session["minBytes"],session["maxBytes"]),(5,680,100,160))
        self.assertEqual(session["fields"],["deviceId","screen","userAgent"])
        self.assertEqual(session["fieldTitles"],["Идентификатор устройства","Параметры экрана","Сведения о клиенте"])
        self.assertEqual(session["title"],"Инициализация сессии")
        self.assertEqual(session["bodyState"],"parsed")
        self.assertEqual(unknown["title"],"Запрос №65000")
        self.assertEqual(unknown["detail"],UNKNOWN_REQUEST_DETAIL)
        self.assertEqual(unknown["bodyState"],"header_only")
        self.assertEqual(sum(row["bytes"] for row in value["requestGroups"]),dashboard["observedBytes"])

    def test_unparsed_and_mixed_session_groups_never_claim_complete_body_parse(self):
        observation=Observation()
        observation.packet(self.metadata(size=50),count=2)
        unparsed=observation.snapshot()["requestGroups"][0]
        self.assertEqual(unparsed["bodyState"],"header_only")
        self.assertEqual(unparsed["fields"],[])
        self.assertIn("не изучалось",unparsed["detail"])
        observation.packet(self.metadata(size=100,fields=["userAgent","screen"]),count=3)
        value=observation.snapshot()
        group=value["requestGroups"][0]
        self.assertEqual(group["bodyState"],"header_only")
        self.assertEqual((group["parsedPackets"],group["headerOnlyPackets"]),(3,2))
        self.assertIn("для 3 из 5",group["detail"])
        self.assertEqual(value["dashboard"]["parsedPackets"],3)
        self.assertEqual(value["dashboard"]["headerOnlyPackets"],2)

    def test_statically_identified_packet_names_do_not_claim_body_inspection(self):
        observation=Observation()
        expected={1:"Проверка соединения",19:"Авторизация",27:"Обновление ресурсов",
                  28:"Получение ресурсов",35:"Статус контактов",
                  162:"Список причин жалобы",208:"Список историй",
                  209:"Список историй владельца",256:"Информация об организации",
                  272:"Список папок",300:"Рекомендации чатов"}
        for opcode in expected:
            observation.packet(self.metadata(opcode=opcode,size=40))
        value=observation.snapshot()
        for row in value["requestGroups"]:
            self.assertEqual(row["title"],expected[row["opcode"]])
            self.assertEqual(row["bodyState"],"header_only")
            self.assertEqual(row["fields"],[])
            self.assertIn("содержимое пакета не изучалось",row["detail"])
        self.assertEqual(value["dashboard"]["parsedPackets"],0)

    def test_batched_headers_preserve_exact_total_minimum_and_maximum(self):
        observation=Observation()
        observation.packet_group({"metadata":self.metadata(opcode=65000,size=40),
                                  "count":3,"bytesTotal":180,"minBytes":40,"maxBytes":80,
                                  "token":"PRIVATE_VALUE"})
        value=observation.snapshot()
        group=value["requestGroups"][0]
        self.assertEqual((group["count"],group["bytes"],group["minBytes"],group["maxBytes"]),(3,180,40,80))
        self.assertEqual(value["dashboard"]["observedBytes"],180)
        self.assertNotIn("PRIVATE_VALUE",json.dumps(value))

    def test_invalid_batches_never_affect_totals(self):
        valid={"metadata":self.metadata(opcode=65000,size=40),"count":3,
               "bytesTotal":180,"minBytes":40,"maxBytes":80}
        variants=[None,[],"PRIVATE_VALUE",{"metadata":valid["metadata"],"count":1}]
        for key,value in (("count",True),("count",0),("count",1000001),
                          ("bytesTotal",True),("bytesTotal",119),("bytesTotal",241),
                          ("minBytes",9),("maxBytes",39),("maxBytes",64*1024*1024+1)):
            variants.append({**valid,key:value})
        variants.append({**valid,"metadata":self.metadata(opcode=65000,size=41)})
        variants.append({**valid,"metadata":self.metadata(size=40,fields=["PRIVATE_VALUE"])})
        observation=Observation()
        for value in variants:
            observation.packet_group(value)
        self.assertEqual(observation.snapshot()["dashboard"]["observedPackets"],0)
        self.assertEqual(observation.snapshot()["requestGroups"],[])

    def test_more_than100_variants_are_included_in_totals_and_one_opcode_group(self):
        observation=Observation()
        for size in range(20,121):
            observation.packet(self.metadata(opcode=65000,size=size),count=2)
        value=observation.snapshot()
        self.assertEqual(len(value["requests"]),100)
        self.assertEqual(len(value["requestGroups"]),1)
        self.assertEqual(value["dashboard"]["observedPackets"],202)
        self.assertEqual(value["dashboard"]["observedBytes"],sum(range(20,121))*2)
        self.assertEqual(value["dashboard"]["excludedPackets"],0)

    def test_opcode_group_overflow_keeps_all_totals_and_reports_excluded_detail(self):
        observation=Observation()
        for opcode in range(257):
            observation.packet(self.metadata(opcode=opcode,size=10),count=2)
        observation.packet(self.metadata(opcode=256,size=20),count=3)
        value=observation.snapshot()
        dashboard=value["dashboard"]
        self.assertEqual(len(value["requestGroups"]),256)
        self.assertEqual(len(value["requests"]),100)
        self.assertEqual(dashboard["requestTypes"],257)
        self.assertTrue(dashboard["requestTypesComplete"])
        self.assertEqual(dashboard["observedPackets"],517)
        self.assertEqual(dashboard["observedBytes"],5200)
        self.assertEqual((dashboard["excludedRequestTypes"],dashboard["excludedPackets"],dashboard["excludedBytes"]),(1,5,80))

    def test_omitted_header_counts_are_visible_without_inventing_opcode_types(self):
        observation=Observation()
        for count,total in ((0,0),(True,100),(0,10),(1,9),(1,64*1024*1024+1),
                            (1000000001,10000000010),("PRIVATE_VALUE",100),(1,"PRIVATE_VALUE")):
            observation.omitted_headers(count,total)
        self.assertEqual(observation.snapshot()["dashboard"]["observedPackets"],0)
        observation.omitted_headers(3,180)
        value=observation.snapshot()
        dashboard=value["dashboard"]
        self.assertEqual((dashboard["observedPackets"],dashboard["observedBytes"]),(3,180))
        self.assertEqual((dashboard["headerOnlyPackets"],dashboard["excludedPackets"],dashboard["excludedBytes"]),(3,3,180))
        self.assertEqual(dashboard["requestTypes"],0)
        self.assertFalse(dashboard["requestTypesComplete"])
        self.assertEqual(value["requestGroups"],[])
        self.assertNotIn("PRIVATE_VALUE",json.dumps(value))

    def test_all_loopback_diagnostic_endpoints_are_excluded_and_other_local_are_separate(self):
        observation=Observation()
        for host in ("127.0.0.1","127.2.3.4","::1","::ffff:127.0.0.1"):
            observation.connection(host,18973)
            observation.connection(host,80)
        observation.connection("192.168.1.2",443)
        observation.connection("8.8.8.8",443)
        value=observation.snapshot()
        self.assertEqual((value["dashboard"]["localDestinations"],value["dashboard"]["externalDestinations"]),(4,2))
        self.assertEqual(len(value["connections"]),6)
        self.assertTrue(all(row["port"]!=18973 for row in value["connections"]))
        self.assertTrue(all(row["title"] in ("Локальное назначение","Внешнее назначение") for row in value["connections"]))

    def test_bad_host_values_and_scoped_ipv6_cannot_leak_into_status(self):
        observation=Observation()
        for host in (None,{},123,True,b"PRIVATE_VALUE","PRIVATE_VALUE","fe80::1%PRIVATE_VALUE"):
            observation.connection(host,443)
        for port in (None,{},True,0,65536,"PRIVATE_VALUE"):
            observation.connection("127.0.0.1",port)
        value=observation.snapshot()
        self.assertEqual(value["connections"],[])
        self.assertNotIn("PRIVATE_VALUE",json.dumps(value))

    def test_destinations_are_bounded_and_panel_requests_do_not_consume_capacity(self):
        observation=Observation()
        for index in range(1,102):
            observation.connection("127.0.0.1",18973)
            observation.connection(f"10.0.0.{index}",443)
        value=observation.snapshot()
        self.assertEqual(len(value["connections"]),100)
        self.assertEqual(value["dashboard"]["externalDestinations"],100)

    def test_all_field_labels_and_observed_sdk_calls_have_static_russian_titles(self):
        self.assertEqual(set(FIELD_TITLES),set(ALLOWED_FIELDS))
        self.assertEqual(len(FIELD_TITLES),13)
        observation=Observation()
        for symbol in TRACER_TITLES:
            observation.tracer_call(symbol)
        value=observation.snapshot()
        self.assertEqual(len(value["tracerCalls"]),17)
        self.assertTrue(all(row["title"]==TRACER_TITLES[row["function"]] for row in value["tracerCalls"]))
        self.assertIsNone(value["observation"]["blockedCalls"])
        self.assertIn("главного приложения пока недоступен",value["observation"]["coverage"])

    def test_protection_status_distinguishes_enabled_masking_and_disabled_log_attachment(self):
        rules=rules_from_manifest({"files":build_mod.specifications(["accounts","privacy","isolation"])})
        rows={row["id"]:row for row in rules}
        self.assertEqual(rows["log_masking"]["statusText"],"Маскировка включена")
        self.assertEqual(rows["error_logs"]["statusText"],"Автоматическое приложение журналов отключено")
        self.assertTrue(all(row["state"]=="enabled" for row in rows.values()))
        unknown=rules_from_manifest({"files":[]})
        self.assertTrue(all(row["statusText"]=="Защита не подтверждена" for row in unknown))


class LifecycleTests(unittest.TestCase):
    def fixture_monitor(self):
        classifier = patch("privacy_server.original_image", return_value=False)
        classifier.start()
        self.addCleanup(classifier.stop)
        monitor = Monitor.__new__(Monitor)
        monitor.device = Mock()
        monitor.device.enumerate_processes.return_value = [SimpleNamespace(pid=123, name="max.exe")]
        monitor.sessions, monitor.roles, monitor.failed = {}, {}, {}
        monitor.observation = Observation()
        monitor.activity_lock = threading.Lock()
        monitor.client_pids, monitor.scan_completed = set(), False
        monitor.main_client_pids = set()
        monitor.profile_pids = set()
        path = Path("D:/synthetic/max.exe")
        import os
        monitor.allowed = {os.path.normcase(str(path)): False}
        monitor.profile_paths = set(monitor.allowed)
        return monitor, path

    def test_previous_mods_and_unknown_paths_block_import_without_attaching(self):
        monitor, current = self.fixture_monitor()
        previous, official = Path("D:/synthetic/previous/max.exe"), Path("D:/official/max.exe")
        import os
        monitor.profile_paths.add(os.path.normcase(str(previous)))
        paths = {123: current, 124: previous, 125: official, 126: None}
        monitor.device.enumerate_processes.return_value = [SimpleNamespace(pid=pid, name="max.exe") for pid in paths]
        monitor.attach = Mock()
        with patch("privacy_server.process_path", side_effect=lambda pid: paths[pid]), \
                patch("privacy_server.current_tcp_endpoints", return_value=[]):
            monitor.tick()
        monitor.attach.assert_called_once_with(123, service=False)
        self.assertEqual(monitor.activity(), (1, True))
        self.assertEqual(monitor.main_activity(), (1, True))
        self.assertEqual(monitor.profile_activity(), (4, True))

    def test_orphan_calls_service_is_not_a_running_main_client(self):
        monitor, _path = self.fixture_monitor()
        service = Path("D:/synthetic/max-service/max-service.exe")
        import os
        monitor.allowed = {os.path.normcase(str(service)): True}
        monitor.profile_paths = set(monitor.allowed)
        monitor.device.enumerate_processes.return_value = [SimpleNamespace(pid=456, name="max-service.exe")]
        monitor.attach = Mock()
        with patch("privacy_server.process_path", return_value=service), \
                patch("privacy_server.current_tcp_endpoints", return_value=[]):
            monitor.tick()
        self.assertEqual(monitor.activity(), (1, True))
        self.assertEqual(monitor.profile_activity(), (1, True))
        self.assertEqual(monitor.main_activity(), (0, True))

    def test_real_frida_failure_is_sanitized_and_live_pid_can_retry(self):
        monitor, path = self.fixture_monitor()
        monitor.attach = Mock(side_effect=[frida.PermissionDeniedError("PRIVATE_VALUE"), None])
        with patch("privacy_server.process_path", return_value=path), \
                patch("privacy_server.current_tcp_endpoints", return_value=[]), \
                patch("privacy_server.time.monotonic", return_value=100) as clock:
            monitor.tick()
            self.assertEqual(monitor.attach.call_count, 1)
            clock.return_value = 104
            monitor.tick()
            self.assertEqual(monitor.attach.call_count, 1)
            clock.return_value = 106
            monitor.tick()
            self.assertEqual(monitor.attach.call_count, 2)
        self.assertEqual(monitor.failed, {})
        self.assertNotIn("PRIVATE_VALUE", json.dumps(monitor.observation.snapshot()))

    def test_snapshot_timeout_removes_stale_counter_and_schedules_retry(self):
        monitor, path = self.fixture_monitor()
        session, script, channel = Mock(), Mock(), Mock()
        channel.snapshot.side_effect = SnapshotTimeout("PRIVATE_VALUE")
        monitor.sessions[123] = session, script, channel
        monitor.roles[123] = "main"
        monitor.observation.attach(123, tracer_hooks=1, transport_hooks=2)
        with patch("privacy_server.process_path", return_value=path), \
                patch("privacy_server.bounded_rpc", side_effect=lambda call: call()):
            monitor.tick()
        channel.close.assert_called_once()
        session.detach.assert_called_once()
        self.assertFalse(monitor.observation.snapshot()["observation"]["active"])
        self.assertIn(123, monitor.failed)

    def test_attach_load_failure_closes_the_snapshot_channel(self):
        monitor, _path = self.fixture_monitor()
        monitor.specs, monitor.source = {False: {}}, "synthetic"
        session, script, channel = Mock(), Mock(), Mock()
        monitor.device.attach.return_value = session
        session.create_script.return_value = script
        script.load.side_effect = frida.InvalidOperationError("synthetic")
        with patch("privacy_server.SnapshotChannel", return_value=channel), \
                patch("privacy_server.bounded_rpc", side_effect=lambda call: call()):
            with self.assertRaises(frida.InvalidOperationError):
                monitor.attach(123, service=False)
        channel.close.assert_called_once()
        session.detach.assert_called_once()

    def test_live_loopback_listener_cannot_be_bound_twice(self):
        first = LoopbackServer(("127.0.0.1", 0), handler_factory(lambda: {}))
        self.addCleanup(first.server_close)
        with self.assertRaises(OSError):
            second = LoopbackServer(first.server_address, handler_factory(lambda: {}))
            second.server_close()

    def test_idle_exit_waits_for_scan_and_resets_when_client_returns(self):
        idle = IdleDeadline(20)
        self.assertFalse(idle.expired(0, False, 100))
        self.assertFalse(idle.expired(0, True, 200))
        self.assertFalse(idle.expired(0, True, 219))
        self.assertFalse(idle.expired(1, True, 221))
        self.assertFalse(idle.expired(0, True, 222))
        self.assertTrue(idle.expired(0, True, 242))
        self.assertFalse(IdleDeadline(0).expired(0, True, 100000))

    def test_spawn_is_piped_and_failure_kills_only_the_new_pid(self):
        monitor = Monitor.__new__(Monitor)
        monitor.device = Mock()
        monitor.device.spawn.return_value = 123
        monitor.attach = Mock()
        monitor.activity_lock = threading.Lock()
        monitor.client_pids = set()
        monitor.main_client_pids = set()
        monitor.profile_pids = set()
        path = Path("D:/synthetic/max.exe")
        from contextlib import nullcontext
        native_env = {"SYNTHETIC": "1", "WEBVIEW2_USER_DATA_FOLDER": "D:/synthetic/MXMOD/business-webview2"}
        with patch("external_environment.native_environment", side_effect=lambda: nullcontext({"SYNTHETIC": "1"})), \
                patch("launch_mod.prepare_config", return_value=(Path("D:/synthetic/MXMOD/config.json"), "synthetic")), \
                patch("business_environment.child_environment", return_value=native_env):
            self.assertEqual(monitor.launch(path), 123)
            monitor.device.spawn.assert_called_once_with([str(path)], cwd=str(path.parent), stdio="pipe",
                                                        env={**native_env, "QML_XHR_ALLOW_FILE_READ": "1"})
            monitor.device.kill.assert_not_called()
            monitor.attach.side_effect = ValueError("synthetic attach failure")
            monitor.device.spawn.return_value = 456
            with self.assertRaises(ValueError):
                monitor.launch(path)
        monitor.device.kill.assert_called_once_with(456)
        self.assertEqual(monitor.client_pids, {123})


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler_factory(
            lambda: {"schema": 1, "safe": True}, lambda: {"schema": 1, "kind": "synthetic"}))
        self.server.daemon_threads = True
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(timeout=2)

    def request(self, method="GET", path="/status", **headers):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=2)
        connection.request(method, path, headers={"Host": f"127.0.0.1:{PORT}", **headers})
        response = connection.getresponse()
        result = response.status, dict(response.headers), response.read()
        connection.close()
        return result

    def test_status_is_json_readonly_and_not_cacheable(self):
        code, headers, body = self.request()
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body), {"schema": 1, "safe": True})
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_external_origin_host_and_unknown_route_are_rejected(self):
        for arguments in ({"Origin": "https://untrusted.invalid"}, {"Host": "untrusted.invalid"}, {"path": "/other"}):
            with self.subTest(arguments=arguments):
                self.assertEqual(self.request(**arguments)[0], 404)

    def test_no_mutating_http_endpoint(self):
        self.assertEqual(self.request(method="POST")[0], 405)

    def test_identity_is_readonly_with_same_origin_restrictions(self):
        self.assertEqual(json.loads(self.request(path="/identity")[2]), {"schema": 1, "kind": "synthetic"})
        self.assertEqual(self.request(path="/identity", Origin="https://untrusted.invalid")[0], 404)


if __name__ == "__main__":
    unittest.main()
