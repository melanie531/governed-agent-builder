"""Offline source preview only; these local files confer NO runtime authority.

python -m backend.domain_harness_cli --domain ... --foundations ...
    --catalogs ... --authorization ...
No API route is installed until trusted production admission is wired.
"""
import argparse
import json
from pathlib import Path
from pydantic import ValidationError
from .domain_harness import AdmissionDenied, compile_plan
from .domain_harness_schema import (
    DomainHarnessDefinition, FoundationLibrary, Catalogs, AuthorizationContext,
)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('domain', 'foundations', 'catalogs', 'authorization'):
        parser.add_argument('--' + name, required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        plan = compile_plan(DomainHarnessDefinition.model_validate_json(args.domain.read_text()),
                            foundations=FoundationLibrary.model_validate_json(args.foundations.read_text()),
                            catalogs=Catalogs.model_validate_json(args.catalogs.read_text()),
                            authorization=AuthorizationContext.model_validate_json(args.authorization.read_text()))
    except (ValidationError, AdmissionDenied, OSError):
        print(json.dumps({'error': 'ADMISSION_DENIED', 'execution_ready': False}))
        return 2
    print(json.dumps(plan.preview(), sort_keys=True))
    return 0  # successful compilation, explicitly NOT successful execution


if __name__ == '__main__':
    raise SystemExit(main())
