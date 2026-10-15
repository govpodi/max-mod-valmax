"""Synthetic-only parser/redaction tests. Never open a live MAX packet/profile."""
import json
import random
import struct
import unittest
from unittest.mock import patch

import network_metadata as target


def string(value: bytes) -> bytes:
    length = len(value)
    if length < 32:
        return bytes((0xa0+length,))+value
    if length < 256:
        return b"\xd9"+bytes((length,))+value
    return b"\xda"+length.to_bytes(2,"big")+value


def mapping(items: list[tuple[str, bytes]]) -> bytes:
    return bytes((0x80+len(items),))+b"".join(string(key.encode())+value for key,value in items)


def payload(value: bytes = b"SECRET_DEVICE_NAME") -> bytes:
    return mapping([("userAgent", mapping([("deviceName", string(value)),
                                             ("osVersion", string(b"SECRET_OS")),
                                             ("buildNumber", b"\xce\x01\x02\x03\x04")])),
                    ("deviceId",string(b"SECRET_DEVICE_ID")),
                    ("clientSessionId", b"\xd3"+struct.pack(">q",1234567890123))])


def frame(body: bytes, opcode: int = 6, flags: int = 0) -> bytes:
    return b"\x01\x00\x00\x09"+opcode.to_bytes(2,"big")+bytes((flags,))+len(body).to_bytes(3,"big")+body


