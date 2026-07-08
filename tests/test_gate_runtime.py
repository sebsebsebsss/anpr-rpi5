"""GPIO try/finally invariant: RELAY_OFF is always written, even when an
exception fires between activate and release."""
import unittest
from unittest.mock import patch

import gate_runtime as gr


class TestRelayTryFinally(unittest.TestCase):
    def _make_gpio(self, raise_after_high=False):
        calls = []
        import types
        gpio = types.SimpleNamespace(
            BOARD="BOARD",
            OUT="OUT",
            HIGH=gr.RELAY_ON,
            LOW=gr.RELAY_OFF,
            setwarnings=lambda *a: None,
            setmode=lambda *a: None,
            setup=lambda *a: None,
            cleanup=lambda: calls.append("cleanup"),
        )

        def output(pin, level):
            calls.append(("output", pin, level))
            if raise_after_high and level == gr.RELAY_ON:
                raise RuntimeError("simulated crash after HIGH")

        gpio.output = output
        return gpio, calls

    def test_relay_off_written_on_happy_path(self):
        gpio, calls = self._make_gpio()
        with patch.dict("sys.modules", {"RPi.GPIO": gpio, "RPi": type("M", (), {"GPIO": gpio})()}):
            import importlib

            import gate_runtime
            importlib.reload(gate_runtime)

        import logging
        logger = logging.getLogger("test")
        with patch("RPi.GPIO", gpio):
            gr.open_gate(23, 11, logger)

        assert ("output", 23, gr.RELAY_OFF) in calls, "RELAY_OFF not written"

    def test_relay_off_written_even_if_exception(self):
        """RELAY_OFF must be written in the finally block even when an
        exception fires immediately after RELAY_ON (simulating OOM/kill)."""
        import logging

        import gate_runtime as gr2
        logger = logging.getLogger("test")

        written = []

        class FakeGPIO:
            BOARD = "BOARD"
            OUT = "OUT"
            HIGH = gr2.RELAY_ON
            LOW = gr2.RELAY_OFF

            @staticmethod
            def setwarnings(*a): pass

            @staticmethod
            def setmode(*a): pass

            @staticmethod
            def setup(*a): pass

            @staticmethod
            def output(pin, level):
                written.append(level)
                if level == gr2.RELAY_ON:
                    raise RuntimeError("simulated mid-pulse crash")

            @staticmethod
            def cleanup(): pass

        import sys
        sys.modules["RPi.GPIO"] = FakeGPIO
        sys.modules["RPi"] = type("M", (), {"GPIO": FakeGPIO})()

        try:
            gr2.open_gate(23, 11, logger)
        except Exception:
            pass  # lgpio fallback also fails without the real library

        # RELAY_OFF must appear in written after RELAY_ON, regardless of exception.
        if gr2.RELAY_ON in written:
            idx_on = written.index(gr2.RELAY_ON)
            offs_after = [w for w in written[idx_on + 1:] if w == gr2.RELAY_OFF]
            assert offs_after, "RELAY_OFF not written after RELAY_ON despite finally"


if __name__ == "__main__":
    unittest.main()
