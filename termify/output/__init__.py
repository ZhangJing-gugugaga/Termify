from termify.engine import FrameSequence


def render(sequence: FrameSequence, format: str,
           audio_b64: str | None = None, audio_mime: str | None = None) -> str:
    """Render a FrameSequence into a bundled output string.

    format: "python" or "html". Raises ValueError otherwise.
    ``audio_b64``/``audio_mime`` (HTML only) embed a background audio track
    as a data URI so the exported player plays with sound.
    """
    if format == "python":
        from termify.output.python import render as _render
        return _render(sequence)
    if format == "html":
        from termify.output.html import render as _render
        return _render(sequence, audio_b64=audio_b64, audio_mime=audio_mime)
    raise ValueError(f"Unknown format: {format!r} (expected 'python' or 'html')")
