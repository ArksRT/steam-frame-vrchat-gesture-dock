"""Attach the gesture button to the local Steam Frame Dashboard."""

import asyncio
import logging
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import aiohttp


def is_bar(target):
    query = parse_qs(urlsplit(target.get("url", "")).query)
    return query.get("vrOverlayKey") == ["valve.steam.gamepadui.bar"]


async def evaluate_bar(session, script):
    async with session.get("http://127.0.0.1:8081/json") as response:
        response.raise_for_status()
        targets = await response.json(content_type=None)
    bar = next((target for target in targets if is_bar(target)), None)
    if not bar:
        return "waiting for Dashboard"
    async with session.ws_connect(bar["webSocketDebuggerUrl"]) as socket:
        await socket.send_json({"id": 1, "method": "Runtime.evaluate",
                                "params": {"expression": script, "returnByValue": True}})
        while True:
            reply = await socket.receive_json(timeout=3)
            if reply.get("id") != 1:
                continue
            result = reply.get("result", {})
            if reply.get("error") or result.get("exceptionDetails"):
                raise RuntimeError("Dashboard did not evaluate the requested expression")
            return result["result"].get("value")


async def run():
    script = Path(__file__).with_name("dashboard.js").read_text(encoding="utf-8")
    prior = None
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=4)) as session:
        while True:
            try:
                status = await evaluate_bar(session, script)
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError, KeyError,
                    TypeError, ValueError, RuntimeError) as error:
                status = f"Waiting for Steam Dashboard ({type(error).__name__})"
            if status != prior:
                logging.info("%s", status)
                prior = status
            await asyncio.sleep(4)


async def status():
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=3)) as session:
            result = await evaluate_bar(session,
                'document.querySelector("#vrchat-gesture-toggle")?.isConnected ? "mounted" : "waiting"')
            return "mounted" if result == "mounted" else "waiting"
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError, KeyError,
            TypeError, ValueError, RuntimeError):
        return "waiting"


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if sys.argv[1:] == ["--status"]:
        print(asyncio.run(status()))
    else:
        asyncio.run(run())
