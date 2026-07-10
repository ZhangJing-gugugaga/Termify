# Termify

> 万物皆可终端 —— 把任何视频、GIF、图片转换成终端可播放的动画

## What is this?

Termify is a web-based tool that converts any GIF, image (and in future, video) into terminal-playable animations. Upload a file, pick a rendering style, download a `.py` script or `.html` page that plays the animation.

## Features (MVP)

- Upload GIF / PNG / JPG
- 5 rendering styles:
  - Classic ASCII (`@#%*+=-:.`)
  - Unicode blocks with TrueColor (`█▀▄`)
  - Braille dots (`⠁⠂⠄⡀`)
  - Geometric shapes (`■□▪▫`)
  - Minimal binary (`█ `)
- 2 output formats:
  - Python terminal player script
  - Self-contained HTML page
- Live preview with all styles side-by-side

## Tech Stack

- Backend: Python 3.10+, Flask, Pillow
- Frontend: Vanilla HTML/CSS/JS (no framework)
- No database, no user accounts, download-and-go

## Development

```bash
# Install dependencies
pip install flask pillow

# Run server
python app.py
```

## License

MIT
