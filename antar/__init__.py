"""ANTAR -- incrementality-native payment recovery.

Credentials are loaded from a local .env at import so nothing needs to be typed
onto a command line or exported into shell history. Real environment variables
take precedence; the file only fills gaps.
"""

from antar.env import load_env_file

load_env_file()
