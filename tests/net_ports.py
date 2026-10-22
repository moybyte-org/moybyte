"""A TCP port for a host test to serve on, that nothing else on the machine
can take before the host binds it.

Binding port 0 and closing the socket hands back a port from the ephemeral
range, which is free only until the next connect() on the machine: with a
dozen xdist workers and their clients, one came back "port busy" by the time
the kernel webhost bound it. Here the socket that found the port stays open,
bound with SO_REUSEADDR and never listening, for the rest of the process. The
kernel hands a bound port to no autobind or connect, and the webhost's own
SO_REUSEADDR bind and listen (native/moy_net/moy_net_port.c's
moy_http_listen) beside it are allowed, since nothing listens there.
"""

import socket

_HELD = []


def free_port():
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", 0))
    _HELD.append(s)
    return s.getsockname()[1]
