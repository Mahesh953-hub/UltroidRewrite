# SarahUB — standalone web entry (W2-A rewrite).
#
# Thin shim around pyUltroid.webserver: token-gated setup UI + the legacy
# /status health endpoint (exact shape {"message": "running"}), plus the HF
# keep-alive pinger, now fully env-configured (SPACE_URL / HF_TOKEN /
# KEEP_ALIVE / KEEP_ALIVE_MIN_SECONDS / KEEP_ALIVE_MAX_SECONDS — see
# pyUltroid/webserver/keepalive.py).
#
# Under start.sh this file is the container's foreground process while
# `python3 -m pyUltroid` runs in the background. Both may race to own the
# setup port (env PORT, default 7860): whoever binds first serves; this
# shim keeps retrying the bind so it stays alive, and the post-setup bot
# spawn consults the bootstrap waiter's PID marker so exactly one bot
# starts (see pyUltroid/webserver/README.md for the full model).

import os
import time

from pyUltroid.webserver.app import SetupApplication
from pyUltroid.webserver.bootstrap import BOOTSTRAP
from pyUltroid.webserver.keepalive import KeepAliveConfig, KeepAlivePinger
from pyUltroid.webserver.runner import PortProbe, serve_forever

# `app` stays module-level so `uvicorn server:app` keeps working.
setup = SetupApplication(standalone=True)
app = setup.build()


def main() -> None:
    KeepAlivePinger(KeepAliveConfig.from_env()).start()
    host, port = BOOTSTRAP.host_port()
    while not PortProbe().bindable(host, port):
        print(
            f"server: {host}:{port} is owned by another process "
            "(likely the pyUltroid bootstrap setup server) — retrying in 30s."
        )
        time.sleep(30)
    token = setup.state.token
    if not os.environ.get("SETUP_TOKEN"):
        display = "127.0.0.1" if host == "0.0.0.0" else host
        print("=" * 64)
        print("  SarahUB setup server")
        print(f"  Open:  http://{display}:{port}/?token={token}")
        print("=" * 64)
    serve_forever(app, host, port)


if __name__ == "__main__":
    main()
