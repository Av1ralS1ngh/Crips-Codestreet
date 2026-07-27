"""The transparency log must be RFC 6962, and it must fail when the log is edited.

Three kinds of test here, and the distinction matters:

  known answers      roots hard-coded from the published Certificate Transparency test
                     data. These pin the convention: if someone "simplifies" the tree into
                     the odd-node-promotion shape used by caveat/ledger.py, these break.
  independent recompute
                     a second Merkle implementation written a different way (the
                     append-only stack / binary-counter construction) which must agree with
                     the recursive RFC definition for every tree size.
  adversarial        a log that was edited, truncated, forked, or served two ways. Every
                     one of these must fail a check rather than pass quietly.
"""

from __future__ import annotations

import random

import pytest

from caveat import ledger as caveat_ledger
from plumbline.transparency import (
    AUDIT_CONSISTENT,
    AUDIT_FORK,
    AUDIT_LOG_ID_MISMATCH,
    AUDIT_SPLIT_VIEW,
    AUDIT_STH_SIGNATURE_INVALID,
    AUDIT_UNRELATED,
    EMPTY_ROOT,
    ENTRY_RECEIPT,
    ENTRY_UNATTESTED_SELECTION,
    PROOF_BAD_INDEX,
    PROOF_OK,
    PROOF_ROOT_MISMATCH,
    PROOF_SIZE_REGRESSION,
    ConsistencyProof,
    InclusionProof,
    LogAuditor,
    SignedTreeHead,
    TransparencyError,
    TransparencyLog,
    audit_pair,
    check_consistency_proof,
    check_inclusion_proof,
    consistency_path,
    hash_children,
    hash_leaf,
    inclusion_path,
    key_id,
    leaf_data_for,
    merkle_tree_hash,
    replay_leaves,
    sign_tree_head,
    split_point,
    verify_consistency,
    verify_inclusion,
    verify_tree_head,
)

T0 = 1_753_600_000
LOG_KEY = "prototype-transparency-log-key"
OTHER_KEY = "some-other-key-entirely"

# The published Certificate Transparency test leaves, byte for byte.
CT_TEST_DATA = [
    b"",
    bytes([0x00]),
    bytes([0x10]),
    bytes([0x20, 0x21]),
    bytes([0x30, 0x31]),
    bytes([0x40, 0x41, 0x42, 0x43]),
    bytes(range(0x50, 0x58)),
    bytes(range(0x60, 0x70)),
]

# Roots of the first n CT test leaves. These are the published RFC 6962 reference values.
CT_ROOTS = {
    0: "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    1: "6e340b9cffb37a989ca544e6bb780a2c78901d3fb33738768511a30617afa01d",
    8: "5dc9da79a70659a9ad559cb701ded9a2ab9d823aad2f4960cfe370eff4604328",
}


# ======================================================================================
# An independent Merkle implementation, written a different way on purpose.
# ======================================================================================


def reference_mth(leaves):
    """RFC 6962 MTH via the append-only stack construction.

    Deliberately not the recursive split the module implements. Each leaf is pushed as a
    perfect subtree of size 1 and merged with its left neighbour while the two sizes match,
    which is the binary-counter view of the same tree. Folding the leftover stack from the
    right reproduces the root. If this and `merkle_tree_hash` ever disagree, one of them is
    wrong — which is the point of having both.
    """
    if not leaves:
        return EMPTY_ROOT
    stack: list[tuple[int, str]] = []
    for leaf in leaves:
        node = (1, leaf)
        while stack and stack[-1][0] == node[0]:
            size, left = stack.pop()
            node = (size * 2, hash_children(left, node[1]))
        stack.append(node)
    acc = stack[-1][1]
    for _, h in reversed(stack[:-1]):
        acc = hash_children(h, acc)
    return acc


def leaves_of(n: int) -> list[str]:
    return [hash_leaf(f"entry-{i}".encode()) for i in range(n)]


# ======================================================================================
# Known answers — these pin the convention
# ======================================================================================


def test_empty_tree_root_is_sha256_of_nothing():
    assert merkle_tree_hash([]) == CT_ROOTS[0]
    assert EMPTY_ROOT == CT_ROOTS[0]


@pytest.mark.parametrize("n", [1, 8])
def test_ct_reference_roots(n):
    leaves = [hash_leaf(d) for d in CT_TEST_DATA[:n]]
    assert merkle_tree_hash(leaves) == CT_ROOTS[n]


