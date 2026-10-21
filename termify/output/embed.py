"""One-line embed snippet generator (P2).

Every other exporter hands the user a whole page. This one hands them two
lines they can paste into a README or a blog post: a <termify-play> custom
element plus a tiny runtime that fetches the frames JSON from the host and
animates them. The point is distribution -- a shared page converts nobody,
a work embedded in someone else's post converts whoever scrolls past it.
"""

from __future__ import annotations

import html
import json

from termify.engine import FrameSequence


# The runtime is deliberately dependency-free and idempotent: several embeds
# on one page share a single <termify-play> definition (the guard below), so
# a blog listing 10 works registers the element once.
_RUNTIME = """(function () {
  if (window.customElements && customElements.get('termify-play')) return;
  var CSS = 'display:block;background:#0a0e14;color:#c9d1d9;border-radius:8px;' +
    'padding:12px 16px;overflow:auto;margin:0;font:12px/1.3 ui-monospace,' +
    'SFMono-Regular,Menlo,Consolas,monospace;white-space:pre;';
  var TermifyPlay = (window.customElements ? function () {
    return Reflect.construct(HTMLElement, [], TermifyPlay);
  } : function () { return document.createElement('pre'); });
  if (window.customElements) {
    TermifyPlay.prototype = Object.create(HTMLElement.prototype);
    TermifyPlay.prototype.constructor = TermifyPlay;
    Object.setPrototypeOf(TermifyPlay, HTMLElement);
    TermifyPlay.prototype.connectedCallback = function () { mount(this); };
  }
  function mount(host) {
    var src = host.getAttribute('src');
    if (!src || host.getAttribute('data-termify-bound')) return;
    host.setAttribute('data-termify-bound', '1');
    var pre = document.createElement('pre');
    pre.setAttribute('style', CSS);
    pre.textContent = 'loading...';
    host.appendChild(pre);
    fetch(src, { credentials: 'omit' })
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
      .then(function (data) {
        var frames = data.frames || [];
        if (!frames.length) { pre.textContent = '(empty)'; return; }
        var i = 0, timer = null, alive = true;
        var step = Math.max(20, (data.interval || 0.1) * 1000);
        function tick() {
          if (!alive) return;
          pre.textContent = frames[i];
          i = (i + 1) % frames.length;
        }
        tick();
        // Honour prefers-reduced-motion: show one frame and stop. An
        // animation the visitor cannot switch off is an accessibility bug,
        // and the whole point is that people keep the embed.
        var reduce = window.matchMedia &&
          window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        if (reduce) return;
        var start = function () {
          if (timer === null) timer = setInterval(tick, step);
        };
        var stop = function () {
          if (timer !== null) { clearInterval(timer); timer = null; }
        };
        // Off-screen embeds must not burn a timer each; pause when hidden.
        if (window.IntersectionObserver) {
          new IntersectionObserver(function (entries) {
            if (entries[0].isIntersecting) start(); else stop();
          }, { threshold: 0 }).observe(host);
        } else {
          start();
        }
        document.addEventListener('visibilitychange', function () {
          if (document.hidden) stop(); else start();
        });
      })
      .catch(function (e) {
        pre.textContent = 'termify: ' + e.message;
      });
  }
  if (window.customElements) {
    customElements.define('termify-play', TermifyPlay);
  } else {
    // Pre-customElements browsers: upgrade every placeholder <pre> directly.
    Array.prototype.forEach.call(
      document.querySelectorAll('pre[data-termify-play]'), mount);
  }
})();
"""


def frames_payload(sequence: FrameSequence) -> str:
    """Frames JSON for the runtime: plain text rows, no ANSI.

    ANSI escapes cannot be shipped here the way the full player does it --
    the embed targets README renderers and CMS sanitizers that drop or
    mangle escape sequences. Colour is therefore the host page's job
    (:host { color }) and this stays plain text.
    """
    def strip_ansi(text: str) -> str:
        out = []
        i = 0
        n = len(text)
        while i < n:
            if text[i] == "":
                j = text.find("m", i)
                i = n if j < 0 else j + 1
                continue
            out.append(text[i])
            i += 1
        return "".join(out)

    frames = ["\n".join(strip_ansi(line) for line in frame)
              for frame in sequence.lines_per_frame]
    return json.dumps({
        "w": sequence.width,
        "h": sequence.height,
        "interval": sequence.interval,
        "charset": sequence.charset,
        "frames": frames,
    }, ensure_ascii=False, separators=(",", ":"))


def render(sequence: FrameSequence, base_url: str = "") -> str:
    """Inline snippet: runtime + data URI, works in any HTML page."""
    payload = frames_payload(sequence)
    uri = "data:application/json;charset=utf-8," + _encode_uri(payload)
    return _snippet(uri, sequence, inline=True)


def render_reference(sequence: FrameSequence, src_url: str) -> str:
    """Two-line snippet pointing at a hosted frames URL (for READMEs)."""
    return _snippet(html.escape(src_url, quote=True), sequence, inline=False)


def _encode_uri(text: str) -> str:
    from urllib.parse import quote
    return quote(text, safe="")


def _snippet(src: str, sequence: FrameSequence, inline: bool) -> str:
    title = f"Termify · {sequence.charset} · {sequence.width}x{sequence.height}"
    parts = [
        f"<!-- {title} -->",
        f'<script>{_RUNTIME}</script>',
        f'<termify-play src="{src}"></termify-play>',
    ]
    if inline:
        parts.insert(0, "<!-- self-contained: frames baked in, no server needed -->")
    return "\n".join(parts) + "\n"
