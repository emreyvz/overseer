"""Best-effort Windows thread priority for the CURRENT thread (a no-op elsewhere).

Keeps the 30fps display path (capture / encode / stream) scheduled AHEAD of the heavy background
analysis, roster-harvest and classifier threads, so the live feed stays smooth under load. The
priority is a hint the OS scheduler uses when threads contend for CPU cores and for the (GIL-released)
C-extension work inside numpy / OpenCV / torch.
"""
from __future__ import annotations

# Windows THREAD_PRIORITY_* values
ABOVE_NORMAL = 1
BELOW_NORMAL = -1
HIGHEST = 2
LOWEST = -2


def set_current_thread_priority(level: int) -> None:
    try:
        import ctypes
        k = ctypes.windll.kernel32
        k.SetThreadPriority(k.GetCurrentThread(), int(level))
    except Exception:  # noqa: BLE001 - non-Windows / restricted; leave scheduling to the OS
        pass
