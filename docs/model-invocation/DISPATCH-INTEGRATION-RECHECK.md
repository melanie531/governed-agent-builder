# Dispatch subpatch integrated on deployed-Haiku compatible adapter

12bc76e was based on cbd0bab, not efb5292. Applied its endpoint dispatch correction on top of efb5292, resolved Model.check conflict preserving the explicit existing Haiku transport/responseModels branch, and used exact_endpoint for that branch as well. Packaging still includes opus_messages.py. No branch replacement, cloud writes, permissions or inference.

Same-tree targeted rerun: tests/test_opus_transport_dispatch.py tests/test_opus_package.py tests/test_opus_modelclient.py tests/test_opus_request_contract.py tests/test_foundation_executor.py tests/test_foundation_approval.py ->72 passed,2 existing warnings,2.40s. This count supersedes worker-local70/285 for this integrated tree; those original logs are historical only. New dispatch tests stub send to verify reaching the transport seam; they are not successful network/Gateway calls.

Current configuration candidate is the256-token,disabled-thinking,system-schema candidate corrected at a9159da; never apply obsolete16-token OPUS-FIRST-REVIEW instructions. Backend registration/admission/Studio driver remains separately owned. Rejected602017de is not integrated. Real response identity/authorizations/driver activation still require review and live acceptance.
