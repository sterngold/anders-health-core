# Security policy

## Supported version

Security fixes are applied to the latest release on the default branch.

## Reporting a vulnerability

Use GitHub's private vulnerability reporting for this repository. Do not open a
public issue containing a vulnerability, credential, health record, personal
identifier, or private infrastructure detail.

Include the affected version, minimal reproduction, impact, and suggested
mitigation when known. Use synthetic data only.

## Secret handling

The repository must not contain credentials. CI runs both the project privacy
gate and Gitleaks across repository history. If a credential is ever committed,
rotate it first; removing it from the latest commit is not sufficient.
