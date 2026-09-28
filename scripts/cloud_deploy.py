"""Retired legacy-account entry point. No AWS sessions or operations."""

def main(*args, **kwargs):
    raise RuntimeError(
        "Legacy cloud entry point disabled. Use scripts/serverless_deploy.py with "
        "--expected-account, --profile, --region and a fresh --state path; "
        "see docs/deployment.md. Do not reuse legacy state/resources."
    )


if __name__ == "__main__":
    main()
