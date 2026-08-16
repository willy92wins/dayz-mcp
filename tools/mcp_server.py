#!/usr/bin/env python3
from __future__ import annotations

from dayz_mcp.loopback import (
    CLIENT_COMMANDS,
    MAX_QUEUE,
    SERVER_COMMANDS,
    VALID_PEERS,
    WHITELISTED_COMMANDS,
    Handler,
    LoopbackServer,
    ServerState,
    create_http_server,
    main,
    peer_for_command,
    read_key,
)


if __name__ == "__main__":
    raise SystemExit(main())
