"""Synthetic observer batching tests; never touches native clients or profiles."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest
from unittest.mock import Mock, patch

from privacy_labels import TRACER_TITLES
from privacy_status import Observation
import test_privacy_status as fixtures
from test_privacy_monitor_js import HARNESS


class ObservationBatchingTests(unittest.TestCase):
    def test_batches_match_individual_observations_and_normalize_destinations(self):
        individual, batched = Observation(), Observation()
        for symbol in TRACER_TITLES:
            for _ in range(17):
                individual.tracer_call(symbol)
            batched.tracer_call(symbol, count=17)
        for host, port in (("8.8.8.8", 443), ("2001:0db8::1", 443),
                           ("127.0.0.1", 18973), ("::ffff:127.0.0.1", 18973)):
            for _ in range(31):
                individual.connection(host, port)
            batched.connection(host, port, count=31)
        expected, actual = individual.snapshot(), batched.snapshot()
        self.assertEqual(actual["tracerCalls"], expected["tracerCalls"])
        self.assertEqual(actual["connections"], expected["connections"])
        self.assertEqual(actual["dashboard"], expected["dashboard"])

    def test_invalid_counts_are_rejected_without_forwarding_arbitrary_values(self):
        observation = Observation()
        for count in (None, True, False, -1, 0, 1.0, 1000001, "PRIVATE_VALUE", {}):
            observation.tracer_call("tracer_init", count=count)
            observation.connection("8.8.8.8", 443, count=count)
        value = observation.snapshot()
        self.assertEqual(value["tracerCalls"], [])
        self.assertEqual(value["connections"], [])
        self.assertNotIn("PRIVATE_VALUE", json.dumps(value))
        observation.tracer_call("tracer_init", count=1000000)
        observation.connection("8.8.8.8", 443, count=1000000)
        value = observation.snapshot()
        self.assertEqual(value["tracerCalls"][0]["count"], 1000000)
        self.assertEqual(value["connections"][0]["count"], 1000000)

    def test_capacity_still_accepts_existing_destinations_after_full(self):
        observation = Observation()
        for index in range(1, 102):
            observation.connection(f"10.0.0.{index}", 443, count=17)
        observation.connection("10.0.0.1", 443, count=31)
        value = observation.snapshot()
        self.assertEqual(len(value["connections"]), 100)
        self.assertEqual(value["connections"][0]["count"], 48)

    def test_monitor_merges_each_valid_counter_once_with_original_caps(self):
        monitor, path = fixtures.LifecycleTests.fixture_monitor(self)
        observation = Observation()
        monitor.observation = Mock(wraps=observation)
        session, script, channel = Mock(), Mock(), Mock()
        monitor.sessions[123] = session, script, channel
        monitor.roles[123] = "main"
        channel.snapshot.return_value = {
            "stats": {"tracerHooks": 1, "transportHooks": 2},
            "tracerCounts": {**{symbol: 1000000 for symbol in TRACER_TITLES},
                             "PRIVATE_VALUE": True},
            "connections": {"8.8.8.8|443": 1000000, "2001:0db8::1|443": 3,
                            "127.0.0.1|18973": 4, "10.0.0.1|443": False,
                            "10.0.0.2|443": -1, "10.0.0.3|443": "PRIVATE_VALUE"},
            "packets": [], "failures": 0}
        with patch("privacy_server.process_path", return_value=path), \
                patch("privacy_server.bounded_rpc", side_effect=lambda call: call()):
            monitor.tick()
        self.assertEqual(monitor.observation.tracer_call.call_count, len(TRACER_TITLES))
        self.assertEqual(monitor.observation.connection.call_count, 3)
        value = observation.snapshot()
        self.assertTrue(all(row["count"] == 100000 for row in value["tracerCalls"]))
        self.assertEqual([row["count"] for row in value["connections"]], [1000, 3])
        self.assertNotIn("PRIVATE_VALUE", json.dumps(value))
        session.detach.assert_not_called()
        channel.close.assert_not_called()


@unittest.skipUnless(shutil.which("node"), "Node.js unavailable")
class JavascriptCounterCapacityTests(unittest.TestCase):
    def test_capacity_existing_keys_and_snapshot_reset_use_actual_observer(self):
        prefix = HARNESS.split("const a=setup();", 1)[0]
        checks = r"""
const value = setup();
for (let op = 7; op < 264; op++) value.packet(op, 24);
value.packet(7, 30);
for (let port = 1; port <= 101; port++) value.connect('192.0.2.1', port);
value.connect('192.0.2.1', 1);
let state = value.snapshot();
assert.strictEqual(state.packets.length, 256);
assert.strictEqual(state.omittedPackets, 1);
assert.strictEqual(state.packets.find(p => p.metadata.opcode === 7).count, 2);
assert.strictEqual(Object.keys(state.connections).length, 100);
assert.strictEqual(state.connections['192.0.2.1|1'], 2);
assert.strictEqual(state.connections['192.0.2.1|101'], undefined);
for (let op = 1000; op < 1256; op++) value.packet(op, 24);
for (let port = 1000; port < 1100; port++) value.connect('192.0.2.1', port);
state = value.snapshot();
assert.strictEqual(state.packets.length, 256);
assert.strictEqual(state.omittedPackets, 0);
assert.strictEqual(Object.keys(state.connections).length, 100);
assert.strictEqual(state.connections['192.0.2.1|1000'], 1);
assert.strictEqual(value.sent.length, 0);
assert(value.reads.every(n => n === 10));
console.log(JSON.stringify({checks: 12}));
"""
        result = subprocess.run([shutil.which("node"), "-e", prefix + checks,
                                 str(Path(__file__).with_name("privacy_monitor.js"))],
                                capture_output=True, text=True, timeout=10, check=True)
        self.assertEqual(json.loads(result.stdout), {"checks": 12})


if __name__ == "__main__":
    unittest.main()
