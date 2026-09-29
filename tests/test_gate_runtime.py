"""Exercise relay failure paths with fake backends; never access real GPIO."""

import json
import logging
import os
import subprocess
import sys
import types
from pathlib import Path

import gate_runtime as gr
import pytest


@pytest.fixture
def gpio_env(monkeypatch, tmp_path):
    monkeypatch.delenv("GATE_GPIO_BACKEND", raising=False)
    monkeypatch.setenv("GATE_GPIO_CHIP", "0")
    monkeypatch.setenv("GATE_GPIO_MIN_INTERVAL_SECONDS", "1.0")
    monkeypatch.setenv("GATE_GPIO_LOCK_PATH", str(tmp_path / "gpio.lock"))
    monkeypatch.setattr(gr.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(gr, "_logged_gpio_config", None)
    calls = []

    def gpio_write(handle, pin, level):
        calls.append(("write", pin, level))

    lgpio = types.SimpleNamespace(
        gpiochip_open=lambda chip: calls.append(("open", chip)) or 7,
        gpio_claim_output=lambda handle, pin, level: calls.append(("claim", pin, level)),
        gpio_write=gpio_write,
        gpiochip_close=lambda handle: calls.append(("close", handle)),
    )
    monkeypatch.setitem(sys.modules, "lgpio", lgpio)
    return calls, lgpio, logging.getLogger("gpio-test"), tmp_path / "gpio.lock"


def test_default_lgpio_uses_configured_chip_and_releases_relay(gpio_env, monkeypatch):
    calls, _, logger, _ = gpio_env
    monkeypatch.setenv("GATE_GPIO_CHIP", "4")
    assert gr.open_gate(23, 11, logger) is True
    assert calls == [("open", 4), ("claim", 11, 0), ("write", 11, 1), ("write", 11, 0), ("close", 7)]


@pytest.mark.parametrize("interruption", [OSError("pulse interrupted"), SystemExit("SIGTERM")])
def test_lgpio_always_deactivates_on_interrupted_sleep(gpio_env, monkeypatch, interruption):
    calls, _, logger, _ = gpio_env

    def interrupt(seconds):
        raise interruption

    monkeypatch.setattr(gr.time, "sleep", interrupt)
    expected = SystemExit if isinstance(interruption, SystemExit) else gr.GateActuationError
    with pytest.raises(expected) as caught:
        gr.open_gate(23, 11, logger)
    if isinstance(caught.value, gr.GateActuationError):
        assert caught.value.may_have_activated is True
    assert calls[-3:] == [("write", 11, 1), ("write", 11, 0), ("close", 7)]


@pytest.mark.parametrize("failed_level", [gr.RELAY_ON, gr.RELAY_OFF])
def test_lgpio_closes_even_when_an_output_write_fails(gpio_env, failed_level):
    calls, lgpio, logger, _ = gpio_env

    def failing_write(handle, pin, level):
        calls.append(("write", pin, level))
        if level == failed_level:
            raise OSError("output write failed")

    lgpio.gpio_write = failing_write
    with pytest.raises(gr.GateActuationError) as caught:
        gr.open_gate(23, 11, logger)
    assert caught.value.may_have_activated is True
    assert calls[-3:] == [("write", 11, 1), ("write", 11, 0), ("close", 7)]


def test_claim_failure_can_be_retried_without_cooldown(gpio_env):
    calls, lgpio, logger, _ = gpio_env
    original_claim = lgpio.gpio_claim_output

    def fail_claim(*args):
        raise OSError("GPIO busy")

    lgpio.gpio_claim_output = fail_claim
    with pytest.raises(gr.GateActuationError) as caught:
        gr.open_gate(23, 11, logger)
    assert caught.value.may_have_activated is False
    assert calls == [("open", 0), ("close", 7)]

    lgpio.gpio_claim_output = original_claim
    assert gr.open_gate(23, 11, logger) is True
    assert calls.count(("write", 11, 1)) == 1


def test_recent_success_coalesces_until_minimum_interval_expires(gpio_env, monkeypatch):
    calls, _, logger, _ = gpio_env
    monkeypatch.setattr(gr.time, "time", lambda: 1000.0)
    assert gr.open_gate(23, 11, logger) is True
    monkeypatch.setattr(gr.time, "time", lambda: 1000.75)
    assert gr.open_gate(23, 11, logger) is False
    monkeypatch.setattr(gr.time, "time", lambda: 1001.0)
    assert gr.open_gate(23, 11, logger) is True
    assert calls.count(("write", 11, 1)) == 2


def test_uncertain_pulse_is_not_reported_as_success_or_retried(gpio_env, monkeypatch):
    calls, _, logger, lock_path = gpio_env
    monkeypatch.setattr(gr.time, "time", lambda: 1000.0)

    def interrupt(seconds):
        raise OSError("interrupted")

    monkeypatch.setattr(gr.time, "sleep", interrupt)
    with pytest.raises(gr.GateActuationError):
        gr.open_gate(23, 11, logger)
    assert json.loads(lock_path.read_text())["completed"] is False
    monkeypatch.setattr(gr.time, "sleep", lambda seconds: None)
    with pytest.raises(gr.GateActuationError, match="uncertain outcome") as caught:
        gr.open_gate(23, 11, logger)
    assert caught.value.may_have_activated is True
    assert calls.count(("write", 11, 1)) == 1


@pytest.mark.parametrize("failed_level", [None, gr.RELAY_ON, gr.RELAY_OFF])
def test_explicit_legacy_backend_cleans_up_without_fallback(gpio_env, monkeypatch, failed_level):
    calls, _, logger, _ = gpio_env
    monkeypatch.setenv("GATE_GPIO_BACKEND", "rpi_gpio")

    def output(pin, level):
        calls.append(("output", pin, level))
        if level == failed_level:
            raise RuntimeError("legacy GPIO write failed")

    gpio = types.SimpleNamespace(
        BOARD="BOARD",
        OUT="OUT",
        setwarnings=lambda value: None,
        setmode=lambda mode: None,
        setup=lambda pin, mode, initial: calls.append(("setup", pin, initial)),
        output=output,
        cleanup=lambda pin: calls.append(("cleanup", pin)),
    )
    monkeypatch.setitem(sys.modules, "RPi", types.SimpleNamespace(GPIO=gpio))
    monkeypatch.setitem(sys.modules, "RPi.GPIO", gpio)
    if failed_level is None:
        assert gr.open_gate(23, 11, logger) is True
    else:
        with pytest.raises(gr.GateActuationError) as caught:
            gr.open_gate(23, 11, logger)
        assert caught.value.may_have_activated is True
    assert calls == [("setup", 23, 0), ("output", 23, 1), ("output", 23, 0), ("cleanup", 23)]


@pytest.mark.parametrize(
    "name,value",
    [
        ("GATE_GPIO_BACKEND", "unknown"),
        ("GATE_GPIO_CHIP", "bad"),
        ("GATE_GPIO_CHIP", "-1"),
        ("GATE_GPIO_MIN_INTERVAL_SECONDS", "nan"),
        ("GATE_GPIO_MIN_INTERVAL_SECONDS", "0"),
    ],
)
def test_invalid_config_does_not_touch_gpio(gpio_env, monkeypatch, name, value):
    calls, _, logger, _ = gpio_env
    monkeypatch.setenv(name, value)
    with pytest.raises(gr.GateActuationError) as caught:
        gr.open_gate(23, 11, logger)
    assert caught.value.may_have_activated is False
    assert calls == []


def test_separate_processes_share_lock_and_coalesce(tmp_path):
    # Both child interpreters replace lgpio before importing the application.
    # A delayed claim exposes the race if the shared file lock is removed.
    child = """
import logging, os, sys, time, types
def claim(*args):
    time.sleep(0.2)
def write(handle, pin, level):
    if level:
        with open(os.environ['GPIO_TEST_TRACE'], 'a') as trace:
            trace.write('ON\\n')
sys.modules['lgpio'] = types.SimpleNamespace(
    gpiochip_open=lambda chip: 7, gpio_claim_output=claim,
    gpio_write=write, gpiochip_close=lambda handle: None,
)
import gate_runtime
print('ready', flush=True)
sys.stdin.readline()
print(gate_runtime.open_gate(23, 11, logging.getLogger('child')), flush=True)
"""
    env = os.environ.copy()
    env.update(
        {
            "PYTHONPATH": str(Path(gr.__file__).parent),
            "PYTHONDONTWRITEBYTECODE": "1",
            "GATE_GPIO_BACKEND": "lgpio",
            "GATE_GPIO_CHIP": "0",
            "GATE_GPIO_LOCK_PATH": str(tmp_path / "shared.lock"),
            "GATE_GPIO_MIN_INTERVAL_SECONDS": "10",
            "GPIO_TEST_TRACE": str(tmp_path / "trace.txt"),
        }
    )
    children = [
        subprocess.Popen(
            [sys.executable, "-B", "-c", child],
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(2)
    ]
    try:
        for process in children:
            assert process.stdout.readline().strip() == "ready"
        for process in children:
            process.stdin.write("start\n")
            process.stdin.flush()
        outcomes = []
        for process in children:
            stdout, stderr = process.communicate(timeout=5)
            assert process.returncode == 0, stderr
            outcomes.append(stdout.strip())
        assert sorted(outcomes) == ["False", "True"]
        assert (tmp_path / "trace.txt").read_text() == "ON\n"
    finally:
        for process in children:
            if process.poll() is None:
                process.kill()
                process.wait()
