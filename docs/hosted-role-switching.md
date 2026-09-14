# Hosted role switching

The account menu can switch between **Business User** and **Platform Admin**
when an operator has assigned both roles in Cognito and explicitly enrolled the
account in `studio-role-switcher`. Ordinary users see only Sign out.

Enrollment requires one business group (`studio-research` or
`studio-operations`), `studio-admin`, and `studio-role-switcher`. Assign these
groups only to an explicitly approved account. There is no self-enrollment API
or email-based administrator shortcut. Sign in again after a group change so
the application receives fresh Cognito claims.

`POST /api/auth/role` accepts an assigned `group_id`. The backend validates the
signed token, current session and CSRF token, then persists `active_group` in
that session and writes a `role_switched` audit event. Each subsequent request
checks that the selected group remains in its signed claims.

The canonical business membership, grants, agent ownership and job authority
remain unchanged when selecting the admin view. Another browser session keeps
its own selected role. Switching back restores access to the same business
agents; it does not restore revoked capabilities. The frontend reloads after
switching so state from the previous console does not carry over.

Existing access tokens expire normally; Cognito group changes become visible
with new tokens. Emergency revocation must also revoke the affected hosted
sessions. The demo switch is not a substitute for separate production approvers:
the original subject is retained, including the existing prohibition on
approving one's own capability-access request.

Validation covers signed-token authorization on SQLite and DynamoDB, CSRF,
unassigned roles, ambiguous assignments, session isolation, revocation, grant
preservation and menu navigation. Temporary QA accounts remain restricted to
their original enrolled role.
