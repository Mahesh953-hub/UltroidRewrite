import ast
import asyncio
import inspect


class DebugKit:
    """Async execution helpers for the eval/debug surfaces.

    One instance (``debug_kit``); the module-level functions below are
    backward-compatible aliases.
    """

    async def aexec(self, code: str, kwargs: dict = {}) -> object:
        """Compile *code* as the body of an async function and run it.

        ``kwargs`` become the function's arguments (by name), so ``code`` can
        reference them directly. A trailing bare expression is returned; if
        that expression itself evaluates to an unawaited coroutine, it is
        awaited too before returning.
        """
        body = ast.parse(code, "exec").body
        if body and isinstance(body[-1], ast.Expr):
            body[-1] = ast.Return(value=body[-1].value)

        name = "aexec"
        node = ast.Module(
            body=[
                ast.AsyncFunctionDef(
                    name=name,
                    args=ast.arguments(
                        posonlyargs=[],
                        args=[ast.arg(arg=key) for key in kwargs],
                        vararg=None,
                        kwonlyargs=[],
                        kw_defaults=[],
                        kwarg=None,
                        defaults=[],
                    ),
                    body=body,
                    decorator_list=[],
                    returns=None,
                    type_params=[],
                )
            ],
            type_ignores=[],
        )
        ast.fix_missing_locations(node)
        temp = {}
        exec(compile(node, "<string>", "exec"), temp)
        func = await temp[name](*kwargs.values())
        return await func if inspect.iscoroutine(func) else func

    async def shell(self, cmd: str) -> str:
        proc = await asyncio.create_subprocess_shell(
            cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        try:
            stdout, stderr = await proc.communicate()
            return (stdout + stderr).decode()
        finally:
            try:
                if not proc.returncode:
                    proc.terminate()
            except ProcessLookupError:
                pass
            else:
                await proc.wait()


debug_kit = DebugKit()


async def aexec(code: str, kwargs: dict = {}) -> object:
    return await debug_kit.aexec(code, kwargs)


async def shell(cmd: str) -> str:
    return await debug_kit.shell(cmd)


async def shell(cmd: str) -> str:
    proc = await asyncio.create_subprocess_shell(
        cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        stdout, stderr = await proc.communicate()
        return (stdout + stderr).decode()
    finally:
        try:
            if not proc.returncode:
                proc.terminate()
        except ProcessLookupError:
            pass
        else:
            await proc.wait()
