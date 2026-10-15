# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is part of < https://github.com/TeamUltroid/Ultroid >
# PLease read the GNU Affero General Public License in
# <https://www.gnu.org/licenses/agpl-3.0.html>.
"""
The group-call commands and the voice-chat music player live in
:mod:`plugins.vcmusic`.

They used to be split across this file and the player module, which meant two
command sets registered against the same PyTgCalls engine. They are now
defined once, in one place, so there is a single source of truth for which
callbacks exist.

See ``plugins/vcmusic.py`` for the command list:

• ``startvc`` / ``stopvc`` / ``vctitle`` / ``vcinvite`` — group-call plumbing
• ``vplay`` / ``vskip`` / ``vpause`` / ``vresume`` — transport
• ``vqueue`` / ``vnow`` / ``vclear`` / ``vloop`` — queue control
"""
