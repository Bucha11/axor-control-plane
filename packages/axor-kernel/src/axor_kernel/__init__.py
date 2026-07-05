"""axor-kernel: the pure governance layer.

Ecosystem placement: this is STAGING for `axor_core.kernel`. The modules here
(events schema, desired-state lattice, degradation recompute, replay) are new
code destined for axor-core as its pure submodule; once merged, this package
is deleted and platform imports switch axor_kernel -> axor_core.kernel.
There is no separate kernel distribution: rule 0 needs shared code, not a
shared package. Purity is enforced by module discipline + a contract test
(no I/O imports), not by packaging.

Structural rule 0 (architecture doc): this package is imported by BOTH the
runtime adapter (enforcement) and the backend replay engine. It therefore
contains zero I/O, zero framework imports, and every public function is a
deterministic function of its inputs. If you are about to add an import of
httpx / fastapi / kuzu / anything with a socket — stop, it belongs elsewhere.
"""

from axor_kernel.degradation import Level, compute_level
from axor_kernel.events import SCHEMA_VERSION, Event, EventKind, Fact
from axor_kernel.replay import ReplayResult, replay
from axor_kernel.state import DesiredState, GovernanceState

__all__ = [
    "SCHEMA_VERSION", "Event", "EventKind", "Fact",
    "DesiredState", "GovernanceState",
    "Level", "compute_level",
    "ReplayResult", "replay",
]
