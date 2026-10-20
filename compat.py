#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
compat.py - tiny Python 2.7 / 3 compatibility helpers (standard library only).

The Solo runs stock Python 2.7, which lacks subprocess.run, os.makedirs(exist_ok=),
and open(encoding=). The rest of the code base calls these helpers instead.
"""

import io
import os
import sys
import errno
import threading
import subprocess

PY3 = sys.version_info[0] >= 3


class TimeoutExpired(Exception):
    pass


class CompletedProcess(object):
    def __init__(self, args, returncode, stdout=None, stderr=None):
        self.args = args
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def makedirs(path):
    """os.makedirs(path, exist_ok=True) for Python 2.7."""
    try:
        os.makedirs(path)
    except OSError as e:
        if e.errno != errno.EEXIST or not os.path.isdir(path):
            raise


def open_text(path, mode="r", encoding="utf-8"):
    """open(path, mode, encoding=...) for Python 2.7 (plain byte-string file objects)."""
    if PY3:
        return open(path, mode, encoding=encoding)
    return open(path, mode)


def _to_text(data):
    if data is None:
        return data
    if PY3 and isinstance(data, bytes):
        return data.decode("utf-8", "replace")
    return data


def run(args, capture_output=False, text=False, timeout=None, input=None,
        shell=False, stdout=None, stderr=None, **kwargs):
    """Subset of Python 3's subprocess.run that also works on Python 2.7."""
    if capture_output:
        stdout = subprocess.PIPE
        stderr = subprocess.PIPE
    stdin = subprocess.PIPE if input is not None else None
    proc = subprocess.Popen(args, stdin=stdin, stdout=stdout, stderr=stderr, shell=shell, **kwargs)

    timed_out = [False]

    def _kill():
        timed_out[0] = True
        try:
            proc.kill()
        except Exception:
            pass

    timer = None
    if timeout is not None:
        timer = threading.Timer(timeout, _kill)
        timer.daemon = True
        timer.start()
    try:
        out, err = proc.communicate(input)
    finally:
        if timer is not None:
            timer.cancel()
    if timed_out[0]:
        raise TimeoutExpired("Command %r timed out after %s seconds" % (args, timeout))
    if text:
        out = _to_text(out)
        err = _to_text(err)
    return CompletedProcess(args, proc.returncode, out, err)
