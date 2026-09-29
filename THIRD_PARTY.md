# Third-party licences

This project's own code uses [The Unlicense](LICENSE). The bundled font and
the main third-party components installed by Ansible retain their own licences.

## System packages (installed by Ansible)

| Package | Licence | Notes |
|---------|---------|-------|
| **OpenALPR** (`alprd` daemon) | AGPLv3 | [Upstream licence](https://github.com/openalpr/openalpr/blob/master/LICENSE); runs as a separate service |
| **Tesseract OCR** | Apache-2.0 | https://github.com/tesseract-ocr/tesseract |
| **Leptonica** | BSD-2-Clause | https://github.com/DanBloomberg/leptonica |
| **OpenCV** | Apache-2.0 | https://opencv.org/ |
| **ffmpeg** | LGPL-2.1 or GPL-2.0 (depends on build flags) | https://ffmpeg.org/legal.html — Debian's `ffmpeg` package is GPL |
| **beanstalkd** | MIT | https://github.com/beanstalkd/beanstalkd |

## Python packages (installed into venv by Ansible)

| Package | Licence |
|---------|---------|
| **Flask** | BSD-3-Clause |
| **blinker** | MIT |
| **Werkzeug** | BSD-3-Clause |
| **Jinja2** | BSD-3-Clause |
| **MarkupSafe** | BSD-3-Clause |
| **click** | BSD-3-Clause |
| **itsdangerous** | BSD-3-Clause |
| **waitress** | ZPL-2.1 |
| **greenstalk** | MIT |
| **requests** | Apache-2.0 |
| **Pillow** | [MIT-CMU](https://github.com/python-pillow/Pillow/blob/12.3.0/LICENSE) |
| **certifi** | MPL-2.0 |
| **urllib3** | MIT |
| **charset-normalizer** | MIT |
| **idna** | BSD-3-Clause |

## Optional certificate client

Automatic HTTPS installs **acme.sh 3.1.6**, released under
[GPLv3](https://github.com/acmesh-official/acme.sh/blob/807da6498377ee5e0cf43a78091f46f12dc59a89/LICENSE.md).
Ansible downloads the pinned source archive with a verified checksum and keeps
its licence alongside the installed client. Manual certificate mode does not
install acme.sh.

## Fonts

| Font | Licence |
|------|---------|
| **Space Grotesk** (woff2 in `files/web/static/fonts/`) | SIL Open Font Licence 1.1 — [bundled copyright and licence](files/web/static/fonts/OFL.txt), [upstream project](https://github.com/floriankarsten/space-grotesk) |

## OpenALPR and the AGPL

OpenALPR's [AGPLv3 licence](https://github.com/openalpr/openalpr/blob/master/LICENSE)
sets its redistribution conditions and, in section 13, conditions for remote
interaction with modified versions. Refer to that upstream text when modifying
or redistributing OpenALPR. This repository's licence does not replace the
licences of installed dependencies or bundled assets.
