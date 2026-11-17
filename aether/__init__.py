"""AETHER package. On import, HTTPS certificate checks trust the operating system's store when the
optional `truststore` package is installed (see aether/tls.py); verification is never turned off."""
from aether import tls as _tls

_tls.use_system_trust_store()