def test_leaf_and_node_hashing_are_domain_separated():
    """An interior node must not be presentable as a leaf, or a proof could be forged."""
    a, b = hash_leaf(b"a"), hash_leaf(b"b")
    assert hash_children(a, b) != hash_leaf(bytes.fromhex(a) + bytes.fromhex(b))


def test_node_hash_rejects_non_hex_input():
    with pytest.raises(TransparencyError, match="hex-encoded"):
        hash_children("not-a-hash", hash_leaf(b"x"))


@pytest.mark.parametrize(
    ("n", "expected"), [(2, 1), (3, 2), (4, 2), (5, 4), (7, 4), (8, 4), (9, 8), (1000, 512)]
)
def test_split_point_is_largest_power_of_two_below_n(n, expected):
    assert split_point(n) == expected


@pytest.mark.parametrize("n", [0, 1])
def test_split_point_undefined_below_two(n):
    with pytest.raises(TransparencyError, match="undefined"):
        split_point(n)


def kernel_root(leaves):
    """caveat/ledger.py's tree: build bottom-up, promote an odd trailing node."""
    if not leaves:
        return caveat_ledger.GENESIS
    level = list(leaves)
    while len(level) > 1:
        nxt = [
            caveat_ledger._node_hash(level[i], level[i + 1]) for i in range(0, len(level) - 1, 2)
        ]
        if len(level) % 2 == 1:
            nxt.append(level[-1])
        level = nxt
    return level[0]


