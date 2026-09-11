"""Only a trusted backend adapter may supply Binding; it is not a credential."""
from dataclasses import dataclass
from typing import Protocol


class Denied(RuntimeError):
    pass


@dataclass(frozen=True)
class Binding:
    run_ref: str
    owner: str
    workspace: str
    workload: str
    runtime: str
    runtime_version: str
    runtime_session: str
    manifest_digest: str
    foundation_digest: str
    epoch: int
    expires_at: float


class Authority(Protocol):
    def redeem(self, run_ref: str, authenticated_entry: Binding) -> Binding: ...
    def authorize(self, binding: Binding, operation: str, resource: str) -> None: ...
    def finish(self, binding: Binding) -> None: ...


class MissingAuthority:
    def redeem(self, run_ref, authenticated_entry):
        raise Denied('AUTHENTICATED_BACKEND_REDEMPTION_NOT_CONNECTED')

    def authorize(self, binding, operation, resource):
        raise Denied('AUTHENTICATED_BACKEND_REDEMPTION_NOT_CONNECTED')

    def finish(self, binding):
        raise Denied('AUTHENTICATED_BACKEND_REDEMPTION_NOT_CONNECTED')


@dataclass(frozen=True)
class ResolvedEntry:
    """Server-redeemed input and persistent budget handle, never payload fields."""
    binding: Binding
    stored_input: str
    limits: object
    reservation: object
