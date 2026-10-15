# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://www.github.com/TeamUltroid/Ultroid/blob/main/LICENSE/>.


from . import get_help

__doc__ = get_help("help_core")


import os

from pyUltroid._core.loader import confirm_unload
from pyUltroid.startup.loader import load_addons

from . import LOGS, eod, get_string, safeinstall, ultroid_cmd
from pyUltroid.fns import fns


@ultroid_cmd(pattern="install", fullsudo=True)
async def install(event):
    await safeinstall(event)


@ultroid_cmd(
    pattern=r"unload( (.*)|$)",
)
async def unload(event):
    shortname = event.pattern_match.group(1).strip()
    if not shortname:
        await event.eor(get_string("core_9"))
        return
    lsd = os.listdir("addons")
    zym = f"{shortname}.py"
    if zym in lsd:
        # Button-confirm flow: sends an inline Yes/No prompt; the unload (and
        # HRE manifest detach) happens in the callback. Returns immediately.
        await confirm_unload(event.client, event.chat_id, shortname)
        await event.eor(f"`Confirm unload of` **{shortname}** `below…`", time=5)
    elif zym in os.listdir("plugins"):
        return await event.eor(get_string("core_11"), time=3)
    else:
        await event.eor(f"**Nᴏ Pʟᴜɢɪɴ Nᴀᴍᴇᴅ** `{shortname}`", time=3)


@ultroid_cmd(
    pattern=r"uninstall( (.*)|$)",
)
async def uninstall(event):
    shortname = event.pattern_match.group(1).strip()
    if not shortname:
        await event.eor(get_string("core_13"))
        return
    lsd = os.listdir("addons")
    zym = f"{shortname}.py"
    if zym in lsd:
        try:
            fns.un_plug(shortname)
            os.remove(f"addons/{shortname}.py")
            await event.eor(f"**Uɴɪɴsᴛᴀʟʟᴇᴅ** `{shortname}` **Sᴜᴄᴄᴇssғᴜʟʟʏ.**", time=3)
            # os.remove(f"addons/{shortname}.py")
        except Exception as ex:
            return await event.eor(str(ex))
    elif zym in os.listdir("plugins"):
        return await event.eor(get_string("core_15"), time=3)
    else:
        return await event.eor(f"**Nᴏ Pʟᴜɢɪɴ Nᴀᴍᴇᴅ** `{shortname}`", time=3)


@ultroid_cmd(
    pattern=r"load( (.*)|$)",
    fullsudo=True,
)
async def load(event):
    shortname = event.pattern_match.group(1).strip()
    if not shortname:
        await event.eor(get_string("core_16"))
        return
    try:
        try:
            fns.un_plug(shortname)
        except Exception:
            pass
        load_addons(f"addons/{shortname}.py")
        await event.eor(get_string("core_17").format(shortname), time=3)
    except Exception as e:
        LOGS.exception(e)
        await eod(
            event,
            get_string("core_18").format(shortname, e),
            time=3,
        )


@ultroid_cmd(pattern="getaddons( (.*)|$)", fullsudo=True)
async def get_the_addons_lol(event):
    thelink = event.pattern_match.group(1).strip()
    xx = await event.eor(get_string("com_1"))
    fool = get_string("gas_1")
    if thelink is None:
        return await xx.eor(fool, time=10)
    split_thelink = thelink.split("/")
    if not ("raw" in thelink and thelink.endswith(".py")):
        return await xx.eor(fool, time=10)
    name_of_it = split_thelink[-1]
    plug = await fns.async_searcher(thelink)
    fil = f"addons/{name_of_it}"
    await xx.edit("Packing the codes...")
    with open(fil, "w", encoding="utf-8") as uult:
        uult.write(plug)
    await xx.edit("Packed. Now loading the plugin..")
    shortname = name_of_it.split(".")[0]
    try:
        load_addons(fil)
        await xx.eor(get_string("core_17").format(shortname), time=15)
    except Exception as e:
        LOGS.exception(e)
        await eod(
            xx,
            get_string("core_18").format(shortname, e),
            time=3,
        )


# ── HRE dependency surfaces ────────────────────────────────────────────────
# Status/audit/cleanup for the extender ResourceManager. Hardcoded strings
# (like plugins/tracer.py) — strings/** belongs to another wave.


