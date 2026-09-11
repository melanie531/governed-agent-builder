"""Controlled synthetic driver authority, NOT Studio authentication.

Constructed only by tests/probe driver with a backend-approved synthetic record.
No HTTP route, environment switch or Runtime payload enables this authority.
Production must implement scoped redemption over supported authenticated context.
"""
import threading
import time
from foundation_harness.context import Denied


class ControlledAuthority:
    def __init__(self, binding, grants):
        self.binding, self.grants = binding, grants
        self.claimed = self.finished = False
        self.lock = threading.Lock()

    def redeem(self, run_ref, authenticated_entry):
        with self.lock:
            if (authenticated_entry != self.binding or run_ref != self.binding.run_ref
                    or self.binding.expires_at <= time.time()):
                raise Denied('CONTROLLED_BINDING_DENIED')
            if self.claimed:
                raise Denied('RUN_REPLAY_DENIED')
            self.claimed = True
            return self.binding

    def authorize(self, binding, operation, resource):
        with self.lock:
            if (binding != self.binding or not self.claimed or self.finished
                    or binding.expires_at <= time.time() or (operation, resource) not in self.grants):
                raise Denied('CURRENT_GRANT_DENIED')

    def finish(self, binding):
        with self.lock:
            if binding != self.binding:
                raise Denied('CONTROLLED_BINDING_DENIED')
            self.finished = True
