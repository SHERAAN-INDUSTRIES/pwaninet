"""
Compatibility shims for third-party library version mismatches.

Import and call the helpers here before exercising the affected code paths.
"""


def patch_av_metadata_errors() -> None:
    """
    Make ``av.open`` tolerate the removed ``metadata_errors`` keyword.

    faster-whisper (<= 1.2.1, the current latest) calls
    ``av.open(..., metadata_errors="ignore")`` in its audio decoder, but PyAV
    13+ removed that keyword argument, raising ``TypeError: open() got an
    unexpected keyword argument 'metadata_errors'``. PyAV < 13 has no wheels
    for Python 3.14, so we cannot simply downgrade. Wrapping ``av.open`` to
    drop the unsupported keyword restores decoding without forking the library.

    Safe to call multiple times (idempotent).
    """
    try:
        import av
    except ImportError:  # pragma: no cover - faster-whisper handles this path
        return

    original_open = av.open
    if getattr(original_open, "_pwanimate_metadata_errors_patched", False):
        return

    def open_compat(*args, **kwargs):
        kwargs.pop("metadata_errors", None)
        return original_open(*args, **kwargs)

    open_compat._pwanimate_metadata_errors_patched = True
    av.open = open_compat
