"""Core package. Also applies one process-wide network compat fix.

aiohttp >= 3.11 silently switches to AsyncResolver (c-ares via aiodns) when
aiodns is installed. c-ares queries the DNS servers directly and intermittently
fails on this network ("Could not contact DNS servers") while the OS resolver —
used by browsers and everything else — keeps working from its cache. Pin every
ClientSession to the OS resolver so the bot behaves like the rest of the machine.
"""
import aiohttp.connector
import aiohttp.resolver

aiohttp.resolver.DefaultResolver = aiohttp.resolver.ThreadedResolver
aiohttp.connector.DefaultResolver = aiohttp.resolver.ThreadedResolver
