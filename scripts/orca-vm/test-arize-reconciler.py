"""Exercise the actual upstream reconciler with converted V2 fixture data, offline."""

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, sys.argv[1])
payload = json.loads(Path(sys.argv[2]).read_text())
with tempfile.TemporaryDirectory(prefix="mercor-tracing-test-") as temp:
    os.environ["ARIZE_LOG_FILE"] = str(Path(temp) / "arize.log")
    os.environ["ARIZE_LOG_PROMPTS"] = "false"
    os.environ["ARIZE_LOG_TOOL_DETAILS"] = "false"
    os.environ["ARIZE_LOG_TOOL_CONTENT"] = "false"
    # Redirect all upstream filesystem state into the test directory.
    with patch.object(Path, "home", return_value=Path(temp)):
        from tracing.opencode.hooks import handlers

        spans = []
        with patch.object(handlers, "_send_span_async", side_effect=spans.append):
            handlers._handle_close(payload)
            count = len(spans)
            handlers._handle_close(payload)
        assert len(spans) == count, "Repeated snapshot exported duplicate spans"
        text = json.dumps(spans)
        assert count == 3, f"Expected Turn, LLM, and TOOL spans, received {count}"
        assert "large-fixture" in text and "llm.token_count.prompt" in text
        assert "private fixture prompt" not in text and "fixture answer" not in text
        assert "printf fixture" not in text, "Tool argument redaction failed"
        print("Upstream Arize reconciler: 3 spans, deduplication and content redaction passed")