@ultroid_cmd(pattern=r"depstatus(?: |$)([\w.]*)", owner_only=True)
async def depstatus_cmd(event):
    """
    .depstatus [plugin]
    Show HRE dependency status: an overview, or one plugin's manifest.
    """
    from pyUltroid.extender import ex as hre

    query = event.pattern_match.group(1).strip()

    if not query:
        audit = hre.audit()
        stats = audit["registry_stats"]
        text = (
            f"**📦 HRE Dependency Status**\n\n"
            f"Managed plugins: `{audit['total']}`\n"
            f"OK: `{audit['ok']}`   Blocked: `{audit['blocked']}`\n"
            f"HRE-installed packages: `{stats['hre_managed']}`\n"
            f"Baseline packages: `{stats['baseline_packages']}`\n"
            f"Orphaned packages: `{len(audit['orphans'])}`"
        )
        if audit["blocked_plugins"]:
            text += "\n\n**Blocked:** " + ", ".join(
                f"`{p}`" for p in audit["blocked_plugins"]
            )
        if audit["orphans"]:
            text += (
                "\n**Orphans:** "
                + ", ".join(f"`{p}`" for p in audit["orphans"])
                + "\nRun `.depclean` to remove them."
            )
        return await event.eor(text)

    # Accept a bare stem ("imaging") as well as a canonical id
    # ("plugins.imaging").
    name = query
    if hre.status(name) is None:
        for prefix in ("plugins", "addons", "assistant"):
            candidate = f"{prefix}.{query}"
            if hre.status(candidate) is not None:
                name = candidate
                break
    st = hre.status(name)
    if st is None:
        return await event.eor(
            f"No HRE manifest for `{query}` — the plugin is not loaded "
            f"under HRE management."
        )

    lines = [
        f"**📦 `{st['plugin']}`**",
        f"Loadable: {'✅' if st['is_loadable'] else '❌'}",
    ]
    if st["requires"]:
        lines.append("Requires: " + ", ".join(f"`{d}`" for d in st["requires"]))
    if st["optional"]:
        lines.append("Optional: " + ", ".join(f"`{d}`" for d in st["optional"]))
    if st["conflicts"]:
        lines.append("Conflicts: " + ", ".join(f"`{d}`" for d in st["conflicts"]))
    if st["missing_required"]:
        lines.append(
            "**Missing required:** "
            + ", ".join(f"`{d}`" for d in st["missing_required"])
        )
    if st["missing_optional"]:
        lines.append(
            "Missing optional: " + ", ".join(f"`{d}`" for d in st["missing_optional"])
        )
    if st["conflicting"]:
        lines.append(
            "**Conflicting (installed):** "
            + ", ".join(f"`{d}`" for d in st["conflicting"])
        )
    if st["tags"]:
        lines.append("Tags: " + ", ".join(f"`{t}`" for t in st["tags"]))
    await event.eor("\n".join(lines))


@ultroid_cmd(pattern=r"depaudit$", owner_only=True)
async def depaudit_cmd(event):
    """
    .depaudit
    Audit dependencies across all HRE-managed plugins.
    """
    from pyUltroid.extender import ex as hre

    audit = hre.audit()
    lines = [
        f"**📦 HRE Audit** — `{audit['total']}` managed, "
        f"`{audit['ok']}` ok, `{audit['blocked']}` blocked"
    ]
    for plugin, deps in audit["missing_deps"].items():
        lines.append(f"⚠️ `{plugin}` missing: " + ", ".join(f"`{d}`" for d in deps))
    for plugin, deps in audit["conflicts"].items():
        lines.append(f"❌ `{plugin}` conflicts: " + ", ".join(f"`{d}`" for d in deps))
    if audit["orphans"]:
        lines.append(
            "🗑 Orphans: "
            + ", ".join(f"`{p}`" for p in audit["orphans"])
            + " (`.depclean` to remove)"
        )
    if len(lines) == 1:
        lines.append("\n✅ All managed plugins satisfied.")
    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:3990] + "\n_…truncated_"
    await event.eor(text)


@ultroid_cmd(pattern=r"depclean$", owner_only=True)
async def depclean_cmd(event):
    """
    .depclean
    Uninstall orphaned HRE-managed packages (installed by HRE, no longer
    needed by any loaded plugin).
    """
    from pyUltroid.extender import ex as hre

    msg = await event.eor("`🗑 scanning for orphaned HRE packages…`")
    try:
        removed = await hre.cleanup()
    except Exception as exc:
        LOGS.exception(exc)
        return await msg.edit(f"❌ Cleanup failed: `{exc}`")
    if removed:
        await msg.edit(
            "🗑 Removed orphaned packages: " + ", ".join(f"`{p}`" for p in removed)
        )
    else:
        await msg.edit("✅ No orphaned HRE-managed packages.")
