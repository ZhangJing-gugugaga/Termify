from termify.engine import FrameSequence


def render(sequence: FrameSequence, format: str) -> str:
    """Render a FrameSequence into a bundled output string.

    format: "python" or "html". Raises ValueError otherwise.
    """
    if format == "python":
        from termify.output.python import render as _render
    elif format == "html":
        from termify.output.html import render as _render
    else:
        raise ValueError(f"Unknown format: {format!r} (expected 'python' or 'html')")
    return _render(sequence)
