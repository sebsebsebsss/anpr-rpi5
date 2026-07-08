# Third-party licences

This project links against and ships with the following third-party software.

## System packages (installed by Ansible)

| Package | Licence | Notes |
|---------|---------|-------|
| **OpenALPR** (`alprd` daemon) | AGPLv3 | https://github.com/openalpr/openalpr — the AGPL network-use clause applies if you make the web UI accessible to users over a network |
| **Tesseract OCR** | Apache-2.0 | https://github.com/tesseract-ocr/tesseract |
| **Leptonica** | BSD-2-Clause | https://github.com/DanBloomberg/leptonica |
| **OpenCV** | Apache-2.0 | https://opencv.org/ |
| **ffmpeg** | LGPL-2.1 or GPL-2.0 (depends on build flags) | https://ffmpeg.org/legal.html — Debian's `ffmpeg` package is GPL |
| **beanstalkd** | MIT | https://github.com/beanstalkd/beanstalkd |

## Python packages (installed into venv by Ansible)

| Package | Licence |
|---------|---------|
| **Flask** | BSD-3-Clause |
| **Werkzeug** | BSD-3-Clause |
| **Jinja2** | BSD-3-Clause |
| **MarkupSafe** | BSD-3-Clause |
| **click** | BSD-3-Clause |
| **itsdangerous** | BSD-3-Clause |
| **waitress** | ZPL-2.1 |
| **greenstalk** | MIT |
| **requests** | Apache-2.0 |
| **certifi** | MPL-2.0 |
| **urllib3** | MIT |
| **charset-normalizer** | MIT |
| **idna** | BSD-3-Clause |

## Fonts

| Font | Licence |
|------|---------|
| **Space Grotesk** (woff2 in `files/web/static/fonts/`) | SIL Open Font Licence 1.1 — https://fonts.google.com/specimen/Space+Grotesk/about |

## OpenALPR and the AGPL

OpenALPR is licenced under the GNU Affero General Public Licence v3 (AGPLv3).
This means that if you allow users to interact with the ANPR service over a
network, you must make the corresponding source code of your combined work
available to them. For private home or small-site deployments where access is
restricted to trusted users on a private LAN, typical AGPL obligations are
minimal — consult a lawyer if you intend to deploy more broadly.