class NetworkMetadataTest(unittest.TestCase):
    def test_header_fallback_keeps_unknown_body_unparsed_and_rejects_other_buffers(self):
        unknown = frame(mapping([("PRIVATE_FIELD", string(b"SECRET_VALUE"))]))
        value = target.session_init_header_metadata(unknown)
        self.assertEqual(value, {"protocol": "msgpack", "opcode": 6,
                                 "fields": [], "bytes": len(unknown)})
        self.assertIsNone(target.packet_metadata(unknown))
        self.assertNotIn("SECRET", json.dumps(value))
        for invalid in (b"SECRET_VALUE", frame(b"SECRET_VALUE", opcode=19),
                        frame(b"SECRET_VALUE", flags=1), unknown[:-1], None):
            self.assertIsNone(target.session_init_header_metadata(invalid))
    def test_reviewed_payload_reports_only_static_names_types_and_lengths(self):
        result = target.describe_session_init_payload(payload())
        self.assertEqual(result["opcode"],6)
        self.assertEqual(result["fields"],["buildNumber","clientSessionId","deviceId","deviceName","osVersion","userAgent"])
        serialized=json.dumps(result)
        self.assertNotIn("SECRET",serialized)
        self.assertNotIn("1234567890123",serialized)
        self.assertNotIn("16909060",serialized)
        self.assertIn({"field":"deviceName","type":"string","length":18},result["structure"])

    def test_status_validator_discards_structural_and_other_extra_values(self):
        metadata=target.describe_session_init_payload(payload())
        metadata["token"]=b"SECRET_AUTH_VALUE"
        metadata["request"]=object()
        result=target.validate_metadata(metadata)
        self.assertEqual(set(result),{"protocol","opcode","fields","bytes"})
        self.assertNotIn("SECRET",json.dumps(result))

    def test_scalar_values_never_need_unicode_decoding(self):
        result=target.describe_session_init_payload(payload(b"\xff\x00\xfeSECRET"))
        self.assertIsNotNone(result)
        self.assertNotIn("SECRET",json.dumps(result))

    def test_unknown_and_sensitive_map_keys_fail_closed(self):
        for key in ("token","password","text","qr","file","SECRET_DYNAMIC_KEY"):
            with self.subTest(key=key):
                body=mapping([("userAgent",mapping([(key,string(b"SECRET"))]))])
                self.assertIsNone(target.describe_session_init_payload(body))

    def test_unknown_top_keys_fail_closed(self):
        body=mapping([("userAgent",mapping([("deviceName",string(b"secret"))])),("phone",string(b"secret"))])
        self.assertIsNone(target.describe_session_init_payload(body))

    def test_duplicate_keys_reject_even_within_allowlist(self):
        body=mapping([("userAgent",mapping([("deviceName",string(b"a")),("deviceName",string(b"b"))]))])
        self.assertIsNone(target.describe_session_init_payload(body))

    def test_non_ascii_dynamic_key_rejects(self):
        body=b"\x81"+string("секрет".encode())+string(b"secret")
        self.assertIsNone(target.describe_session_init_payload(body))

    def test_long_dynamic_key_rejects(self):
        body=b"\x81"+string(b"secret"*10)+string(b"value")
        self.assertIsNone(target.describe_session_init_payload(body))

    def test_user_agent_map_required(self):
        self.assertIsNone(target.describe_session_init_payload(mapping([("deviceId",string(b"secret"))])))

    def test_arbitrary_root_shapes_reject(self):
        for body in (b"\x90",b"\xc0",b"\x01",string(b"secret"),b"\x80"):
            with self.subTest(marker=body[0]):
                self.assertIsNone(target.describe_session_init_payload(body))

    def test_arrays_binary_extensions_and_nested_maps_reject(self):
        for value in (b"\x91\x01",b"\xc4\x01x",b"\xd4\x00x",b"\x81\xa4text\xa1x"):
            with self.subTest(marker=value[0]):
                self.assertIsNone(target.describe_session_init_payload(mapping([("userAgent",mapping([("deviceName",value)]))])))

    def test_all_scalar_numeric_encodings_are_redacted(self):
        values=(b"\x01",b"\xff",b"\xc0",b"\xc2",b"\xc3",b"\xcc\x7f",b"\xcd\x01\x02",
                b"\xce"+b"\x01"*4,b"\xcf"+b"\x01"*8,b"\xd0\x80",b"\xd1"+b"\x01"*2,
                b"\xd2"+b"\x01"*4,b"\xd3"+b"\x01"*8,b"\xca"+struct.pack(">f",7.125),b"\xcb"+struct.pack(">d",7.125))
        for value in values:
            with self.subTest(marker=value[0]):
                result=target.describe_session_init_payload(mapping([("userAgent",mapping([("buildNumber",value)]))]))
                self.assertIsNotNone(result)
                self.assertNotIn("value",result["structure"][-1])

    def test_str8_str16_and_str32_lengths_without_values(self):
        values=(string(b"secret"*10),string(b"secret"*100),b"\xdb\x00\x00\x00\x06secret")
        for value in values:
            result=target.describe_session_init_payload(mapping([("userAgent",mapping([("deviceName",value)]))]))
            self.assertIsNotNone(result)
            self.assertNotIn("secret",json.dumps(result))

    def test_map16_and_map32_encodings(self):
        child=string(b"deviceName")+string(b"secret")
        for prefix in (b"\xde\x00\x01",b"\xdf\x00\x00\x00\x01"):
            result=target.describe_session_init_payload(mapping([("userAgent",prefix+child)]))
            self.assertIsNotNone(result)

    def test_every_truncated_prefix_rejects_without_packet_exceptions(self):
        body=payload()
        for length in range(len(body)):
            self.assertIsNone(target.describe_session_init_payload(body[:length]))

    def test_trailing_data_rejects(self):
        self.assertIsNone(target.describe_session_init_payload(payload()+b"SECRET"))

    def test_oversize_empty_and_invalid_input_reject(self):
        for body in (b"",b"x"*(target.MAX_PAYLOAD_BYTES+1),None,"SECRET",1):
            self.assertIsNone(target.describe_session_init_payload(body))

    def test_typed_memoryview_budget_counts_bytes_before_parsing(self):
        body=memoryview(b"x"*(target.MAX_PAYLOAD_BYTES+4)).cast("I")
        with patch.object(target._Cursor,"object") as parser:
            self.assertIsNone(target.describe_session_init_payload(body))
            parser.assert_not_called()

    def test_synthetic_random_bytes_never_escape_or_emit_values(self):
        rng=random.Random(26351)
        for _ in range(5000):
            body=rng.randbytes(rng.randrange(0,512))
            result=target.describe_session_init_payload(body)
            if result is not None:
                checked=target.validate_metadata(result)
                self.assertIsNotNone(checked)
                self.assertTrue(set(checked["fields"])<=target.ALLOWED_FIELDS)

    def test_other_opcodes_never_invoke_body_parser(self):
        with patch.object(target,"describe_session_init_payload") as parser:
            self.assertIsNone(target.describe_session_init_frame(frame(b"SECRET_AUTH_MESSAGE",opcode=16)))
            parser.assert_not_called()

    def test_nonzero_control_flags_never_invoke_body_parser(self):
        with patch.object(target,"describe_session_init_payload") as parser:
            self.assertIsNone(target.describe_session_init_frame(frame(b"SECRET",flags=1)))
            parser.assert_not_called()

    def test_frame_exact_length_and_opcode(self):
        packet=frame(payload())
        result=target.describe_session_init_frame(packet)
        self.assertEqual(result["bytes"],len(packet))
        self.assertNotIn("SECRET",json.dumps(result))
        self.assertIsNone(target.describe_session_init_frame(packet[:-1]))
        self.assertIsNone(target.describe_session_init_frame(packet+b"x"))

    def test_collector_entry_point_has_exact_safe_contract(self):
        result=target.packet_metadata(frame(payload()))
        self.assertEqual(set(result),{"protocol","opcode","fields","bytes"})
        self.assertNotIn("SECRET",json.dumps(result))
        self.assertIsNone(target.packet_metadata(frame(b"SECRET_AUTH_VALUE",opcode=16)))

    def test_complete_statically_reviewed_ten_field_user_agent(self):
        items=[(key,string(b"SECRET_DEVICE_VALUE")) for key in sorted(target.USER_AGENT_FIELDS) if key!="buildNumber"]
        items.append(("buildNumber",b"\xce\x01\x02\x03\x04"))
        body=mapping([("userAgent",mapping(items)),("deviceId",string(b"SECRET_DEVICE_ID")),("clientSessionId",b"\x01")])
        metadata,reason=target.packet_metadata_diagnostic(frame(body))
        self.assertEqual(reason,"ok")
        self.assertEqual(len(target.USER_AGENT_FIELDS),10)
        self.assertEqual(metadata["fields"],sorted(target.ALLOWED_FIELDS))
        self.assertIn("screen",metadata["fields"])
        self.assertNotIn("SECRET",json.dumps(metadata))

    def test_fixed_diagnostic_reasons_cover_failure_shapes_without_values(self):
        nested=lambda value:mapping([("userAgent",mapping([("deviceName",value)]))])
        cases={
            "input":None,
            "bounds":b"x"*(target.MAX_FRAME_BYTES+1),
            "header":b"SECRET123",
            "opcode":frame(b"SECRET",opcode=16),
            "flags":frame(b"SECRET",flags=1),
            "length":frame(payload())[:-1],
            "marker":frame(b"\x90"),
            "layout":frame(b"\x80"),
            "unknown_key":frame(mapping([("userAgent",mapping([("SECRET_FIELD",string(b"SECRET_VALUE"))]))])),
            "duplicate_key":frame(mapping([("userAgent",mapping([("deviceName",string(b"SECRET1")),("deviceName",string(b"SECRET2"))]))])),
            "truncated":frame(nested(b"\xa5xx")),
            "trailing":frame(payload()+b"SECRET_TRAILING"),
            "depth":frame(nested(b"\x81\xa4text\xa6SECRET")),
            "scalar_type":frame(nested(b"\xc4\x06SECRET")),
            "missing_user_agent":frame(mapping([("deviceId",string(b"SECRET"))]))}
        self.assertEqual(set(cases),target.DIAGNOSTIC_REASONS-{"ok"})
        for expected,packet in cases.items():
            with self.subTest(reason=expected):
                metadata,reason=target.packet_metadata_diagnostic(packet)
                self.assertIsNone(metadata)
                self.assertEqual(reason,expected)
                self.assertNotIn("SECRET",json.dumps([metadata,reason]))

    def test_diagnostics_never_return_partial_names_on_schema_rejection(self):
        body=mapping([("userAgent",mapping([("deviceName",string(b"SECRET_VALUE")),("SECRET_KEY",string(b"SECRET_VALUE"))]))])
        self.assertEqual(target.packet_metadata_diagnostic(frame(body)),(None,"unknown_key"))

    def test_exception_argument_is_always_a_static_reason_code(self):
        for value in ("SECRET",object(),None,True,"ok"):
            error=target._Invalid(value)
            self.assertEqual(error.args,("layout",))
            self.assertEqual(str(error),"layout")

    def test_random_diagnostics_are_bounded_static_codes(self):
        rng=random.Random(26352)
        for _ in range(5000):
            metadata,reason=target.packet_metadata_diagnostic(frame(rng.randbytes(rng.randrange(1,512))))
            self.assertIn(reason,target.DIAGNOSTIC_REASONS)
            self.assertTrue(metadata is None or reason=="ok")

    def test_validator_rejects_unbounded_unknown_or_mistyped_metadata(self):
        valid={"protocol":"msgpack","opcode":6,"fields":["userAgent"],"bytes":100}
        bad=(None,[],dict(valid,protocol="http"),dict(valid,opcode=True),dict(valid,opcode=-1),
             dict(valid,opcode=65536),dict(valid,bytes=True),dict(valid,bytes=-1),
             dict(valid,bytes=target.MAX_METADATA_BYTES+1),dict(valid,fields=["SECRET"]),
             dict(valid,fields="userAgent"),dict(valid,fields=[object()]),dict(valid,opcode=16))
        for metadata in bad:
            self.assertIsNone(target.validate_metadata(metadata))

    def test_header_only_other_opcode_has_no_fields(self):
        self.assertEqual(target.validate_metadata({"protocol":"msgpack","opcode":16,"fields":[],"bytes":10}),
                         {"protocol":"msgpack","opcode":16,"fields":[],"bytes":10})


if __name__ == "__main__":
    unittest.main()
