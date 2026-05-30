import logging
from logging.config import dictConfig


class StructuredLogDefaultsFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        event = getattr(record, "event", None)
        if event in (None, ""):
            event = "generic_log_event"
            record.event = event
        if getattr(record, "action_type", None) in (None, ""):
            record.action_type = str(event)
        if not hasattr(record, "request_id"):
            record.request_id = None
        return True


def configure_logging(log_level: str) -> None:
    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "filters": {
                "structured_defaults": {
                    "()": "app.core.logging.StructuredLogDefaultsFilter",
                }
            },
            "formatters": {
                "json": {
                    "()": "pythonjsonlogger.json.JsonFormatter",
                    "fmt": (
                        "%(asctime)s %(levelname)s %(name)s %(message)s "
                        "%(event)s %(action_type)s %(request_id)s"
                    ),
                }
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "json",
                    "filters": ["structured_defaults"],
                }
            },
            "root": {
                "handlers": ["console"],
                "level": log_level.upper(),
            },
        }
    )
    logging.getLogger("uvicorn.access").setLevel(log_level.upper())
