import logging

from pythonjsonlogger.json import JsonFormatter

_MARKER_ATTR = "_cbks_json_handler"


def setup_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger()
    # Idempotent: mehrfacher Aufruf (Lifespan + CLI + Tests) darf keine
    # doppelten Handler anhaengen.
    if any(getattr(h, _MARKER_ATTR, False) for h in root.handlers):
        return
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    setattr(handler, _MARKER_ATTR, True)
    root.addHandler(handler)
    root.setLevel(level)
