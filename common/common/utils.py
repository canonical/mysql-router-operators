# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Utility functions."""

import secrets
import string


def generate_password() -> str:
    """Generate a random password."""
    choices = string.ascii_letters + string.digits
    # Might seem risky but in fact the probability that a password doesn't pass these checks is low
    while True:
        password = "".join([secrets.choice(choices) for i in range(24)])
        # These checks are consistent with the rules for the password validation on MySQL server
        if all((
            any(c.islower() for c in password),
            any(c.isupper() for c in password),
            any(c.isdigit() for c in password),
        )):
            return password
