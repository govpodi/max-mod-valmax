"""Deterministic test-only HTTP framing for early loopback rejection tests.

The ordinary HTTP client sends headers and byte bodies separately. On Windows,
an immediate rejection can close the socket before its second write arrives,
resetting the connection before the test reads the already produced response.
Coalesce only in-memory unchunked bodies. Preserve every header/body byte and
the standard implementation for streams, chunked requests and bodyless reads.
No retry, response substitution, delay or production server change is used.
"""
import http.client
import unittest


class CoalescedHTTPConnection(http.client.HTTPConnection):
    def _send_output(self, message_body=None, encode_chunked=False):
        if isinstance(message_body, (bytes, bytearray, memoryview)) and not encode_chunked:
            message = b'\r\n'.join(self._buffer) + b'\r\n\r\n' + bytes(message_body)
            self._buffer.clear()
            self.send(message)
        else:
            super()._send_output(message_body, encode_chunked)


class CaptureSocket:
    def __init__(self):
        self.writes = []

    def sendall(self, value):
        self.writes.append(bytes(value))


class CoalescedTransportTests(unittest.TestCase):
    def connection(self):
        connection = CoalescedHTTPConnection('127.0.0.1', 18973)
        connection.sock = CaptureSocket()
        return connection

    def test_byte_body_and_security_headers_are_preserved_in_one_write(self):
        for body in (b'owned\x00synthetic\xff', bytearray(b'owned'), memoryview(b'synthetic')):
            with self.subTest(body_type=type(body).__name__):
                connection = self.connection()
                connection.request('POST', '/owned-fixture', body=body, headers={
                    'Host': '127.0.0.1:18973', 'Origin': 'https://untrusted.invalid',
                    'X-Max-Mod-Local-Write': 'wrong-marker', 'Content-Type': 'text/plain'})
                self.assertEqual(1, len(connection.sock.writes))
                headers, sent_body = connection.sock.writes[0].split(b'\r\n\r\n', 1)
                self.assertEqual(bytes(body), sent_body)
                for header in (b'POST /owned-fixture HTTP/1.1', b'Host: 127.0.0.1:18973',
                               b'Origin: https://untrusted.invalid', b'X-Max-Mod-Local-Write: wrong-marker',
                               b'Content-Type: text/plain', f'Content-Length: {len(body)}'.encode()):
                    self.assertIn(header, headers)
                self.assertEqual([], connection._buffer)

    def test_explicit_length_and_empty_body_are_not_corrected(self):
        connection = self.connection()
        connection.request('POST', '/owned-fixture', body=b'', headers={'Content-Length': '262145'})
        self.assertEqual(1, len(connection.sock.writes))
        headers, body = connection.sock.writes[0].split(b'\r\n\r\n', 1)
        self.assertIn(b'Content-Length: 262145', headers)
        self.assertEqual(b'', body)

    def test_bodyless_and_chunked_requests_keep_standard_transport(self):
        connection = self.connection()
        connection.request('GET', '/owned-fixture')
        self.assertEqual(1, len(connection.sock.writes))
        self.assertTrue(connection.sock.writes[0].endswith(b'\r\n\r\n'))
        connection = self.connection()
        connection.request('POST', '/owned-fixture', body=iter([b'one', b'two']), encode_chunked=True)
        self.assertGreater(len(connection.sock.writes), 1)
        headers, body = b''.join(connection.sock.writes).split(b'\r\n\r\n', 1)
        self.assertIn(b'Transfer-Encoding: chunked', headers)
        self.assertEqual(b'3\r\none\r\n3\r\ntwo\r\n0\r\n\r\n', body)


if __name__ == '__main__':
    unittest.main()
