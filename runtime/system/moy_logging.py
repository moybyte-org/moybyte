"""`logging` for a Python app, micropython-lib's face over the OS log
(native/moy_kernel/moy_log.h): a record goes into the ring under the app's
process, at the syslog severity of its level (CRITICAL crit, ERROR err,
WARNING warning, INFO info, DEBUG debug), and to serial as
`LEVEL:name:message`.

    import logging
    log = logging.getLogger("score")
    log.info("level %d", n)

On a VM with no `logging` of its own this module IS `logging`
(`os_log.install_logging`); CPython keeps its standard library's.

The root logger's level is WARNING until `basicConfig(level=...)` sets it;
a logger with no level of its own takes its parent's (the root). A call
below the level costs a comparison: no record, no formatting, no object.
Handlers and formatters are not kept: the ring is the one sink.
"""

try:
    import moy_log as _L
except ImportError:  # pragma: no cover - an image without the kernel
    _L = None

CRITICAL = 50
ERROR = 40
WARNING = 30
INFO = 20
DEBUG = 10
NOTSET = 0

_NAMES = {CRITICAL: "CRITICAL", ERROR: "ERROR", WARNING: "WARNING",
          INFO: "INFO", DEBUG: "DEBUG", NOTSET: "NOTSET"}


def _syslog(level):
    if level >= CRITICAL:
        return 2
    if level >= ERROR:
        return 3
    if level >= WARNING:
        return 4
    if level >= INFO:
        return 6
    return 7


def addLevelName(level, name):
    _NAMES[level] = name


def getLevelName(level):
    return _NAMES.get(level, "Level %d" % level)


class Logger:

    def __init__(self, name, level=NOTSET):
        self.name = name
        self.level = level
        self.parent = None
        self._tag = name + ":"

    def setLevel(self, level):
        self.level = level

    def getEffectiveLevel(self):
        lg = self
        while lg is not None:
            if lg.level != NOTSET:
                return lg.level
            lg = lg.parent
        return NOTSET

    def isEnabledFor(self, level):
        return level >= self.getEffectiveLevel()

    def _emit(self, level, msg, args):
        if args:
            msg = msg % args
        elif not isinstance(msg, str):
            msg = str(msg)
        try:
            print(_NAMES.get(level, "LEVEL"), self.name, msg, sep=":")
        except Exception:  # noqa: BLE001 -- a log call never fails its caller
            pass
        if _L is not None:
            if self is root:
                _L.write(_syslog(level), None, msg)
            else:
                _L.write(_syslog(level), None, self._tag, msg)

    def log(self, level, msg, *args):
        if self.isEnabledFor(level):
            self._emit(level, msg, args)

    def debug(self, msg, *args):
        if self.isEnabledFor(DEBUG):
            self._emit(DEBUG, msg, args)

    def info(self, msg, *args):
        if self.isEnabledFor(INFO):
            self._emit(INFO, msg, args)

    def warning(self, msg, *args):
        if self.isEnabledFor(WARNING):
            self._emit(WARNING, msg, args)

    def error(self, msg, *args):
        if self.isEnabledFor(ERROR):
            self._emit(ERROR, msg, args)

    def critical(self, msg, *args):
        if self.isEnabledFor(CRITICAL):
            self._emit(CRITICAL, msg, args)

    def exception(self, msg, *args, exc_info=True):
        """An error with the exception being handled's traceback (pass it as
        `exc_info`: MicroPython has no sys.exc_info)."""
        if not self.isEnabledFor(ERROR):
            return
        self._emit(ERROR, msg, args)
        if isinstance(exc_info, BaseException):
            import os_log
            text = os_log.traceback_text(exc_info)
            if text and _L is not None:
                _L.write(_syslog(ERROR), None, text)


root = Logger("root", WARNING)
_loggers = {}


def getLogger(name=None):
    if name is None or name == "root":
        return root
    lg = _loggers.get(name)
    if lg is None:
        lg = _loggers[name] = Logger(name)
        lg.parent = root
    return lg


def basicConfig(level=None, **kw):
    """The root's level; the other keywords of micropython-lib's are taken
    and do nothing (the ring is the one sink)."""
    if level is not None:
        root.setLevel(level)


def log(level, msg, *args):
    root.log(level, msg, *args)


def debug(msg, *args):
    root.log(DEBUG, msg, *args)


def info(msg, *args):
    root.log(INFO, msg, *args)


def warning(msg, *args):
    root.log(WARNING, msg, *args)


def error(msg, *args):
    root.log(ERROR, msg, *args)


def critical(msg, *args):
    root.log(CRITICAL, msg, *args)


def exception(msg, *args, exc_info=True):
    root.exception(msg, *args, exc_info=exc_info)


def shutdown():
    pass
