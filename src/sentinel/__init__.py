"""SENTINEL's backend-independent drone safety contracts."""

from .client import DroneClient, EmergencyAborted
from .dry_run import DryRunClient, EmergencyLatchedError
from .envelope import EnvelopeViolation, FlightEnvelope
from .models import (
    AdapterInfo,
    CommandRecord,
    Direction,
    Position,
    RotationDirection,
    RunReport,
    Telemetry,
    VideoFrame,
)

# Expose the modules used by the CLI and emergency fallback.  Besides making
# the package pleasant to introspect, this keeps dotted-path dependency
# injection (`sentinel.cli.*`, `sentinel.emergency.*`) reliable.
from . import cli as cli
from . import emergency as emergency

__version__ = "0.1.0"

__all__ = [
    "CommandRecord",
    "AdapterInfo",
    "Direction",
    "DroneClient",
    "DryRunClient",
    "EmergencyAborted",
    "EmergencyLatchedError",
    "EnvelopeViolation",
    "FlightEnvelope",
    "Position",
    "RotationDirection",
    "RunReport",
    "Telemetry",
    "VideoFrame",
    "__version__",
]
