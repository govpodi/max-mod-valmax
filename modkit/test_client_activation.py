"""Mocked native window metadata/actions; never operates an actual window."""
from pathlib import Path
import unittest
from unittest.mock import patch

import client_activation as module


EXE = r"D:\Synthetic\ValMAX\runtime\mod\max.exe"
EXPECTED = EXE.casefold()


def window(pid=71, **changes):
    return {"pid": pid, "owner": 0, "style": 0x00CF0000, "exstyle": 0x40000,
            "class": "Qt6112QWindowIcon", "width": 900, "height": 700,
            "visible": True, "iconic": False, **changes}


class Clock:
    def __init__(self):
        self.value = 0.0
        self.on_sleep = None

    def now(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds
        if self.on_sleep:
            self.on_sleep()


class Windows:
    def __init__(self, items=None, images=None):
        self.items = {101: window()} if items is None else items
        self.images = {71: EXE} if images is None else images
        self.opened, self.closed, self.shown, self.foregrounded = [], [], [], []
        self.inaccessible, self.dead = set(), set()
        self.show_ok, self.apply_show, self.focus_ok = True, True, True
        self.info_calls, self.image_calls, self.alive_calls = 0, 0, 0
        self.on_info = None
        self.on_image = None
        self.on_alive = None
        self.enumeration_ok = True

    def windows(self):
        return list(self.items) if self.enumeration_ok else None

    def info(self, hwnd):
        self.info_calls += 1
        if self.on_info:
            self.on_info(self.info_calls, hwnd)
        return dict(self.items[hwnd]) if hwnd in self.items else None

    def open_process(self, pid):
        self.opened.append(pid)
        return 0 if pid in self.inaccessible else pid + 10000

    def alive(self, handle):
        self.alive_calls += 1
        if self.on_alive:
            self.on_alive(self.alive_calls, handle)
        return handle - 10000 not in self.dead

    def image(self, handle):
        self.image_calls += 1
        if self.on_image:
            self.on_image(self.image_calls, handle)
        return self.images.get(handle - 10000)

    def close(self, handle):
        self.closed.append(handle)

    def show(self, hwnd, command):
        self.shown.append((hwnd, command))
        if self.show_ok and self.apply_show:
            self.items[hwnd].update(visible=True, iconic=False)
        return self.show_ok

    def foreground(self, hwnd):
        self.foregrounded.append(hwnd)
        return self.focus_ok


class ClientActivationTests(unittest.TestCase):
    def run_activation(self, api, clock=None, **options):
        clock = Clock() if clock is None else clock
        return module._activate(EXPECTED, api, clock=clock.now, sleep=clock.sleep, **options)

    def test_exact_windows_image_spelling(self):
        for value in (EXE, EXE.upper(), EXE.replace("\\", "/"), "\\\\?\\" + EXE):
            self.assertEqual(module._normalized(value), EXPECTED)
        for value in (None, b"file", "relative/max.exe", r"\\server\share\max.exe",
                      r"C:\folder\other.exe", r"C:\folder\..\max.exe",
                      r"C:\folder.\max.exe", r"C:\folder:stream\max.exe",
                      "C:/x/max.exe\0", "1:/x/max.exe"):
            self.assertIsNone(module._normalized(value))

    def test_hidden_tray_window_is_shown_without_spawning(self):
        api = Windows({101: window(visible=False)})
        self.assertTrue(self.run_activation(api))
        self.assertEqual(api.shown, [(101, 5)])
        self.assertEqual(api.foregrounded, [101])
        self.assertEqual(api.closed, [10071])

    def test_iconic_window_is_restored(self):
        api = Windows({101: window(visible=False, iconic=True)})
        self.assertTrue(self.run_activation(api))
        self.assertEqual(api.shown, [(101, 9)])

    def test_visible_window_is_only_activated_preserving_geometry(self):
        api = Windows()
        before = dict(api.items[101])
        self.assertTrue(self.run_activation(api))
        self.assertEqual(api.shown, [])
        self.assertEqual(api.items[101], before)
        self.assertEqual(api.foregrounded, [101])

    def test_os_foreground_denial_keeps_restored_window_successful(self):
        api = Windows({101: window(visible=False)})
        api.focus_ok = False
        self.assertTrue(self.run_activation(api))
        self.assertTrue(api.items[101]["visible"])

    def test_official_or_other_runtime_window_is_not_touched(self):
        api = Windows({101: window(71), 102: window(72, visible=False)},
                      {71: r"C:\Official MAX\max.exe", 72: EXE})
        self.assertTrue(self.run_activation(api))
        self.assertEqual(api.shown, [(102, 5)])
        self.assertEqual(api.foregrounded, [102])
        self.assertCountEqual(api.closed, [10071, 10072])

    def test_tool_menu_owned_child_and_wrong_class_windows_are_excluded(self):
        invalid = [window(owner=55), window(exstyle=module._WS_EX_TOOLWINDOW),
                   window(exstyle=module._WS_EX_NOACTIVATE), window(style=module._WS_CHILD),
                   window(**{"class": "Qt6112QWindowPopupDropShadowSaveBits"}),
                   window(**{"class": "OtherQWindowIcon"}), window(width=0), window(height=-1)]
        for value in invalid:
            with self.subTest(window_class=value["class"]):
                api = Windows({101: value})
                self.assertFalse(self.run_activation(api))
                self.assertEqual(api.opened, [])
                self.assertEqual(api.shown, [])
                self.assertEqual(api.foregrounded, [])

    def test_qt_release_class_variants_are_accepted(self):
        for name in ("Qt6QWindowIcon", "Qt682QWindowIcon", "Qt6112QWindowOwnDCIcon",
                     "Qt6112QWindowIcon{12345678-1234-1234-1234-123456789abc}"):
            api = Windows({101: window(**{"class": name})})
            self.assertTrue(self.run_activation(api))

    def test_ambiguous_independent_main_windows_are_not_guessed(self):
        api = Windows({101: window(), 102: window()})
        self.assertFalse(self.run_activation(api))
        self.assertEqual(api.opened, [71])
        self.assertEqual(api.closed, [10071])
        self.assertEqual(api.foregrounded, [])

    def test_two_matching_processes_are_not_guessed(self):
        api = Windows({101: window(71), 102: window(72)}, {71: EXE, 72: EXE})
        self.assertFalse(self.run_activation(api))
        self.assertCountEqual(api.closed, [10071, 10072])
        self.assertEqual(api.foregrounded, [])

    def test_inaccessible_or_dead_process_cannot_be_activated(self):
        for kind in ("inaccessible", "dead"):
            api = Windows()
            getattr(api, kind).add(71)
            self.assertFalse(self.run_activation(api))
            self.assertEqual(api.foregrounded, [])
            self.assertEqual(api.closed, [] if kind == "inaccessible" else [10071])

    def test_window_pid_reuse_before_show_is_rejected(self):
        api = Windows({101: window(visible=False)})
        api.on_info = lambda count, hwnd: api.items[hwnd].update(pid=72) if count == 2 else None
        self.assertFalse(self.run_activation(api))
        self.assertEqual(api.shown, [])
        self.assertEqual(api.closed, [10071])

    def test_process_image_rechecked_before_any_ui_action(self):
        api = Windows({101: window(visible=False)})
        api.on_image = lambda count, _: api.images.update({71: r"C:\Other\max.exe"}) if count == 2 else None
        self.assertFalse(self.run_activation(api))
        self.assertEqual(api.shown, [])
        self.assertEqual(api.foregrounded, [])

    def test_process_exit_during_async_restore_stops_activation(self):
        api = Windows({101: window(visible=False)})
        api.on_alive = lambda count, _: api.dead.add(71) if count == 3 else None
        self.assertFalse(self.run_activation(api))
        self.assertEqual(api.shown, [(101, 5)])
        self.assertEqual(api.foregrounded, [])
        self.assertEqual(api.closed, [10071])

    def test_show_failure_does_not_claim_success(self):
        api = Windows({101: window(visible=False)})
        api.show_ok = False
        self.assertFalse(self.run_activation(api))
        self.assertEqual(api.foregrounded, [])

    def test_hung_window_async_restore_is_bounded(self):
        api = Windows({101: window(visible=False)})
        api.apply_show = False
        clock = Clock()
        self.assertFalse(self.run_activation(api, clock))
        self.assertLessEqual(clock.value, module._SHOW_SECONDS)
        self.assertEqual(api.foregrounded, [])
        self.assertEqual(api.closed, [10071])

    def test_enumeration_failure_or_empty_desktop_does_nothing(self):
        for failed in (True, False):
            api = Windows({})
            api.enumeration_ok = not failed
            self.assertFalse(self.run_activation(api))
            self.assertEqual(api.opened, [])

    def test_public_entry_waits_for_window_creation_without_another_client(self):
        api, clock = Windows({}), Clock()
        clock.on_sleep = lambda: api.items.update({101: window(visible=False)})
        with patch.object(module.os, "name", "nt"), patch.object(module, "_Windows", return_value=api), \
             patch.object(module.time, "monotonic", side_effect=clock.now), \
             patch.object(module.time, "sleep", side_effect=clock.sleep):
            self.assertTrue(module.activate(Path(EXE)))
        self.assertLess(clock.value, module._ACTIVATION_SECONDS)
        self.assertEqual(api.shown, [(101, 5)])

    def test_public_entry_has_total_deadline_for_missing_window(self):
        api, clock = Windows({}), Clock()
        with patch.object(module.os, "name", "nt"), patch.object(module, "_Windows", return_value=api), \
             patch.object(module.time, "monotonic", side_effect=clock.now), \
             patch.object(module.time, "sleep", side_effect=clock.sleep):
            self.assertFalse(module.activate(EXE))
        self.assertLessEqual(clock.value, module._ACTIVATION_SECONDS)
        self.assertEqual(api.foregrounded, [])

    def test_public_entry_total_deadline_includes_hung_restore(self):
        api, clock = Windows({101: window(visible=False)}), Clock()
        api.apply_show = False
        with patch.object(module.os, "name", "nt"), patch.object(module, "_Windows", return_value=api), \
             patch.object(module.time, "monotonic", side_effect=clock.now), \
             patch.object(module.time, "sleep", side_effect=clock.sleep):
            self.assertFalse(module.activate(EXE))
        self.assertLessEqual(clock.value, module._ACTIVATION_SECONDS)
        self.assertEqual(api.foregrounded, [])

    def test_native_failure_is_fixed_false_and_closes_process_handle(self):
        api = Windows()
        api.on_image = lambda *_: (_ for _ in ()).throw(OSError("SYNTHETIC_SECRET"))
        with patch.object(module.os, "name", "nt"), patch.object(module, "_Windows", return_value=api):
            self.assertFalse(module.activate(EXE))
        self.assertEqual(api.closed, [10071])
        self.assertEqual(api.foregrounded, [])

    def test_invalid_entry_never_initializes_native_api(self):
        with patch.object(module, "_Windows", side_effect=AssertionError("native access")):
            self.assertFalse(module.activate("relative/max.exe"))
            self.assertFalse(module.activate(object()))
            with patch.object(module.os, "name", "posix"):
                self.assertFalse(module.activate(EXE))


if __name__ == "__main__":
    unittest.main()
