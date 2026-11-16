"""Explicit compatibility boundary for persisted worker request snapshots."""
from pwanimate.services.agent_runs import RunError

CURRENT_SNAPSHOT_VERSION = 2


class SnapshotCompatibilityError(RunError):
    pass


def decode_snapshot(snapshot, *, supported_version=CURRENT_SNAPSHOT_VERSION,
                    require_execution_boundary=False):
    from pwanimate.services.agent_runs import SNAPSHOT_FIELDS
    if not isinstance(snapshot, dict):
        raise SnapshotCompatibilityError('Invalid task snapshot.')
    version = snapshot.get('snapshot_version', 1)
    if type(version) is not int or version not in range(1, supported_version + 1):
        raise SnapshotCompatibilityError('This worker cannot execute this task snapshot version.')
    if set(snapshot) - set(SNAPSHOT_FIELDS):
        raise SnapshotCompatibilityError('This task contains unsupported snapshot fields.')
    if not isinstance(snapshot.get('query'), str) or not snapshot['query'].strip():
        raise SnapshotCompatibilityError('This task has no valid saved request.')
    boundary = snapshot.get('execution_boundary_id')
    if boundary is not None and (type(boundary) is not int or boundary <= 0):
        raise SnapshotCompatibilityError('Invalid prospective execution receipt.')
    if require_execution_boundary and boundary is None:
        raise SnapshotCompatibilityError('This academic execution requires a prospective receipt.')
    return {**snapshot, 'snapshot_version':version}
