import sys

from . import cli, hlink as hlink_cli, spawn
from .providers import PROVIDERS


def harness_link():
    cli.main()


def hlink():
    hlink_cli.main()


def harness_link_spawn():
    if len(sys.argv) > 1 and sys.argv[1] in PROVIDERS:
        cli.provider_key(PROVIDERS[sys.argv[1]])
    spawn.main()


def _provider(slug):
    cli.main([slug, *sys.argv[1:]])


def _provider_spawn(slug):
    cli.provider_key(PROVIDERS[slug], prompt=(slug == "inferx"))
    spawn.main([slug, *sys.argv[1:]])


def albert():
    _provider("albert")


def nim():
    _provider("nim")


def inferx():
    _provider("inferx")


def orfree():
    _provider("orfree")


def albert_spawn():
    _provider_spawn("albert")


def nim_spawn():
    _provider_spawn("nim")


def inferx_spawn():
    _provider_spawn("inferx")


def orfree_spawn():
    _provider_spawn("orfree")
