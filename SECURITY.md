# Security policy

## Supported versions

Vigil is alpha software. Only the `main` branch receives security fixes.
A tagged release receives a fix when the defect affects a supported deployment.

## Report a vulnerability

Do not open a public issue, a public pull request, or a public discussion for a security defect.

Use one of these private channels:

1. GitHub private vulnerability reporting: open the **Security** tab, then **Report a vulnerability**.
2. Email: nasirdaud2015@gmail.com. Put `Vigil security` in the subject line.

Include:

- The affected commit or version.
- The steps to reproduce the defect.
- The impact that you expect.
- A proof of concept, if you have one.

We acknowledge a report within 72 hours. We send an assessment within 7 days.
We credit the reporter in the advisory unless the reporter asks for privacy.

## Scope

These classes are in scope:

| Class | Example |
| --- | --- |
| Authentication bypass | A request reaches a protected route without a valid device token. |
| Authorization defect | A device reads a card, a run, or a memory chunk of another device. |
| Sandbox escape | A tool call reaches the host, the Docker socket, or another run workspace. |
| Secret exposure | A connector credential, an API key, or a pairing token appears in a log, an error, or a response. |
| SQL injection | A parameter reaches a query as text. |
| Server-side request forgery | A connector URL reaches an internal address. |
| Approval bypass | An external side effect runs without an approval row. |

## Out of scope

- A finding on a deployment that ignores the documented configuration, such as a published `SECRET_KEY`.
- Denial of service from a local operator account.
- A defect in a third-party image or package without a path through Vigil code. Report that upstream.

## Safe harbor

We will not pursue a legal claim against a researcher who follows this policy, stays inside a deployment that the researcher owns, and does not access data of another person.
