"""coletar — a portable AI workspace.

Memory is a first-class typed object with provenance and confidence, held in one
canonical graph. Two operating modes sit on top of it (SCOPE §3):

  * Live Sync      — the store stays authoritative; every surface queries it in
                     real time over MCP or the local proxy.
  * True Migration — a directional, point-in-time compile of canonical objects
                     into a destination's *native* containers, with a Migration
                     Manifest and a Continuity Score.
"""

__version__ = "0.1.0"
