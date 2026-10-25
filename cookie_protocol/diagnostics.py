"""Stage-only diagnostics. No caller/upstream values are logged."""
def stage(name):
    from gemini_webapi.request_diagnostics import emit
    emit(name, transport='cookie', protocol='process-session')
