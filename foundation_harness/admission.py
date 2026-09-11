"""All dispatches recheck the authoritative binding, including continuations."""
import time
from .context import Denied
from .config import digest


def admit(authority, run_ref, entry, config):
    binding = authority.redeem(run_ref, entry)
    if (binding.manifest_digest != digest(config.model_dump(mode='json'))
            or binding.foundation_digest != config.foundation.digest
            or binding.expires_at <= time.time()):
        raise Denied('IMMUTABLE_ENTRY_BINDING_DENIED')
    return binding


def recheck(authority, binding, operation, resource):
    if binding.expires_at <= time.time():
        raise Denied('AUTHORITY_EXPIRED')
    authority.authorize(binding, operation, resource)
