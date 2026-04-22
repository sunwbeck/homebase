"""Local self-test helpers for the homebase client workflow."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import tempfile

from homebase_cli.client import (
    PairRequest,
    discovery_payload,
    load_client_state,
    pair_controller,
    parse_discovery_payload,
    parse_profile_payload,
    profile_payload,
    refresh_pair_code,
)


@dataclass(frozen=True)
class SelfTestResult:
    """Summary of one local client self-test run."""

    node_id: str
    hostname: str
    platform: str
    version: str
    paired_services: tuple[str, ...]
    paired_ports: tuple[int, ...]


def run_client_self_test() -> SelfTestResult:
    """Exercise local discovery and pairing logic without requiring another node."""
    with tempfile.TemporaryDirectory(prefix="homebase-selftest-") as tmp_dir:
        temp_state_path = Path(tmp_dir) / "client-state.json"
        original_state_path = os.environ.get("HOMEBASE_CLIENT_STATE_PATH")
        os.environ["HOMEBASE_CLIENT_STATE_PATH"] = str(temp_state_path)
        try:
            state = refresh_pair_code()
            discovery = parse_discovery_payload(discovery_payload())
            paired = pair_controller(PairRequest(controller_id="selftest-controller", code=state.pair_code))
            if not paired:
                raise RuntimeError("pairing request failed during self-test")
            updated = load_client_state()
            if "selftest-controller" not in updated.paired_controllers:
                raise RuntimeError("paired controller was not persisted during self-test")
            profile = parse_profile_payload(profile_payload())
            return SelfTestResult(
                node_id=profile.node_id,
                hostname=profile.hostname,
                platform=profile.platform,
                version=profile.version,
                paired_services=profile.services,
                paired_ports=profile.open_ports,
            )
        finally:
            if original_state_path is None:
                os.environ.pop("HOMEBASE_CLIENT_STATE_PATH", None)
            else:
                os.environ["HOMEBASE_CLIENT_STATE_PATH"] = original_state_path
