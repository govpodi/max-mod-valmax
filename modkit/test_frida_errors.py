"""Exception-boundary checks only; no Frida device access or MAX execution."""
import unittest

import frida
from frida_errors import FRIDA_ERRORS


class FridaErrorsTest(unittest.TestCase):
    def test_explicit_tuple_covers_installed_public_error_classes(self):
        public_errors = {getattr(frida, name) for name in dir(frida)
                         if name.endswith("Error") and isinstance(getattr(frida, name), type)
                         and issubclass(getattr(frida, name), Exception)}
        self.assertEqual(type(FRIDA_ERRORS), tuple)
        self.assertEqual(len(FRIDA_ERRORS), 14)
        self.assertEqual(len(set(FRIDA_ERRORS)), 14)
        self.assertEqual(set(FRIDA_ERRORS), public_errors)
        self.assertNotIn(Exception, FRIDA_ERRORS)
        self.assertNotIn(BaseException, FRIDA_ERRORS)

    def test_every_public_frida_error_is_caught_without_generic_error_base(self):
        for error_type in FRIDA_ERRORS:
            with self.subTest(error=error_type.__name__):
                try:
                    raise error_type("SYNTHETIC_PRIVATE_VALUE")
                except FRIDA_ERRORS as caught:
                    self.assertIs(type(caught), error_type)


if __name__ == "__main__":
    unittest.main()
