# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Regression test for https://github.com/canonical/mysql-router-operators/issues/115.

`RelationSecrets` used to call `.id` on the result of `model.get_relation()` without
guarding against `None`. During scale-in, the unit's `tls` peer relation (used to store
TLS secrets) is torn down around the same time as the `certificates` relation, so by the
time `certificates-relation-broken` is handled, `model.get_relation("tls")` can already
return `None`, crashing `_on_tls_relation_broken` -> `secrets.set_value` -> `_remove_value`.
"""

import scenario

import charm


def test_certificates_relation_broken_without_tls_peer_does_not_crash():
    """`certificates-relation-broken` must not raise AttributeError when the `tls`

    peer relation has already been torn down (as happens during scale-in).
    """
    context = scenario.Context(charm.KubernetesRouterCharm)
    certificates_relation = scenario.Relation(
        endpoint="certificates", interface="tls-certificates"
    )
    input_state = scenario.State(
        containers=[scenario.Container("mysql-router", can_connect=True)],
        leader=True,
        relations=[
            certificates_relation,
            # Deliberately omit the "tls" peer relation to simulate it already being gone.
            scenario.PeerRelation(endpoint="mysql-router-peers"),
            scenario.PeerRelation(endpoint="refresh-v-three"),
        ],
    )

    # Before the fix, this raised AttributeError: 'NoneType' object has no attribute 'id'
    # because `self.model.get_relation("tls")` returns None while handling the
    # certificates relation's relation-broken event.
    output_state = context.run(certificates_relation.broken_event, input_state)

    assert output_state.unit_status.name != "error"
