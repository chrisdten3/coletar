from datetime import UTC, datetime, timedelta

from coletar.compiler import WEIGHTS, Fidelity, ManifestEntry, MigrationManifest, score


def _manifest(*fidelities: Fidelity, scope_preserved: bool = True) -> MigrationManifest:
    manifest = MigrationManifest(destination="claude")
    for i, fidelity in enumerate(fidelities):
        manifest.add(
            ManifestEntry(
                source_id=f"mem_{i}",
                source_type="memory",
                fidelity=fidelity,
                scope_preserved=scope_preserved,
            )
        )
    return manifest


def test_weights_are_published_and_sum_to_one():
    """§7: if the weighting isn't inspectable, the score is a badge, not a metric."""
    assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9


def test_perfect_fresh_migration_scores_one():
    manifest = _manifest(Fidelity.NATIVE, Fidelity.NATIVE)
    assert score(manifest, source_object_count=2).total == 1.0


def test_unsupported_objects_cost_coverage_and_fidelity():
    result = score(_manifest(Fidelity.NATIVE, Fidelity.UNSUPPORTED), source_object_count=2)
    assert result.object_coverage == 0.5
    assert result.fidelity == 0.5


def test_objects_never_reaching_the_manifest_still_count_against_coverage():
    """Asked to move 4, only 2 arrived — the denominator is the ask, not the result."""
    result = score(_manifest(Fidelity.NATIVE, Fidelity.NATIVE), source_object_count=4)
    assert result.object_coverage == 0.5


def test_reconstruction_costs_fidelity_but_not_coverage():
    result = score(_manifest(Fidelity.NATIVE, Fidelity.RECONSTRUCTED), source_object_count=2)
    assert result.object_coverage == 1.0
    assert result.fidelity == 0.5


def test_flattening_project_scope_to_global_is_penalised():
    result = score(_manifest(Fidelity.NATIVE, scope_preserved=False), source_object_count=1)
    assert result.scope_preservation == 0.0


def test_staleness_decays_after_a_day():
    manifest = _manifest(Fidelity.NATIVE)
    manifest.compiled_at = datetime.now(UTC) - timedelta(days=16)
    result = score(manifest, source_object_count=1)
    assert 0.4 < result.staleness < 0.6
    assert result.total < 1.0


def test_empty_source_scores_zero_rather_than_dividing_by_zero():
    assert score(_manifest(), source_object_count=0).total == 0.0


def test_explain_shows_the_arithmetic():
    text = score(_manifest(Fidelity.NATIVE), source_object_count=1).explain()
    assert "object_coverage" in text and "total" in text
