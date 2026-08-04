# Security Policy

## Supported versions

Security fixes are applied to the latest commit on `main`.

## Reporting a vulnerability

Please **do not** open a public issue for security problems.

Use GitHub's [private vulnerability reporting](https://github.com/ehsanhajian/ValidatorPulse/security/advisories/new) for this repository, or email the maintainer listed on the GitHub profile.

Include:

- A clear description of the issue
- Steps to reproduce
- Impact assessment (data exposure, RCE, auth bypass, etc.)
- Any suggested fix, if you have one

You should receive an acknowledgment within a few days. Please give us a reasonable window to patch before any public disclosure.

## Scope notes

ValidatorPulse monitors validator / node health. It is **not** a security scanner for external attack surfaces. Reports about missing external pentest coverage are out of scope unless they affect this application's own code, dependencies, or deployment defaults.
