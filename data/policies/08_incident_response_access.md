---
policy_id: EPG-INCIDENT-008
title: Incident Response Access Policy
domain: incident-response
---
# Incident Response Access Policy

## Purpose and activation

This fictional policy defines temporary access used to investigate, contain, eradicate, and recover from a declared security or service incident. It applies to incident commanders, responders, system owners, legal and privacy advisers, and approved external specialists.

An incident record must exist before emergency access is issued unless creating the record would delay urgent containment. In that case, the record must be created as soon as the coordination service is available. The incident commander identifies the affected systems, authorized responders, allowed actions, evidence needs, and access expiration.

## Access controls

Responder access must use named identities, managed devices, multi-factor authentication, and the privileged access gateway. Permissions must be limited to the systems and time window needed for the assigned incident role. Break-glass credentials may be used only when normal identity services are unavailable or too slow to prevent material harm. Their use must trigger an alert and credential rotation.

Session activity, evidence collection, commands, permission changes, and data exports must be logged. Responders must preserve chain-of-custody details for collected evidence. Customer, employee, finance, or source-code data should be minimized and stored only in the restricted incident workspace. Sharing with a vendor requires an approved incident role and applicable confidentiality terms.

## Boundaries and review

Incident declaration is not blanket authorization. Bulk data export, employee monitoring beyond the incident scope, disabling audit controls, or accessing unrelated accounts requires separate owner, Privacy, Legal, or Security approval as applicable. Production fixes must follow the emergency path in the Software Release and Production Deployment Policy.

Temporary access expires at incident closure or the stated end time, whichever comes first. The incident commander must ensure accounts, tokens, firewall rules, and shared workspaces are removed or returned to standard permissions. Within one business day, the system owner reviews emergency access and documents any unsupported action. Lessons learned must identify excessive standing privilege and missing telemetry.
