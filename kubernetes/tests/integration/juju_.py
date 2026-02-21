# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

import jubilant_backports


def run_action(juju: jubilant_backports.Juju, unit_name: str, action_name: str, **params) -> dict:
    """Run a Juju action on a unit.

    Args:
        juju: Jubilant Juju instance
        unit_name: Name of the unit to run the action on
        action_name: Name of the action to run
        **params: Action parameters

    Returns:
        Dictionary of action results
    """
    task = juju.run(unit_name, action_name, params)
    return task.results
