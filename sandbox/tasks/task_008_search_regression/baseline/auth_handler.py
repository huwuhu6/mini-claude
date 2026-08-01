#!/usr/bin/env python3
"""
Regression test file: long path (60+ chars) + 10 consecutive 'config' lines.
Combined: src/very/deeply/nested/auth_handler/config_manager.py
Path length: 58 chars
"""
# src/very/deeply/nested/auth_handler/config_manager.py

# Lines 1-2: filler
import logging
logger = logging.getLogger(__name__)


def init_config():          # HIT 1
    config = load()         # HIT 2
    check(config)           # HIT 3
    merge(config)           # HIT 4
    return config


def init_config():          # HIT 5 (overloaded, duplicate - intentional)
    config = load()         # HIT 6
    check(config)           # HIT 7
    merge(config)           # HIT 8
    return config


def init_config():          # HIT 9 (overloaded, duplicate - intentional)
    config = load()         # HIT 10
    check(config)           # HIT 11
    merge(config)           # HIT 12
    return config
