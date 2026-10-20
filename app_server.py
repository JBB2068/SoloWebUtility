#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Legacy SoloLink TCP App Server
Bootstraps the 3DR Solo legacy app server socket loop.
Compatible with Python 2.7 and Python 3.x.
"""

import sys
import optparse
from modules.legacy_server import (
    load_app_server_config,
    setup_sololink_logging,
    start_legacy_server,
    LegacyTcpServerThread,
    DEFAULT_SOLO_CONF
)


def main():
    parser = optparse.OptionParser("app_server [options]")
    parser.add_option("-c", "--conf", dest="conf", default=DEFAULT_SOLO_CONF,
                      help="Configuration file path (%default)")
    parser.add_option("-p", "--port", dest="port", type="int", default=None,
                      help="Override listen port")
    parser.add_option("-d", "--daemon", dest="daemon", action="store_true", default=False,
                      help="Run in isolated background daemon thread")

    (opts, args) = parser.parse_args()

    setup_sololink_logging(opts.conf)

    if opts.daemon:
        server = start_legacy_server(port=opts.port)
        print("SoloLink app server running in background on port %d" % server.port)
    else:
        server = LegacyTcpServerThread(port=opts.port, conf_path=opts.conf)
        server.run()


if __name__ == "__main__":
    main()
