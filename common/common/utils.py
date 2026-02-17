# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Utility functions."""

import secrets
import string

DEFAULT_PASSWORD_LENGTH = 24


def generate_password(length: int = DEFAULT_PASSWORD_LENGTH) -> str:
    """Randomly generate a string intended to be used as a password.

    Args:
        length: length of the randomly generated string to be returned
    Returns:
        A randomly generated string intended to be used as a password.
    """
    choices = string.ascii_letters + string.digits
    # Might seem risky but in fact the probability that a password doesn't pass these checks is low
    while True:
        password = "".join([secrets.choice(choices) for i in range(length)])
        # These checks are consistent with the rules for the password validation MySQL component
        # on the MySQL charms
        if all((
            any(c.islower() for c in password),
            any(c.isupper() for c in password),
            any(c.isdigit() for c in password),
        )):
            return password
