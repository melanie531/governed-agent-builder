"""Temporary Cognito role-switcher identity for the self-approval rollout smoke.

Creates (or with --delete removes) a synthetic Studio user that belongs to
studio-admin, one business group and studio-role-switcher, which is the only
identity shape that can file its own capability request (business-active) and
then self-approve it (admin-active). The password is generated locally and
written only to a chmod-600 credential file; it is never printed, logged or
passed on a command line. Delete the identity with --delete as soon as the
smoke has run.

Portable inputs (no absolute paths, account literals or pool ids in this file):
  --expected-account  12-digit target account; verified against STS first
  --profile           operator-approved AWS SDK profile
  --region            target region
  --user-pool-id      Cognito user pool of the deployed application
  --email             synthetic e-mail/username for the identity
  --cred-out          path for the chmod-600 SMOKE_USER/SMOKE_PW file
  --business-group    business group for the switcher (default studio-research)
  --delete            remove the identity and the credential file
"""
import argparse
import os
import secrets
import string
from pathlib import Path

import boto3

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--expected-account", required=True)
parser.add_argument("--profile", required=True)
parser.add_argument("--region", required=True)
parser.add_argument("--user-pool-id", required=True)
parser.add_argument("--email", required=True)
parser.add_argument("--cred-out", required=True, type=Path)
parser.add_argument("--business-group", default="studio-research")
parser.add_argument("--delete", action="store_true")
args = parser.parse_args()

session = boto3.Session(profile_name=args.profile, region_name=args.region)
if session.client("sts").get_caller_identity()["Account"] != args.expected_account:
    raise RuntimeError("STS account mismatch; refusing to touch the user pool")
idp = session.client("cognito-idp")
groups = ["studio-admin", args.business_group, "studio-role-switcher"]

if args.delete:
    idp.admin_delete_user(UserPoolId=args.user_pool_id, Username=args.email)
    if args.cred_out.exists():
        args.cred_out.unlink()
    print("deleted synthetic switcher identity and credential file")
else:
    alphabet = string.ascii_letters + string.digits
    pw = (secrets.choice(string.ascii_uppercase) + secrets.choice(string.ascii_lowercase)
          + secrets.choice(string.digits) + "!" + "".join(secrets.choice(alphabet) for _ in range(18)))
    idp.admin_create_user(UserPoolId=args.user_pool_id, Username=args.email, MessageAction="SUPPRESS",
                          UserAttributes=[{"Name": "email", "Value": args.email},
                                          {"Name": "email_verified", "Value": "true"}])
    generated = pw
    idp.admin_set_user_password(UserPoolId=args.user_pool_id, Username=args.email,
                                Password=generated, Permanent=True)
    for group in groups:
        idp.admin_add_user_to_group(UserPoolId=args.user_pool_id, Username=args.email, GroupName=group)
    # Exclusive 0600 creation; never widen or follow an existing file/symlink.
    fd = os.open(args.cred_out, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(f"SMOKE_USER={args.email}\nSMOKE_PW={pw}\n")
    print("created synthetic switcher identity in groups: " + ",".join(groups))
