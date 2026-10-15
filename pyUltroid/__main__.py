# Ultroid - UserBot
# Copyright (C) 2021-2025 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# Please read the GNU Affero General Public License in
# <https://github.com/TeamUltroid/pyUltroid/blob/main/LICENSE>.

"""The process entry: run the union, deploy, serve.

Three lines of lifecycle, nothing else. Importing ``pyUltroid`` boots
nothing (the package shell); everything the process does is the core
union's ``run`` -> ``deploy`` -> ``serve_forever`` flow.
"""

from .ultroid import core

if __name__ == "__main__":
    core.run()
    core.deploy()
    core.serve_forever()
