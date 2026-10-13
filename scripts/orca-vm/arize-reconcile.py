"""Run Arize's reconciler, recording export acknowledgments without payloads."""

import importlib
import json
import os
import time
from pathlib import Path

from core import common

original_post = common._post_otlp


def observed_post(url, body, headers, label):
    accepted = original_post(url, body, headers, label)
    log = Path.home() / ".arize/harness/logs/export-receipts.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "w") as out:
        out.write(json.dumps({"time": time.time(), "backend": label, "accepted": accepted}) + "\n")
    return accepted


common._post_otlp = observed_post
importlib.import_module("tracing.opencode.hooks.handlers").main()
