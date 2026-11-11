"""Real host-memory supervisor for antihack calibration.

Soft monitor thresholds only — not OS/JobObject limits. The adapters in
``real_adapters`` read CIM/GPI and call the public FastMCP ``dayz_test_stop``.
"""

__version__ = "0.2.0"