@pytest.mark.parametrize("n", list(range(1, 129)))
def test_odd_tail_promotion_computes_the_same_root_as_the_rfc_split(n):
    """The honest finding, measured rather than argued.

    "Promote the odd tail" reads like a different tree from "split at the largest power of
    two", and it is tempting to claim the two conventions disagree. They do not: for every
    size up to 128 the roots are identical. The claim in this module's docstring is this
    test, not the other way round.
    """
    leaves = [hash_leaf(bytes([i % 256, i // 256])) for i in range(n)]
    assert merkle_tree_hash(leaves) == kernel_root(leaves)


def test_the_two_logs_are_still_not_interchangeable():
    """Same root arithmetic, different leaf definition and different proof wire format.

    This is what actually stops a proof crossing between the two logs, and it is worth
    pinning: a future "unification" that made the leaves compatible would silently change
    what an inclusion proof means.
    """
    body = {"receipt_id": "rcpt_0"}
    ct_log = TransparencyLog("ct")
    ct_entry = ct_log.append(kind=ENTRY_RECEIPT, body=body, timestamp=T0)

    kernel = caveat_ledger.MerkleLedger()
    kernel_entry = kernel.append(ENTRY_RECEIPT, body, T0)

    # The kernel hashes seq and prev_hash into the leaf; this module hashes content only.
    assert ct_entry.leaf_hash != kernel_entry.leaf_hash

    # The kernel emits (side, hash) pairs; RFC 6962 emits bare ordered hashes.
    for _ in range(3):
        kernel.append(ENTRY_RECEIPT, body, T0)
        ct_log.append(kind=ENTRY_RECEIPT, body=body, timestamp=T0)
    kernel_proof = kernel.inclusion_proof(0).to_dict()
    assert kernel_proof["path"] and isinstance(kernel_proof["path"][0], dict)
    assert all(isinstance(h, str) for h in ct_log.inclusion_proof(0).to_dict()["path"])
    with pytest.raises(KeyError):
        InclusionProof.from_dict(kernel_proof)

    # And the kernel has no consistency proof, which is the reason this module exists.
    assert not hasattr(caveat_ledger.MerkleLedger, "consistency_proof")
    assert hasattr(TransparencyLog, "consistency_proof")


# ======================================================================================
# Independent recompute
# ======================================================================================


@pytest.mark.parametrize("n", list(range(0, 65)))
def test_recursive_and_stack_constructions_agree(n):
    leaves = leaves_of(n)
    assert merkle_tree_hash(leaves) == reference_mth(leaves)


def test_ct_reference_roots_via_the_independent_implementation():
    for n, expected in CT_ROOTS.items():
        assert reference_mth([hash_leaf(d) for d in CT_TEST_DATA[:n]]) == expected


# ======================================================================================
# Inclusion proofs
# ======================================================================================


@pytest.mark.parametrize("n", list(range(1, 34)))
def test_every_inclusion_proof_verifies_against_an_independent_root(n):
    leaves = leaves_of(n)
    root = reference_mth(leaves)  # not the module's own root
    for index in range(n):
        path = inclusion_path(index, leaves)
        assert verify_inclusion(
            leaf_hash=leaves[index],
            leaf_index=index,
            tree_size=n,
            path=path,
            root_hash=root,
        ), f"n={n} index={index}"


def test_inclusion_path_length_is_logarithmic():
    leaves = leaves_of(1000)
    assert len(inclusion_path(0, leaves)) <= 10
    assert len(inclusion_path(999, leaves)) <= 10


def test_inclusion_proof_fails_for_a_substituted_leaf():
    leaves = leaves_of(9)
    root = merkle_tree_hash(leaves)
    path = inclusion_path(4, leaves)
    assert not verify_inclusion(
        leaf_hash=hash_leaf(b"a receipt that was never logged"),
        leaf_index=4,
        tree_size=9,
        path=path,
        root_hash=root,
    )


def test_inclusion_proof_fails_at_the_wrong_index():
    leaves = leaves_of(9)
    root = merkle_tree_hash(leaves)
    path = inclusion_path(4, leaves)
    assert not verify_inclusion(
        leaf_hash=leaves[4], leaf_index=5, tree_size=9, path=path, root_hash=root
    )


def test_inclusion_proof_fails_when_the_path_is_truncated_or_padded():
    leaves = leaves_of(11)
    root = merkle_tree_hash(leaves)
    path = inclusion_path(6, leaves)
    assert verify_inclusion(
        leaf_hash=leaves[6], leaf_index=6, tree_size=11, path=path, root_hash=root
    )
    assert not verify_inclusion(
        leaf_hash=leaves[6], leaf_index=6, tree_size=11, path=path[:-1], root_hash=root
    )
    assert not verify_inclusion(
        leaf_hash=leaves[6],
        leaf_index=6,
        tree_size=11,
        path=(*path, hash_leaf(b"extra")),
        root_hash=root,
    )


def test_inclusion_proof_rejects_out_of_range_index():
    leaves = leaves_of(4)
    assert not verify_inclusion(
        leaf_hash=leaves[0], leaf_index=4, tree_size=4, path=(), root_hash=merkle_tree_hash(leaves)
    )
    assert not verify_inclusion(
        leaf_hash=leaves[0], leaf_index=-1, tree_size=4, path=(), root_hash=merkle_tree_hash(leaves)
    )
    with pytest.raises(TransparencyError, match="outside a tree"):
        inclusion_path(4, leaves)


# ======================================================================================
# Consistency proofs — the mechanism that catches a retroactive edit
# ======================================================================================


@pytest.mark.parametrize("n", list(range(1, 25)))
def test_every_consistency_proof_verifies_against_independent_roots(n):
    leaves = leaves_of(n)
    for m in range(1, n + 1):
        path = consistency_path(m, leaves)
        assert verify_consistency(
            first_size=m,
            first_root=reference_mth(leaves[:m]),
            second_size=n,
            second_root=reference_mth(leaves),
            path=path,
        ), f"n={n} m={m}"


def test_equal_sizes_need_an_empty_proof_and_equal_roots():
    leaves = leaves_of(7)
    root = merkle_tree_hash(leaves)
    assert consistency_path(7, leaves) == ()
    assert verify_consistency(
        first_size=7, first_root=root, second_size=7, second_root=root, path=()
    )
    assert not verify_consistency(
        first_size=7, first_root=root, second_size=7, second_root=hash_leaf(b"x"), path=()
    )
    assert not verify_consistency(
        first_size=7, first_root=root, second_size=7, second_root=root, path=(root,)
    )


def test_every_tree_extends_the_empty_tree():
    leaves = leaves_of(5)
    assert verify_consistency(
        first_size=0,
        first_root=EMPTY_ROOT,
        second_size=5,
        second_root=merkle_tree_hash(leaves),
        path=(),
    )
    with pytest.raises(TransparencyError, match="0 < first"):
        consistency_path(0, leaves)


def test_retroactive_edit_fails_the_consistency_check():
    """The omission attack: a published entry is rewritten to drop a candidate."""
    original = leaves_of(6)
    published_root = merkle_tree_hash(original[:4])

    edited = list(original)
    edited[1] = hash_leaf(b"entry-1 with an instrument quietly removed")
    edited.append(hash_leaf(b"entry-6"))

    # The editor produces a proof from its own doctored log; it agrees with itself.
    path = consistency_path(4, edited)
    assert verify_consistency(
        first_size=4,
        first_root=merkle_tree_hash(edited[:4]),
        second_size=len(edited),
        second_root=merkle_tree_hash(edited),
        path=path,
    )
    # Against the root that was actually published, it does not.
    assert not verify_consistency(
        first_size=4,
        first_root=published_root,
        second_size=len(edited),
        second_root=merkle_tree_hash(edited),
        path=path,
    )


def test_truncated_log_fails_the_consistency_check():
    leaves = leaves_of(8)
    published_root = merkle_tree_hash(leaves)
    truncated = leaves[:5]

    # Forward direction is nonsense: the log shrank.
    assert not verify_consistency(
        first_size=8,
        first_root=published_root,
        second_size=5,
        second_root=merkle_tree_hash(truncated),
        path=consistency_path(5, leaves),
    )
    # Nor can the truncated log claim the published head as a prefix of itself.
    for m in range(1, 6):
        assert not verify_consistency(
            first_size=8,
            first_root=published_root,
            second_size=5,
            second_root=merkle_tree_hash(truncated),
            path=consistency_path(m, truncated),
        )


def test_dropping_a_leaf_and_backfilling_to_the_same_size_fails():
    """Same tree size, different history — the shape a candidate-set edit actually takes."""
    original = leaves_of(6)
    doctored = original[:2] + original[3:] + [hash_leaf(b"replacement tail")]
    assert len(doctored) == len(original)
    assert not verify_consistency(
        first_size=3,
        first_root=merkle_tree_hash(original[:3]),
        second_size=6,
        second_root=merkle_tree_hash(doctored),
        path=consistency_path(3, doctored),
    )


def test_consistency_proof_rejects_a_forged_path():
    leaves = leaves_of(9)
    path = list(consistency_path(4, leaves))
    assert path
    path[0] = hash_leaf(b"forged node")
    assert not verify_consistency(
        first_size=4,
        first_root=merkle_tree_hash(leaves[:4]),
        second_size=9,
        second_root=merkle_tree_hash(leaves),
        path=tuple(path),
    )


def test_consistency_path_rejects_impossible_sizes():
    leaves = leaves_of(4)
    with pytest.raises(TransparencyError, match="0 < first"):
        consistency_path(5, leaves)


# ======================================================================================
# The log
# ======================================================================================


def build_log(n: int, *, log_id: str = "plumbline-demo-log", key=LOG_KEY) -> TransparencyLog:
    log = TransparencyLog(log_id, signing_key=key)
    for i in range(n):
        log.append(kind=ENTRY_RECEIPT, body={"receipt_id": f"rcpt_{i}"}, timestamp=T0 + i)
    return log


def test_append_and_read_back():
    log = build_log(3)
    assert len(log) == 3
    assert [e.seq for e in log.entries] == [0, 1, 2]
    assert log.get(1).body == {"receipt_id": "rcpt_1"}
    assert log.get(9) is None
    assert len(log.filter(ENTRY_RECEIPT)) == 3
    assert log.filter(ENTRY_UNATTESTED_SELECTION) == ()


def test_append_rejects_an_unknown_entry_kind():
    log = TransparencyLog("l")
    with pytest.raises(TransparencyError, match="unknown log entry kind"):
        log.append(kind="whatever", body={}, timestamp=T0)


def test_log_root_matches_the_independent_implementation():
    log = build_log(13)
    assert log.root() == reference_mth(list(log.leaves))
    assert log.root(5) == reference_mth(list(log.leaves)[:5])


def test_the_log_is_deterministic_across_instances():
    """Two runs over the same entries and timestamps produce the same root, byte for byte."""
    a, b = build_log(9), build_log(9)
    assert a.root() == b.root()
    assert a.leaves == b.leaves


def test_leaf_hash_does_not_depend_on_position():
    """RFC 6962 leaves commit to content, not index.

    That is what lets two observers compare their views of the same receipt at all: if the
    index were hashed in, a receipt logged at a different position would look like a
    different document.
    """
    body = {"receipt_id": "rcpt_shared"}
    first = TransparencyLog("a")
    second = TransparencyLog("b")
    second.append(kind=ENTRY_RECEIPT, body={"receipt_id": "filler"}, timestamp=T0)
    e1 = first.append(kind=ENTRY_RECEIPT, body=body, timestamp=T0 + 5)
    e2 = second.append(kind=ENTRY_RECEIPT, body=body, timestamp=T0 + 5)
    assert e1.seq != e2.seq
    assert e1.leaf_hash == e2.leaf_hash
    assert e1.leaf_data() == leaf_data_for(kind=ENTRY_RECEIPT, body=body, timestamp=T0 + 5)


def test_log_inclusion_proof_round_trip():
    log = build_log(17)
    sth = log.signed_tree_head(timestamp=T0 + 100)
    for seq in range(17):
        proof = log.inclusion_proof(seq)
        assert proof.verify()
        assert proof.verify_against(sth)
        assert InclusionProof.from_dict(proof.to_dict()) == proof
        assert check_inclusion_proof(proof, sth) == (True, PROOF_OK)


def test_inclusion_proof_against_a_head_from_a_different_size_is_rejected():
    log = build_log(8)
    proof = log.inclusion_proof(2, tree_size=5)
    later = log.signed_tree_head(timestamp=T0 + 100)
    assert proof.verify()
    assert not proof.verify_against(later)
    ok, code = check_inclusion_proof(proof, later)
    assert (ok, code) == (False, "PROOF_MALFORMED")


def test_inclusion_proof_reason_codes():
    log = build_log(4)
    sth = log.signed_tree_head(timestamp=T0)
    bad_index = InclusionProof(leaf_index=9, leaf_hash=log.leaves[0], tree_size=4, root_hash=sth.root_hash)
    assert check_inclusion_proof(bad_index, sth) == (False, PROOF_BAD_INDEX)

    wrong_root = InclusionProof(
        leaf_index=0,
        leaf_hash=log.leaves[0],
        tree_size=4,
        root_hash=hash_leaf(b"not the root"),
        path=log.inclusion_proof(0).path,
    )
    assert check_inclusion_proof(wrong_root, sth) == (False, PROOF_ROOT_MISMATCH)


def test_inclusion_proof_errors_are_actionable():
    log = build_log(3)
    with pytest.raises(TransparencyError, match="request a proof against a head published"):
        log.inclusion_proof(5)
    with pytest.raises(TransparencyError, match="the log holds"):
        log.inclusion_proof(0, tree_size=99)


def test_find_leaf_locates_a_receipt_and_reports_absence():
    log = build_log(5)
    assert log.find_leaf(log.leaves[3]) == 3
    assert log.find_leaf(hash_leaf(b"never logged")) is None


def test_replay_leaves_catches_an_asserted_leaf_hash():
    """An auditor recomputes every leaf before trusting a root computed over them."""
    log = build_log(4)
    assert replay_leaves(log.entries) == log.leaves

    entries = list(log.entries)
    tampered = entries[:1] + [
        type(entries[1])(
            seq=1,
            kind=ENTRY_RECEIPT,
            body={"receipt_id": "rcpt_1_edited"},
            timestamp=entries[1].timestamp,
            leaf_hash=entries[1].leaf_hash,  # the old hash, asserted rather than computed
        )
    ] + entries[2:]
    assert replay_leaves(tampered) != log.leaves


def test_log_consistency_proof_round_trip():
    log = build_log(21)
    for first in range(0, 22):
        proof = log.consistency_proof(first)
        assert proof.verify(), first
        assert ConsistencyProof.from_dict(proof.to_dict()) == proof
        assert check_consistency_proof(proof) == (True, PROOF_OK)


def test_prove_extends_confirms_an_append_only_log():
    log = build_log(4)
    published = log.signed_tree_head(timestamp=T0 + 50)
    for i in range(4, 9):
        log.append(kind=ENTRY_RECEIPT, body={"receipt_id": f"rcpt_{i}"}, timestamp=T0 + i)
    proof = log.prove_extends(published)
    assert proof.verify()
    assert proof.first_root == published.root_hash


def test_prove_extends_fails_when_a_published_entry_was_edited():
    """This is the demo beat: omission leaves a signature."""
    log = build_log(4)
    published = log.signed_tree_head(timestamp=T0 + 50)

    doctored = TransparencyLog("plumbline-demo-log", signing_key=LOG_KEY)
    for i in range(4):
        body = {"receipt_id": f"rcpt_{i}"}
        if i == 1:
            body = {"receipt_id": "rcpt_1", "candidate_set": ["one instrument fewer"]}
        doctored.append(kind=ENTRY_RECEIPT, body=body, timestamp=T0 + i)
    doctored.append(kind=ENTRY_RECEIPT, body={"receipt_id": "rcpt_4"}, timestamp=T0 + 4)

    proof = doctored.prove_extends(published)
    assert not proof.verify()
    ok, code = check_consistency_proof(proof)
    assert (ok, code) == (False, PROOF_ROOT_MISMATCH)


def test_prove_extends_refuses_a_shrinking_log():
    log = build_log(6)
    published = log.signed_tree_head(timestamp=T0 + 50)
    short = build_log(3)
    with pytest.raises(TransparencyError, match="shrinking log"):
        short.prove_extends(published)


def test_check_consistency_proof_reports_a_size_regression():
    proof = ConsistencyProof(
        first_size=8, first_root=hash_leaf(b"a"), second_size=5, second_root=hash_leaf(b"b")
    )
    assert check_consistency_proof(proof) == (False, PROOF_SIZE_REGRESSION)


def test_log_refuses_proofs_against_sizes_it_does_not_have():
    log = build_log(3)
    with pytest.raises(TransparencyError, match="the log holds"):
        log.consistency_proof(2, 99)
    with pytest.raises(TransparencyError, match="consistency runs forward only"):
        log.consistency_proof(3, 2)
    with pytest.raises(TransparencyError, match="cannot root a tree"):
        log.root(99)


# ======================================================================================
# Signed tree heads
# ======================================================================================


def test_signed_tree_head_round_trip_and_tamper():
    log = build_log(6)
    sth = log.signed_tree_head(timestamp=T0 + 10)
    assert verify_tree_head(sth, LOG_KEY)
    assert not verify_tree_head(sth, OTHER_KEY)
    assert SignedTreeHead.from_dict(sth.to_dict()) == sth
    assert sth.signing_key_id == key_id(LOG_KEY)

    forged = SignedTreeHead(
        log_id=sth.log_id,
        tree_size=sth.tree_size,
        root_hash=hash_leaf(b"a root that was never computed"),
        timestamp=sth.timestamp,
        signature=sth.signature,
        signing_key_id=sth.signing_key_id,
    )
    assert not verify_tree_head(forged, LOG_KEY)


def test_unsigned_log_publishes_an_unsigned_head():
    log = TransparencyLog("no-key")
    log.append(kind=ENTRY_RECEIPT, body={"a": 1}, timestamp=T0)
    sth = log.signed_tree_head(timestamp=T0)
    assert sth.signature == ""
    assert not verify_tree_head(sth, LOG_KEY)
    signed = sign_tree_head(sth, LOG_KEY)
    assert verify_tree_head(signed, LOG_KEY)


def test_head_signature_covers_the_size_as_well_as_the_root():
    log = build_log(5)
    sth = log.signed_tree_head(timestamp=T0)
    lying = SignedTreeHead(
        log_id=sth.log_id,
        tree_size=99,
        root_hash=sth.root_hash,
        timestamp=sth.timestamp,
        signature=sth.signature,
        signing_key_id=sth.signing_key_id,
    )
    assert not verify_tree_head(lying, LOG_KEY)


# ======================================================================================
# The witness / auditor — split-view detection
# ======================================================================================


def test_two_observers_of_one_honest_log_are_consistent():
    log = build_log(5)
    head = log.signed_tree_head(timestamp=T0 + 10)
    report = audit_pair(
        auditor_id="witness_a", issuer_view=head, cardholder_view=head, key=LOG_KEY
    )
    assert report.ok
    assert report.outcome == AUDIT_CONSISTENT
    assert "witness_a" in report.render_text()


def test_split_view_is_detected_at_equal_tree_size():
    """A platform shows one log to the issuer and another to the Card Member."""
    issuer_log = build_log(4)
    cardholder_log = TransparencyLog("plumbline-demo-log", signing_key=LOG_KEY)
    for i in range(4):
        body = {"receipt_id": f"rcpt_{i}"}
        if i == 2:
            body = {"receipt_id": "rcpt_2", "note": "Amex never appeared in this version"}
        cardholder_log.append(kind=ENTRY_RECEIPT, body=body, timestamp=T0 + i)

    issuer_head = issuer_log.signed_tree_head(timestamp=T0 + 10)
    cardholder_head = cardholder_log.signed_tree_head(timestamp=T0 + 10)
    assert issuer_head.tree_size == cardholder_head.tree_size
    assert issuer_head.root_hash != cardholder_head.root_hash

    report = audit_pair(
        auditor_id="witness_a",
        issuer_view=issuer_head,
        cardholder_view=cardholder_head,
        key=LOG_KEY,
    )
    assert not report.ok
    assert report.outcome == AUDIT_SPLIT_VIEW
    assert AUDIT_SPLIT_VIEW in report.codes()
    detail = report.to_dict()["findings"][0]["detail"]
    assert "different histories" in detail


def test_fork_is_detected_when_a_consistency_proof_fails_between_two_views():
    honest = build_log(4)
    published = honest.signed_tree_head(timestamp=T0 + 10)

    doctored = TransparencyLog("plumbline-demo-log", signing_key=LOG_KEY)
    for i in range(6):
        body = {"receipt_id": f"rcpt_{i}"}
        if i == 1:
            body = {"receipt_id": "rcpt_1", "candidate_set": ["edited after publication"]}
        doctored.append(kind=ENTRY_RECEIPT, body=body, timestamp=T0 + i)
    later = doctored.signed_tree_head(timestamp=T0 + 20)

    proof = doctored.prove_extends(published)
    report = audit_pair(
        auditor_id="witness_a",
        issuer_view=published,
        cardholder_view=later,
        proof=proof,
        key=LOG_KEY,
    )
    assert not report.ok
    assert report.outcome == AUDIT_FORK
    assert "not a prefix" in report.to_dict()["findings"][0]["detail"]


def test_growth_between_two_views_is_consistent_when_the_log_is_honest():
    log = build_log(4)
    early = log.signed_tree_head(timestamp=T0 + 10)
    for i in range(4, 9):
        log.append(kind=ENTRY_RECEIPT, body={"receipt_id": f"rcpt_{i}"}, timestamp=T0 + i)
    later = log.signed_tree_head(timestamp=T0 + 20)
    report = audit_pair(
        auditor_id="witness_a",
        issuer_view=early,
        cardholder_view=later,
        proof=log.prove_extends(early),
        key=LOG_KEY,
    )
    assert report.ok


def test_a_witness_without_a_proof_says_unrelated_rather_than_consistent():
    log = build_log(4)
    early = log.signed_tree_head(timestamp=T0 + 10)
    log.append(kind=ENTRY_RECEIPT, body={"receipt_id": "rcpt_4"}, timestamp=T0 + 4)
    later = log.signed_tree_head(timestamp=T0 + 20)
    report = audit_pair(auditor_id="witness_a", issuer_view=early, cardholder_view=later)
    assert not report.ok
    assert report.outcome == AUDIT_UNRELATED


def test_witness_flags_a_head_that_does_not_verify():
    log = build_log(3)
    good = log.signed_tree_head(timestamp=T0)
    forged = sign_tree_head(
        SignedTreeHead(
            log_id=good.log_id, tree_size=3, root_hash=good.root_hash, timestamp=good.timestamp
        ),
        OTHER_KEY,
    )
    report = audit_pair(
        auditor_id="witness_a", issuer_view=good, cardholder_view=forged, key=LOG_KEY
    )
    assert AUDIT_STH_SIGNATURE_INVALID in report.codes()
    assert not report.ok


def test_auditor_reports_heads_from_a_different_log_rather_than_skipping_them():
    """This test previously asserted the opposite, and the opposite was a hole.

    Heads naming different logs used to be skipped in silence, which meant an audit of
    exactly two such heads produced no findings at all and fell through to the
    "nothing to compare" default — reporting VIEWS_CONSISTENT over a comparison it had
    declined to make. See `test_a_split_view_cannot_be_laundered_through_a_log_id` in
    test_plumbline_adversarial.py for the exploit that motivated the change.
    """
    a = build_log(4, log_id="log-a")
    b = build_log(5, log_id="log-b")
    auditor = LogAuditor("witness_a")
    auditor.observe("issuer", a.signed_tree_head(timestamp=T0))
    auditor.observe("cardholder", b.signed_tree_head(timestamp=T0))
    report = auditor.audit()
    assert report.outcome == AUDIT_LOG_ID_MISMATCH
    assert not report.ok
    assert len(auditor.observations) == 2
    assert "log-a" in report.findings[0].detail and "log-b" in report.findings[0].detail


def test_auditor_with_nothing_to_compare_is_honest_about_it():
    report = LogAuditor("witness_a").audit()
    assert report.ok
    # The count is part of the verdict: "consistent" over zero comparisons is an absence
    # of evidence, and a report that does not say so reads as evidence.
    assert "0 head(s) observed" in report.findings[0].detail


def test_a_single_observed_head_says_so_rather_than_claiming_consistency():
    log = build_log(4)
    auditor = LogAuditor("witness_a")
    auditor.observe("issuer", log.signed_tree_head(timestamp=T0))
    report = auditor.audit()
    assert report.outcome == AUDIT_CONSISTENT
    assert "1 head(s) observed" in report.findings[0].detail


def test_three_observers_and_one_split_view():
    honest = build_log(6)
    head = honest.signed_tree_head(timestamp=T0)
    other = TransparencyLog("plumbline-demo-log", signing_key=LOG_KEY)
    for i in range(6):
        other.append(
            kind=ENTRY_RECEIPT,
            body={"receipt_id": f"rcpt_{i}"} if i != 5 else {"receipt_id": "rcpt_5_edited"},
            timestamp=T0 + i,
        )
    auditor = LogAuditor("witness_a")
    auditor.observe("issuer", head)
    auditor.observe("cardholder", head)
    auditor.observe("merchant", other.signed_tree_head(timestamp=T0))
    report = auditor.audit(key=LOG_KEY)
    assert report.outcome == AUDIT_SPLIT_VIEW
    assert report.codes().count(AUDIT_CONSISTENT) == 1
    assert report.codes().count(AUDIT_SPLIT_VIEW) == 2


# ======================================================================================
# Randomised sweep
# ======================================================================================


def test_random_append_only_histories_always_prove_consistent():
    rng = random.Random(20260825)
    for _ in range(40):
        n = rng.randint(1, 60)
        log = TransparencyLog("sweep", signing_key=LOG_KEY)
        heads = []
        for i in range(n):
            log.append(
                kind=ENTRY_RECEIPT, body={"i": i, "nonce": rng.randint(0, 1 << 30)}, timestamp=T0 + i
            )
            if rng.random() < 0.3:
                heads.append(log.signed_tree_head(timestamp=T0 + i))
        assert log.root() == reference_mth(list(log.leaves))
        for head in heads:
            assert log.prove_extends(head).verify()
        for _ in range(5):
            seq = rng.randrange(n)
            assert log.inclusion_proof(seq).verify()


def test_random_single_leaf_edits_always_break_consistency():
    rng = random.Random(6962)
    for _ in range(40):
        n = rng.randint(2, 40)
        leaves = leaves_of(n)
        published_size = rng.randint(1, n)
        published_root = merkle_tree_hash(leaves[:published_size])

        edited = list(leaves)
        victim = rng.randrange(published_size)
        edited[victim] = hash_leaf(f"edited-{victim}-{rng.random()}".encode())
        grown = edited + leaves_of(rng.randint(0, 5))

        assert not verify_consistency(
            first_size=published_size,
            first_root=published_root,
            second_size=len(grown),
            second_root=merkle_tree_hash(grown),
            path=consistency_path(published_size, grown),
        )


# ======================================================================================
# Serialisation of the carried types
# ======================================================================================


def test_log_entry_serialises_and_recomputes_its_own_leaf():
    log = build_log(2)
    entry = log.get(1)
    d = entry.to_dict()
    assert d["seq"] == 1
    assert d["kind"] == ENTRY_RECEIPT
    assert d["leaf_hash"] == hash_leaf(entry.leaf_data())


def test_observation_and_findings_serialise():
    log = build_log(3)
    auditor = LogAuditor("witness_a")
    obs = auditor.observe("issuer", log.signed_tree_head(timestamp=T0))
    auditor.observe("cardholder", log.signed_tree_head(timestamp=T0))
    assert obs.to_dict()["observer"] == "issuer"
    assert obs.to_dict()["sth"]["tree_size"] == 3

    report = auditor.audit(key=LOG_KEY)
    d = report.to_dict()
    assert d["ok"] is True
    assert d["auditor_id"] == "witness_a"
    assert d["findings"][0]["observers"] == ["cardholder", "issuer"]
    assert len(d["observations"]) == 2


def test_a_log_may_be_given_a_key_per_publication():
    log = TransparencyLog("late-key")
    log.append(kind=ENTRY_RECEIPT, body={"a": 1}, timestamp=T0)
    sth = log.signed_tree_head(timestamp=T0, key=LOG_KEY)
    assert verify_tree_head(sth, LOG_KEY)
    assert sth.signing_key_id == key_id(LOG_KEY)


def test_entry_kinds_are_a_closed_vocabulary():
    log = TransparencyLog("l")
    for kind in (ENTRY_RECEIPT, ENTRY_UNATTESTED_SELECTION):
        assert log.append(kind=kind, body={}, timestamp=T0).kind == kind
    assert len(log.filter(ENTRY_UNATTESTED_SELECTION)) == 1
