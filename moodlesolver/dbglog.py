import logging
import os
import sys
import time

LOG = logging.getLogger('moodlesolver')
_handler = None


def enable_debug(logfile=None, console=True):
    global _handler
    LOG.setLevel(logging.DEBUG)
    fmt = logging.Formatter(
        '%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s',
        datefmt='%H:%M:%S')
    if logfile:
        h = logging.FileHandler(logfile, encoding='utf-8')
        h.setFormatter(fmt)
        LOG.addHandler(h)
    if console and _handler is None:
        _handler = logging.StreamHandler(sys.stderr)
        _handler.setFormatter(fmt)
        LOG.addHandler(_handler)
    if os.environ.get('MOODLESOLVER_DEBUG'):
        LOG.debug('debug enabled (env)')
    return LOG


def dbg(msg):
    LOG.debug(msg)


def info(msg):
    LOG.info(msg)


def err(msg):
    LOG.error(msg)
