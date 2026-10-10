# Third-party components

inkhub's own code is MIT-licensed. The following bundled files are not part of it and are
distributed under their own licenses.

| File | Project | License | Source |
| --- | --- | --- | --- |
| `kindle/kindleclock/bin/fbink` | FBInk by NiLuJe, legacy Kindle build taken from KOReader v2026.07.1 (`koreader-kindle-legacy`) | GPL-3.0-or-later | https://github.com/NiLuJe/FBInk (commit `886f25f13368859ad8a899b88d04c26e19cda32e`, as pinned by KOReader), https://github.com/koreader/koreader/releases/tag/v2026.07.1 |
| `kindle/kindleclock/bin/luajit` | LuaJIT, legacy Kindle build taken from KOReader v2026.07.1 | MIT | https://luajit.org, https://github.com/koreader/koreader-base |
| `kindle/kindleclock/fonts/OpenSans-*.ttf`, `hub/fonts/OpenSans-*.ttf` | Open Sans by Steve Matteson | Apache-2.0 | https://github.com/googlefonts/opensans |

FBInk is distributed unmodified, as a separate program that inkhub only runs; its complete
corresponding source code is available at the links above. inkhub's MIT license does not
apply to it.
