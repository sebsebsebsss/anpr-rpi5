"""The optional queue helper must not send anything without an explicit flag."""

import importlib.util
import json
import sys
from pathlib import Path


def _helper():
    spec = importlib.util.spec_from_file_location("synthetic_helper", Path(__file__).with_name("synthetic_job.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_preview_does_not_connect_to_queue(monkeypatch, capsys):
    helper = _helper()

    def unexpected_connection(*args, **kwargs):
        raise AssertionError("A preview must not connect to the worker queue")

    monkeypatch.setattr(helper.greenstalk, "Client", unexpected_connection, raising=False)
    monkeypatch.setattr(sys, "argv", ["synthetic_job.py", "--plate", "DEMO123"])
    helper.main()
    job = json.loads(capsys.readouterr().out)
    assert job["results"][0]["candidates"][0]["plate"] == "DEMO123"


def test_explicit_enqueue_sends_only_to_selected_queue(monkeypatch):
    helper = _helper()
    sent = []

    class Queue:
        def __init__(self, address):
            assert address == ("127.0.0.1", 11300)

        def use(self, tube):
            assert tube == "isolated-test"

        def put(self, payload):
            sent.append(json.loads(payload))

    monkeypatch.setattr(helper.greenstalk, "Client", Queue, raising=False)
    monkeypatch.setattr(sys, "argv", ["synthetic_job.py", "--enqueue", "--tube", "isolated-test"])
    helper.main()
    assert len(sent) == 1
