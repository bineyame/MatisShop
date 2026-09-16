"""Seed the demo environment as soon as the module is installed.

`make bootstrap` therefore produces a demo-ready database with no manual UI
configuration, which is the point of a seeded demo.
"""

import logging

_logger = logging.getLogger(__name__)


def post_init_hook(env):
    _logger.info("mati_demo: seeding demo environment")
    env["mati.demo.setup"].seed_all()
