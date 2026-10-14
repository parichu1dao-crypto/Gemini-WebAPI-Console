"""Task-local, opt-in diagnostics. Never record prompts or credentials."""
from contextvars import ContextVar

diagnostic_sink = ContextVar("gemini_diagnostic_sink", default=None)


def emit(stage, **fields):
    sink = diagnostic_sink.get()
    if sink is not None:
        try:
            sink({"stage": stage, **fields})
        except Exception:
            # Observability must not turn a successful generation into a failure.
            pass
